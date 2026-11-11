import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .protocol import ProtocolError, validate_ids, write_payload
from .service import detect, read_ids, register, save_backup
from .transport import Transcript, ToyotaLink, ble_elm, select_characteristics, serial_elm


def parser():
    p = argparse.ArgumentParser(prog="tpms-relearn", description="Read and register Toyota TPMS sensor IDs through an ELM327 adapter (Bluetooth LE, classic Bluetooth, or USB)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("scan", help="List nearby Bluetooth LE adapters and serial ports (no vehicle commands)")
    plan = sub.add_parser("plan", help="Offline write-payload preview; does not connect")
    plan.add_argument("--protocol", choices=("can", "uds"), required=True)
    plan.add_argument("ids", nargs="+")
    for name in ("inspect", "probe", "read", "write"):
        cmd = sub.add_parser(name)
        # inspect lists BLE services, so it only takes --device.
        adapter = cmd.add_mutually_exclusive_group(required=True) if name != "inspect" else cmd
        adapter.add_argument("--device", required=name == "inspect", help="Bluetooth LE adapter from scan: a UUID on macOS, a Bluetooth address (AA:BB:CC:DD:EE:FF) on Windows and Linux")
        if name != "inspect":
            adapter.add_argument("--port", help="Serial port of a paired classic-Bluetooth or USB adapter: COM5 on Windows, /dev/cu.OBDII on macOS, /dev/rfcomm0 on Linux")
            cmd.add_argument("--baud", type=int, default=38400, help="Serial speed for USB adapters (default 38400; ignored by Bluetooth serial ports)")
        cmd.add_argument("--write-char")
        cmd.add_argument("--notify-char")
        cmd.add_argument("--log", type=Path, help="New transcript file; existing files are never overwritten")
        if name in ("read", "write"):
            cmd.add_argument("--protocol", choices=("auto", "can", "uds") if name == "read" else ("can", "uds"), default="auto" if name == "read" else None, required=name == "write")
            cmd.add_argument("--backup", type=Path, help="New backup file")
        if name == "write":
            cmd.add_argument("--expect", nargs="+", required=True, help="Current IDs in their original order, from a successful read")
            cmd.add_argument("--execute", action="store_true", help="Actually register IDs; without this flag only prints an offline plan")
            cmd.add_argument("--ids", nargs="+", required=True, help="New IDs in desired registration order")
    return p


def emit(value):
    print(json.dumps(value, indent=2))


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


async def run(args):
    if args.action == "plan" or (args.action == "write" and not args.execute):
        emit({"mode": "offline preview; nothing sent", "protocol": args.protocol, "bank": "main", "ids": validate_ids(args.ids), "payload": write_payload(args.protocol, args.ids).hex(" ").upper()})
        return
    if args.action == "write":
        validate_ids(args.ids)
        validate_ids(args.expect)
    if args.action == "scan":
        emit(await scan())
    elif getattr(args, "port", None):
        await run_serial(args)
    else:
        await run_ble(args)


async def scan():
    from serial.tools.list_ports import comports
    from bleak import BleakScanner
    try:
        devices = await BleakScanner.discover(timeout=8, return_adv=True)
    except Exception as exc:  # e.g. no Bluetooth LE radio; serial ports are still useful
        print(f"Bluetooth LE scan failed: {exc or type(exc).__name__}", file=sys.stderr)
        devices = {}
    ble = [{"device": d.address, "name": adv.local_name or d.name, "rssi": adv.rssi, "services": adv.service_uuids} for d, adv in devices.values()]
    # macOS always lists these two; they are never OBD adapters.
    ports = [{"port": p.device, "name": p.description} for p in comports() if not p.device.endswith(("debug-console", "Bluetooth-Incoming-Port"))]
    return {"bluetooth_le": sorted(ble, key=lambda d: -d["rssi"]), "serial_ports": ports}


def open_transcript(args):
    log_path = args.log or Path("sessions") / f"{stamp()}.jsonl"
    print(f"Transcript: {log_path}", file=sys.stderr)
    return Transcript(log_path)


async def run_serial(args):
    transcript = open_transcript(args)
    try:
        transcript.record(port=args.port, action=args.action)
        async with serial_elm(args.port, transcript, args.baud) as elm:
            await operate(args, elm, args.port)
    finally:
        transcript.close()


async def run_ble(args):
    from bleak import BleakClient, BleakScanner
    device = await BleakScanner.find_device_by_address(args.device, timeout=10)
    if device is None:
        raise ProtocolError("Adapter not found. Plug it in, turn the ignition on, close other OBD apps, and run scan again. Classic Bluetooth adapters don't appear here: pair them and use --port instead.")
    transcript = open_transcript(args)
    try:
        async with BleakClient(device, timeout=15) as client:
            transcript.record(device=device.address, name=device.name, action=args.action)
            if args.action == "inspect":
                emit([{"service": s.uuid, "characteristics": [{"uuid": c.uuid, "properties": c.properties} for c in s.characteristics]} for s in client.services])
                return
            tx, rx = select_characteristics(client.services, args.write_char, args.notify_char)
            transcript.record(write_characteristic=tx.uuid, notify_characteristic=rx.uuid)
            await operate(args, await ble_elm(client, tx, rx, transcript), device.address)
    finally:
        transcript.close()


async def operate(args, elm, adapter):
    identity = await elm.initialize()
    print(f"Adapter: {identity}", file=sys.stderr)
    link = ToyotaLink(elm)
    if args.action in ("read", "probe"):
        snapshot = await detect(link) if args.action == "probe" or args.protocol == "auto" else await read_ids(link, args.protocol)
        snapshot["adapter"] = adapter
        backup = getattr(args, "backup", None) or Path("backups") / f"{stamp()}.json"
        save_backup(snapshot, backup)
        emit({**snapshot, "backup": str(backup)})
    else:
        backup = args.backup or Path("backups") / f"before-write-{stamp()}.json"
        emit(await register(link, args.protocol, args.ids, args.expect, backup))


def main():
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted. If a write had started, read IDs again to establish its outcome.", file=sys.stderr)
        return 130
    except TimeoutError:
        print("Error: the adapter stopped responding. Unplug it for 10 seconds, plug it back in with the ignition on, and try again. If a write had started, read IDs again first.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc or type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
