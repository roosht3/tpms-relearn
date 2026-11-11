import asyncio
import json
import queue
import tempfile
import unittest
from pathlib import Path

from tpms_relearn.protocol import (NegativeResponse, ProtocolError, frames, parse_frames,
                               positive, reassemble, sensor_id, validate_ids, write_payload)
from tpms_relearn.service import read_ids, register, save_backup
from tpms_relearn.transport import ToyotaLink, ble_elm, serial_elm

OLD = ["01234567", "02345678", "03456789", "0456789A"]
NEW = ["01111111", "02222222", "03333333", "04444444"]


class CodecTests(unittest.TestCase):
    def test_sensor_ids(self):
        self.assertEqual(sensor_id("abcdef0"), "0ABCDEF0")
        for invalid in ("123", "11234567", "0x1234567", " 1234567", "0G123456"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                sensor_id(invalid)

    def test_invalid_sets(self):
        for ids in (OLD[:3], OLD + OLD, OLD[:3] + OLD[:1], OLD[:3] + ["0000000"], OLD[:3] + ["FFFFFFF"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_ids(ids)

    def test_golden_uds_payload(self):
        self.assertEqual(write_payload("uds", OLD).hex(), "2e2003040123456702345678034567890456789a00000000")

    def test_golden_can_payload(self):
        self.assertEqual(write_payload("can", OLD).hex(), "3b0e000400000123456700000234567800000345678900000456789a00000fffffff")

    def test_five_sensor_payload(self):
        ids = OLD + ["056789AB"]
        self.assertEqual(len(write_payload("uds", ids)), 24)
        self.assertEqual(len(write_payload("can", ids)), 34)
        self.assertEqual(write_payload("uds", ids)[-4:], bytes.fromhex(ids[-1]))

    def test_transport_round_trip(self):
        for size in (1, 6, 7, 24, 34, 110, 200):
            payload = bytes(i % 256 for i in range(size))
            raw = "\r".join("758 " + p.ljust(8, b"\0").hex(" ") for p in frames(payload))
            self.assertEqual(reassemble(parse_frames(raw)), [payload])

    def test_truncation_and_sequence(self):
        parts = [p[1:] for p in frames(bytes(range(24)))]
        for broken in (parts[:-1], [parts[0], parts[2], parts[1]], [b"\x07" + bytes(7)], [b"\x10\x03" + bytes(5)]):
            with self.assertRaises(ProtocolError):
                reassemble(broken)

    def test_wrong_sender_and_elm_errors(self):
        for text in ("BUS ERROR", "?", "757 2A 02 61 06", "758 F1 02 61 06", "758 2A 03 61 06 GG"):
            with self.assertRaises(ProtocolError):
                parse_frames(text)

    def test_negative_response(self):
        with self.assertRaises(NegativeResponse) as ctx:
            positive(bytes.fromhex("7F2231"), bytes.fromhex("222002"))
        self.assertEqual(ctx.exception.code, 0x31)
        with self.assertRaises(ProtocolError):
            positive(bytes.fromhex("62102401234567"), bytes.fromhex("221023"))


class FakeLink:
    def __init__(self, protocol="uds", fail_write=False):
        self.protocol, self.fail_write = protocol, fail_write
        self.ids, self.requests = list(OLD), []
        self.backup = None

    async def request(self, request):
        self.requests.append(request)
        if request in (bytes.fromhex("222002"), bytes.fromhex("2106")):
            return bytes([request[0] + 0x40]) + request[1:] + (b"\x04" if self.protocol == "uds" else b"\x80")
        if request[0] in (0x21, 0x22):
            i = int.from_bytes(request[1:], "big") - (0x1023 if self.protocol == "uds" else 0x0F)
            return bytes([request[0] + 0x40]) + request[1:] + bytes.fromhex(self.ids[i])
        if request[0] == 0x10:
            return bytes([0x50, 0x01 if self.protocol == "can" else request[1]])
        if request[0] in (0x2E, 0x3B):
            assert self.backup and self.backup.exists(), "Write before durable backup"
            if self.fail_write:
                raise ProtocolError("Connection lost")
            self.ids = list(NEW)
            return bytes.fromhex("6E2003" if self.protocol == "uds" else "7B0E")
        raise AssertionError(request.hex())


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_profiles(self):
        for protocol in ("can", "uds"):
            self.assertEqual((await read_ids(FakeLink(protocol), protocol))["ids"], OLD)

    async def test_backup_and_verified_write(self):
        for protocol in ("can", "uds"):
            with tempfile.TemporaryDirectory() as directory:
                link = FakeLink(protocol)
                link.backup = Path(directory) / "backup.json"
                result = await register(link, protocol, NEW, OLD, link.backup)
                self.assertEqual(result["status"], "verified")
                self.assertEqual(json.loads(link.backup.read_text())["ids"], OLD)

    async def test_stale_expectation_never_starts_session(self):
        link = FakeLink()
        with self.assertRaises(ProtocolError):
            await register(link, "uds", NEW, NEW, "unused.json")
        self.assertTrue(all(r[0] == 0x22 for r in link.requests))

    async def test_lost_write_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            link = FakeLink(fail_write=True)
            link.backup = Path(directory) / "backup.json"
            with self.assertRaisesRegex(ProtocolError, "could not be verified"):
                await register(link, "uds", NEW, OLD, link.backup)
            self.assertEqual(sum(r[0] == 0x2E for r in link.requests), 1)

    async def test_existing_backup_prevents_write(self):
        with tempfile.TemporaryDirectory() as directory:
            link = FakeLink()
            path = Path(directory) / "backup.json"
            path.write_text("keep")
            with self.assertRaises(FileExistsError):
                await register(link, "uds", NEW, OLD, path)
            self.assertEqual(path.read_text(), "keep")
            self.assertTrue(all(r[0] == 0x22 for r in link.requests))

    async def test_readback_mismatch_is_not_success(self):
        class MismatchLink(FakeLink):
            async def request(self, request):
                reply = await super().request(request)
                if request[0] == 0x2E:
                    self.ids = list(OLD)
                return reply

        with tempfile.TemporaryDirectory() as directory:
            link = MismatchLink()
            link.backup = Path(directory) / "backup.json"
            with self.assertRaisesRegex(ProtocolError, "Read-back differs"):
                await register(link, "uds", NEW, OLD, link.backup)
            self.assertEqual(sum(r[0] == 0x2E for r in link.requests), 1)

    async def test_count_change_prevents_write(self):
        link = FakeLink()
        with self.assertRaisesRegex(ProtocolError, "number of registered sensors"):
            await register(link, "uds", NEW + ["05555555"], OLD, "unused.json")
        self.assertTrue(all(r[0] == 0x22 for r in link.requests))


class FakeElm:
    def __init__(self, replies):
        self.replies, self.commands, self.settings = iter(replies), [], []

    async def command(self, command):
        self.commands.append(command)
        return next(self.replies)

    async def at(self, command):
        self.settings.append(command)


class LinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_flow_control(self):
        elm = FakeElm(["758 2A 10 07 62 10 23 01 23", "758 2A 21 45 67 00 00 00 00"])
        self.assertEqual(await ToyotaLink(elm).request(bytes.fromhex("221023")), bytes.fromhex("62102301234567"))
        self.assertEqual(elm.commands[1], "2A30000000000000")

    async def test_segmented_send(self):
        elm = FakeElm(["758 2A 30 00 00 00 00 00 00", "", "", "", "758 2A 03 6E 20 03 00 00 00"])
        self.assertEqual(await ToyotaLink(elm).request(write_payload("uds", OLD)), bytes.fromhex("6E2003"))
        self.assertEqual(elm.settings, ["ATR0", "ATR1"])
        self.assertEqual(len(elm.commands), 5)
        self.assertTrue(all(len(c) == 16 for c in elm.commands))

    async def test_block_size(self):
        fc = "758 2A 30 02 00 00 00 00 00"
        elm = FakeElm([fc, "", fc, "", "758 2A 03 6E 20 03 00 00 00"])
        await ToyotaLink(elm).request(write_payload("uds", OLD))
        self.assertEqual(elm.settings, ["ATR0", "ATR1", "ATR0", "ATR1"])

    async def test_pending_never_retried(self):
        elm = FakeElm(["758 2A 03 7F 22 78 00 00 00"])
        with self.assertRaisesRegex(ProtocolError, "outcome is unknown"):
            await ToyotaLink(elm).request(bytes.fromhex("221023"))
        self.assertEqual(len(elm.commands), 1)

    async def test_overflow_stops_transmission(self):
        elm = FakeElm(["758 2A 32 00 00 00 00 00 00"])
        with self.assertRaises(ProtocolError):
            await ToyotaLink(elm).request(write_payload("uds", OLD))
        self.assertEqual(len(elm.commands), 1)

    async def test_ble_fragmentation_and_timeout(self):
        class Characteristic:
            properties = ["write"]

        class Client:
            async def start_notify(self, characteristic, callback):
                self.callback = callback

            async def write_gatt_char(self, characteristic, data, response):
                self.callback(None, b"ATI\rELM")
                self.callback(None, b"327\r>")

        client = Client()
        elm = await ble_elm(client, Characteristic(), None, Logger())
        self.assertEqual(await elm.command("ATI"), "ELM327")

        async def no_reply(*args, **kwargs):
            pass

        client.write_gatt_char = no_reply
        with self.assertRaises(TimeoutError):
            await elm.command("ATI", timeout=0.01)
        with self.assertRaisesRegex(ProtocolError, "desynchronized"):
            await elm.command("ATI")

    async def test_serial_round_trip_and_close(self):
        class FakeSerial:
            in_waiting = 0

            def __init__(self):
                self.written, self.pending, self.closed = [], queue.Queue(), False

            def write(self, data):
                self.written.append(data)
                # Classic adapters often split replies; deliver in two chunks.
                self.pending.put(b"ATZ\r\rELM327 ")
                self.pending.put(b"v1.5\r\r>")

            def read(self, size):
                try:
                    return self.pending.get(timeout=0.02)
                except queue.Empty:
                    return b""

            def close(self):
                self.closed = True

        port = FakeSerial()
        async with serial_elm(port, Logger()) as elm:
            self.assertEqual(await elm.command("ATZ", timeout=2), "ELM327 v1.5")
        self.assertEqual(port.written, [b"ATZ\r"])
        self.assertTrue(port.closed)


class Logger:
    def record(self, **kwargs):
        pass


if __name__ == "__main__":
    unittest.main()
