# tpms-relearn

Register new tyre pressure (TPMS) sensors in your Toyota from your computer, using a cheap Bluetooth OBD adapter.

New wheels or new sensors? The car will show the TPMS warning light until it learns the new sensor IDs. Workshops charge for this. With `tpms-relearn` you can do it yourself in a few minutes:

1. **Read** the sensor IDs the car currently knows.
2. **Write** the IDs of your new sensors.
3. **Verify:** the tool reads them back and only reports success if they match.

> [!WARNING]
> This tool writes to your car's TPMS control unit. It has been tested on **one car with one adapter** (see [Compatibility](#compatibility)). It is careful (see [Safety](#safety)), but you use it at your own risk. Keep your backup files.

## What you need

- A **Toyota** with a direct TPMS (sensors inside the wheels). Tested so far: C-HR 2023. See [Compatibility](#compatibility).
- An **ELM327 OBD adapter**: Bluetooth LE, classic Bluetooth, or USB. Tested: [Veepeak OBDCheck BLE](https://veepeak.com/products/obdcheck-ble). Not sure which kind yours is? See [step 1](#1-find-your-adapter).
- A **computer with Bluetooth**: a Mac or a Windows 10/11 PC (Linux should work too).
- **Python 3.11 or newer**: [python.org/downloads](https://www.python.org/downloads/)
- The **IDs of your new sensors**. They are printed on the sensor or its box, usually 7 or 8 hexadecimal characters such as `5D10A01`.

## Install

### Windows

1. Install Python from [python.org/downloads](https://www.python.org/downloads/). In the installer, tick **"Add python.exe to PATH"**.
2. Open **Command Prompt** (press Start, type `cmd`, press Enter) and run:

```bat
py -m venv tpms-relearn
tpms-relearn\Scripts\activate
pip install https://github.com/roosht3/tpms-relearn/archive/refs/heads/main.zip
tpms-relearn --help
```

Make sure Bluetooth is switched on (Settings → Bluetooth & devices). You do **not** need to pair the adapter in Windows. The tool connects to it directly.

Next time, open Command Prompt and run `tpms-relearn\Scripts\activate` first.

> [!TIP]
> Use Command Prompt, not PowerShell. PowerShell blocks the `activate` script by default.

### macOS

Open **Terminal** and run:

```sh
python3 -m venv ~/tpms-relearn
source ~/tpms-relearn/bin/activate
pip install https://github.com/roosht3/tpms-relearn/archive/refs/heads/main.zip
tpms-relearn --help
```

Allow Bluetooth for Terminal when macOS asks (System Settings → Privacy & Security → Bluetooth).

Next time, open Terminal and run `source ~/tpms-relearn/bin/activate` first.

> [!TIP]
> Don't create the environment inside an iCloud Drive folder. iCloud breaks Python environments, and you get timeouts or `ModuleNotFoundError: No module named 'tpms_relearn'`.

### Linux

Same as macOS. Your user may need permission to use Bluetooth (BlueZ).

## Usage

**Before you start:** plug the adapter into the OBD port (under the dashboard, driver's side), switch the **ignition on** (the engine can stay off), and **close every other OBD app** on your phone. A Bluetooth adapter only talks to one device at a time.

### 1. Find your adapter

```sh
tpms-relearn scan
```

You get two lists:

```json
{
  "bluetooth_le": [
    { "device": "1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D", "name": "VEEPEAK", ... }
  ],
  "serial_ports": [
    { "port": "COM5", "name": "Standard Serial over Bluetooth link (COM5)" }
  ]
}
```

**Is your adapter under `bluetooth_le`?** Copy its `device` value. On a Mac this is a long ID like the one above; on Windows and Linux it is a Bluetooth address like `AA:BB:CC:DD:EE:FF`. In the commands below, use `--device DEVICE`.

**Not there?** Your adapter probably uses classic Bluetooth (many Vgate and generic ELM327 adapters do) or USB. Those appear under `serial_ports` instead, once they are set up:

- **Classic Bluetooth on Windows:** pair the adapter in Settings → Bluetooth & devices (PIN is usually `1234` or `0000`). Then open **More Bluetooth settings → COM Ports** and note the **Outgoing** port, e.g. `COM5`.
- **Classic Bluetooth on macOS:** pair it in System Settings → Bluetooth. A port like `/dev/cu.OBDII` should appear in `serial_ports`. If none appears, your macOS doesn't offer this adapter as a serial port; use Windows or Linux instead.
- **Classic Bluetooth on Linux:** pair it, then run `sudo rfcomm bind 0 AA:BB:CC:DD:EE:FF` and use `/dev/rfcomm0`.
- **USB cable:** plug it in. It shows up as `COM3`, `/dev/cu.usbserial-…`, or `/dev/ttyUSB0`.

In the commands below, use `--port PORT` instead of `--device DEVICE`, for example `tpms-relearn read --port COM5 --protocol can`.

> [!NOTE]
> On Windows, the `\` at the end of the long commands below doesn't work. Put each command on a single line instead.

### 2. Read the current IDs

```sh
tpms-relearn read --device DEVICE --protocol can
```

```json
{
  "protocol": "can",
  "bank": "main",
  "ids": ["01A2B3C4", "01A2B3C5", "01A2B3C6", "01A2B3C7"],
  "backup": "backups/20261104T143718.400304Z.json"
}
```

This only reads, nothing is changed. It saves a backup in `backups/`.

Not sure which protocol your car uses? Run `tpms-relearn probe --device DEVICE`. It tries both `can` and `uds` and tells you which one answered.

### 3. Preview the write

Put your **current** IDs after `--expect` and your **new** IDs after `--ids`:

```sh
tpms-relearn write --device DEVICE --protocol can \
  --expect 01A2B3C4 01A2B3C5 01A2B3C6 01A2B3C7 \
  --ids    5D10A01  5D10A02  5D10A03  5D10A04
```

Without `--execute` nothing is sent. You only see the request that would be sent.

### 4. Write

Add `--execute` to the same command:

```sh
tpms-relearn write --device DEVICE --protocol can \
  --expect 01A2B3C4 01A2B3C5 01A2B3C6 01A2B3C7 \
  --ids    5D10A01  5D10A02  5D10A03  5D10A04 \
  --execute
```

```json
{
  "status": "verified",
  "ids": ["05D10A01", "05D10A02", "05D10A03", "05D10A04"],
  ...
}
```

`"status": "verified"` means the car accepted the new IDs and reading them back matched exactly.

### 5. Finish in the car

1. Inflate all tyres to the pressure on the driver's door sticker.
2. If your car has a TPMS **SET** button (under the steering wheel or in the glovebox), hold it with the ignition on until the light blinks 3 times.
3. Drive for 10–20 minutes. The TPMS light should go out.

## Safety

The tool is deliberately cautious:

- **It checks before it writes.** It reads the car first and refuses to write if the IDs don't match `--expect`. This prevents writing to the wrong car or from stale information.
- **It always makes a backup** before writing (`backups/before-write-*.json`).
- **It writes once.** If anything is unclear (connection lost, no clear answer), it stops and tells you. It never retries a write automatically.
- **It verifies.** Success is only reported after reading the new IDs back.
- **It keeps a log.** Every adapter command and answer is saved in `sessions/`.

To undo a change, run `write` again with your old IDs from the backup file.

> [!NOTE]
> Backups and logs contain your sensor IDs and adapter ID. Remove them before sharing.

## Compatibility

| Car | Adapter | Computer | Protocol | Read | Write |
|---|---|---|---|---|---|
| Toyota C-HR 2023 (1st gen, EU) | Veepeak OBDCheck BLE (ELM327 v1.5) | macOS | `can` | ✅ | ✅ |

Classic Bluetooth and USB adapters (`--port`) are supported but haven't been used with a real car yet.

Windows and Linux pass the automated tests but haven't been used with a real car yet. If you run it on a Windows PC, please report how it went.

Toyota uses the same TPMS address (`750`/`2A`) across many models, so other Toyotas, and likely Lexus, should work with one of the two supported protocols:

- **`can`**: older-style requests. Tested on the C-HR above.
- **`uds`**: newer-style requests, probably used by recent models. **Not yet tested on any car.**

Run `tpms-relearn probe` first. It only reads, and tells you which protocol your car answers. If you try it, please [report your result](../../issues/new?template=compatibility_report.md), even if it failed. Every report adds a row to this table, and it's the most useful contribution you can make.

## Troubleshooting

| Problem | What to do |
|---|---|
| Adapter missing from `scan` | Classic Bluetooth adapters never appear under `bluetooth_le`. Pair the adapter and use its serial port with `--port` (see [step 1](#1-find-your-adapter)). |
| `Adapter not found` | Ignition on? Another app (or your phone) connected to the adapter? Turn your phone's Bluetooth off. Unplug the adapter for 10 seconds. |
| `could not open port` | Check the port name with `scan`. On Windows, use the **Outgoing** COM port. Close other OBD apps that may hold the port. |
| `the adapter stopped responding` | The adapter fell asleep. Unplug it for 10 seconds, plug it back in, and try again. |
| `Neither TPMS profile returned a complete ID set` | Your car may use a protocol this tool doesn't support yet. Please open a compatibility report with the log file from `sessions/`. |
| `Current IDs differ from --expect` | Run `read` again and copy the IDs exactly, in the same order. |
| `Write was attempted but could not be verified` | Do **not** repeat the write right away. Run `read` to see what the car has now. |
| `BLE UART is ambiguous` | Run `tpms-relearn inspect --device DEVICE` and pass the UUIDs with `--write-char` and `--notify-char`. |

## Limitations

- Only the main sensor set (4 or 5 sensors). The second set (e.g. winter wheels) is not supported.
- You cannot change the number of sensors.
- It registers IDs in the car. It cannot program blank sensors or assign wheel positions.
- No tyre pressure readout, no clearing of fault codes.

## How it works

The adapter is driven as a raw CAN interface, over Bluetooth LE or a serial port. The TPMS unit sits at CAN ID `750` with extended address `2A`. See [docs/protocol.md](docs/protocol.md) for the request bytes and adapter details.

## Contributing

Compatibility reports, bug fixes and support for more cars are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Toyota. All trademarks belong to their owners.
