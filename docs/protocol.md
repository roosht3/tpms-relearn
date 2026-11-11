# Protocol notes

What `tpms-relearn` sends and what the car answers. Observed on a 2023 Toyota C-HR with a Veepeak OBDCheck BLE (ELM327 v1.5) unless marked otherwise.

## Transport

| | |
|---|---|
| BLE service | `FFF0` (write `FFF2`, notify `FFF1`) |
| Classic Bluetooth / USB | serial port, 38400 baud (Bluetooth serial ports ignore the speed) |
| CAN | 500 kbit/s, 11-bit IDs (`ATSP6`) |
| Request ID | `750`, extended address `2A` |
| Response ID | `758`, extended address `2A` |

Every CAN frame starts with the extended address `2A`, followed by a normal ISO-TP frame (7 bytes), giving 8 data bytes in total.

### Adapter setup

```
ATZ ATE0 ATL0 ATS1 ATH1 ATD0 ATSP6 ATAL ATCAF0 ATCFC0 ATSH750 ATCRA758 ATST32 ATAT0 ATR1
```

- **`ATAL` is required.** By default an ELM327 answers `?` to raw frames with 8 data bytes.
- `ATCAF0` / `ATCFC0`: the adapter does no ISO-TP formatting or flow control. `tpms-relearn` builds every frame and sends flow control itself.
- `ATCEA 2A` with 7-byte frames (letting the adapter add the address) returned `NO DATA` on the tested adapter, so it is not used.

### Flow control

The ECU replies to a first frame with `30 01 00`: block size 1, no separation time. It wants a flow-control frame after **every** consecutive frame. When the ECU sends a multi-frame reply, `tpms-relearn` answers with `2A 30 00 00 00 00 00 00`.

## `can` profile (tested)

| Purpose | Request | Reply |
|---|---|---|
| Sensor count | `21 06` | `61 06 80 80`, count = `(byte0 & E0) >> 5` = 4 |
| Sensor 1–5 ID | `21 0F` … `21 13` | `61 0F 01 A2 B3 C4`, ID = 4 bytes & `0FFFFFFF` |
| Diagnostic session | `10 5F` | `50 01` (the session byte is **not** echoed) |
| Register IDs | `3B 0E 00 <count>` + per sensor `00 00 <4-byte ID>` + `00 00 0F FF FF FF` if 4 sensors | `7F 3B 78` (pending), then `7B 0E` |

An empty fifth slot reads as `00000000`.

Example registration request for 4 sensors (34 bytes, 6 frames):

```
3B 0E 00 04
00 00 05 D1 0A 01
00 00 05 D1 0A 02
00 00 05 D1 0A 03
00 00 05 D1 0A 04
00 00 0F FF FF FF
```

## `uds` profile (untested)

The 2023 C-HR rejects these with `7F 22 11` (service not supported). They are meant for other Toyota models.

| Purpose | Request |
|---|---|
| Sensor count | `22 20 02`, count = `byte0 & 0F` |
| Sensor 1–5 ID | `22 10 23` … `22 10 27` |
| Diagnostic session | `10 03` |
| Register IDs | `2E 20 03 <count>` + 4-byte IDs + `00 00 00 00` if 4 sensors |

## Sensor IDs

Toyota IDs are 8 hex digits starting with `0`. A 7-digit ID printed on a sensor gets a leading `0` (`5D10A01` → `05D10A01`). `00000000` and `0FFFFFFF` mean "empty".

## References

- [ELM327 datasheet](https://www.elmelectronics.com/wp-content/uploads/2020/05/ELM327DSL.pdf): `AL`, `CAF`, `CFC`, `SH`, `CRA`
- [Bleak](https://bleak.readthedocs.io/): the BLE library used
