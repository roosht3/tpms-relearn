# Changelog

## 0.2.0 (2026-11-11)

- Classic Bluetooth and USB adapters: connect with `--port` (COM5, /dev/cu.OBDII, /dev/rfcomm0). `scan` now lists serial ports too (#1)
- Windows support: install instructions, Bluetooth address help, and Windows in the test workflow
- Simpler install for everyone: `pip install` from GitHub, no git needed
- `scan` output is now an object with `bluetooth_le` and `serial_ports` lists instead of a single list

## 0.1.0 (2026-11-04)

First release.

- `scan`, `inspect`, `probe`, `read`, `plan`, and `write` commands
- Toyota `can` profile, tested end to end on a 2023 C-HR with a Veepeak OBDCheck BLE
- Toyota `uds` profile (untested)
- Read-before-write check, automatic backups, single write attempt, read-back verification
- Session logs for every adapter command and reply
