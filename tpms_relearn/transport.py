import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path

from .protocol import ProtocolError, frames, parse_frames, reassemble


class Transcript:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, "x", encoding="utf-8")

    def record(self, **data):
        self.file.write(json.dumps({"time": time.time(), **data}) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def select_characteristics(services, write_uuid=None, notify_uuid=None):
    chars = [c for s in services for c in s.characteristics]
    writes = [c for c in chars if set(c.properties) & {"write", "write-without-response"}]
    notifies = [c for c in chars if set(c.properties) & {"notify", "indicate"}]
    if write_uuid:
        writes = [c for c in writes if c.uuid.lower() == write_uuid.lower()]
    if notify_uuid:
        notifies = [c for c in notifies if c.uuid.lower() == notify_uuid.lower()]
    # Prefer the common ELM FFF0 UART service, but use discovered properties.
    if not write_uuid and not notify_uuid:
        uart = [c for c in chars if c.service_uuid.lower() == "0000fff0-0000-1000-8000-00805f9b34fb"]
        if uart:
            writes = [c for c in writes if c in uart]
            notifies = [c for c in notifies if c in uart]
    if len(writes) != 1 or len(notifies) != 1:
        raise ProtocolError("BLE UART is ambiguous. Run inspect and specify --write-char and --notify-char UUIDs.")
    return writes[0], notifies[0]


async def ble_elm(client, write_char, notify_char, transcript):
    """Bluetooth LE adapters: write to one GATT characteristic, replies arrive as notifications."""
    async def send(data):
        # 20-byte chunks also work with adapters advertising small MTUs.
        for start in range(0, len(data), 20):
            await client.write_gatt_char(write_char, data[start:start + 20], response="write" in write_char.properties)

    elm = Elm(send, transcript)
    await client.start_notify(notify_char, lambda _, data: elm.receive(data))
    return elm


@contextlib.asynccontextmanager
async def serial_elm(port, transcript, baudrate=38400):
    """Classic Bluetooth (paired, shows up as a serial port) and USB adapters.

    `port` is a port name such as COM5 or /dev/cu.OBDII, or an open serial-like object.
    """
    if isinstance(port, str):
        import serial
        connection = serial.Serial(port, baudrate, timeout=0.1)
    else:
        connection = port
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    elm = Elm(lambda data: asyncio.to_thread(connection.write, data), transcript)

    def reader():
        # A lost connection ends the reader; the pending command then times out.
        with contextlib.suppress(OSError):
            while not stop.is_set():
                data = connection.read(connection.in_waiting or 1)
                if data:
                    loop.call_soon_threadsafe(elm.receive, data)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        yield elm
    finally:
        stop.set()
        thread.join(1)
        connection.close()


class Elm:
    """ELM327 command channel over any byte transport: `send` writes, `receive` feeds replies."""
    def __init__(self, send, transcript):
        self.send = send
        self.transcript = transcript
        self.queue = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.broken = False

    def receive(self, data):
        self.queue.put_nowait(bytes(data))

    async def command(self, command, timeout=8):
        async with self.lock:
            if self.broken:
                raise ProtocolError("Transport is desynchronized; reconnect before another command")
            while not self.queue.empty():
                self.transcript.record(discarded=self.queue.get_nowait().hex())
            self.transcript.record(tx=command)
            response = bytearray()
            try:
                async with asyncio.timeout(timeout):
                    await self.send((command + "\r").encode("ascii"))
                    while b">" not in response:
                        response.extend(await self.queue.get())
                        if len(response) > 65536:
                            raise ProtocolError("ELM response exceeds limit")
            except BaseException:
                self.broken = True
                self.transcript.record(incomplete_rx=response.decode("ascii", errors="replace"))
                raise
            text = response.decode("ascii", errors="strict").split(">", 1)[0]
            self.transcript.record(rx=text)
            # Remove exact command echo, including the reset command before ATE0.
            lines = [s.strip() for s in text.replace("\r", "\n").splitlines() if s.strip()]
            lines = [s for s in lines if s.replace(" ", "").upper() != command.replace(" ", "").upper()]
            return "\n".join(lines)

    async def at(self, command):
        result = await self.command(command)
        if result != "OK":
            raise ProtocolError(f"Adapter does not support required command {command}: {result}")

    async def initialize(self):
        # ATAL: ELM327 default (NL) rejects 8 raw data bytes with "?".
        identity = await self.command("ATZ")
        for command in ("ATE0", "ATL0", "ATS1", "ATH1", "ATD0", "ATSP6", "ATAL", "ATCAF0", "ATCFC0", "ATSH750", "ATCRA758", "ATST32", "ATAT0", "ATR1"):
            await self.at(command)
        return identity


class ToyotaLink:
    """Raw extended-address ISO-TP with explicit flow control in both directions."""
    def __init__(self, elm):
        self.elm = elm

    async def request(self, payload):
        packets = [packet.ljust(8, b"\0") for packet in frames(payload)]
        reply = await self.elm.command(packets[0].hex().upper())
        if len(packets) > 1:
            parts = parse_frames(reply)
            block, delay = self.flow_control(parts)
            sent_in_block = 0
            receiving = True
            for i, packet in enumerate(packets[1:], 1):
                sent_in_block += 1
                last = i == len(packets) - 1
                need_reply = last or (block != 0 and sent_in_block == block)
                if need_reply != receiving:
                    await self.elm.at("ATR1" if need_reply else "ATR0")
                    receiving = need_reply
                await asyncio.sleep(delay)
                reply = await self.elm.command(packet.hex().upper())
                if need_reply and not last:
                    block, delay = self.flow_control(parse_frames(reply))
                    sent_in_block = 0
                elif not need_reply and reply not in ("", "OK", "NO DATA"):
                    raise ProtocolError(f"Unexpected response during segmented write: {reply}")
        parts = parse_frames(reply)
        # With CAF0 and CFC0 the ECU's first frame arrives alone. Respond using
        # the Toyota extended address before trying to decode the full payload.
        first_frames = [p for p in parts if p[0] >> 4 == 1]
        if first_frames:
            if len(first_frames) != 1 or parts[-1] != first_frames[0]:
                raise ProtocolError("Unexpected first-frame ordering")
            more = await self.elm.command("2A30000000000000")
            parts.extend(parse_frames(more))
        messages = reassemble(parts)
        # Never resend an operation after NRC 78: it may already be executing.
        final = [m for m in messages if m != bytes([0x7F, payload[0], 0x78])]
        if len(final) != 1:
            raise ProtocolError("No unique final ECU response (timeout/pending). Operation outcome is unknown; do not retry a write automatically.")
        return final[0]

    @staticmethod
    def flow_control(parts):
        if len(parts) != 1 or len(parts[0]) < 3 or parts[0][0] != 0x30:
            raise ProtocolError("Expected ISO-TP clear-to-send flow control; write stopped")
        _, block, stmin = parts[0][:3]
        if stmin <= 0x7F:
            delay = stmin / 1000
        elif 0xF1 <= stmin <= 0xF9:
            delay = (stmin - 0xF0) / 10000
        else:
            raise ProtocolError("Reserved ISO-TP separation time")
        return block, delay
