# Test Report
### RPi Sensor Programming & Manufacturing Traceability Station v1.0.0

| | |
|---|---|
| Date | 2026-08-24 |
| Software version | 1.0.0 |
| Python | 3.11.15 |
| PyQt | 5.15.14 |
| openpyxl | 3.1.5 |
| Programmer backend | Simulated (`SimulatedAvrdude`) |
| GPIO backend | Simulated |

## 1. Scope

Verifies the implementation against the SRS. See
[TRACEABILITY.md](TRACEABILITY.md) for the requirement-by-requirement mapping.

**Covered by automated test:** EEPROM map engine, Intel HEX generation, the
programming workflow and its serial-number rules, database schema and search,
authentication and roles, reporting, XLSX export, network backup, the avrdude
command construction, panel IO, and the CLI end to end.

**Covered by inspection and manual test:** the touchscreen screens (built and
exercised offscreen — see `docs/screenshots/`), and everything requiring real
hardware (section 4 below).

## 2. Automated test results

```
........................................................................ [ 61%]
..............................................                           [100%]
118 passed in 9.89s
```

| Suite | Tests | Area |
|---|---:|---|
| `test_workflow.py` | 16 | Programming cycle, step order, serial rules, failure handling (SRS 9, 10, 16) |
| `test_hardware.py` | 19 | Panel IO, GPIO allocation, avrdude command construction (SRS 4, 5) |
| `test_cli.py` | 15 | End-to-end: init, project, program, history, report, export, trace, audit |
| `test_eeprom.py` | 14 | EEPROM map engine, legacy layouts, CRC, field validation (SRS 8) |
| `test_auth.py` | 12 | Password hashing, roles, lockout, audit (SRS 6, 17) |
| `test_database.py` | 10 | Schema, transactional counter+log commit, search (SRS 11) |
| `test_reports.py` | 9 | Daily/weekly/monthly aggregation, operator statistics (SRS 13) |
| `test_config.py` | 7 | Configuration defaults and validation (SRS 5) |
| `test_ihex.py` | 6 | Intel HEX round-trip and rejection of malformed files |
| `test_backup.py` | 6 | SMB backup, snapshot consistency, pruning, failure logging (SRS 15, 16) |
| `test_excel.py` | 4 | XLSX export of records and reports (SRS 14) |
| **Total** | **118** | |

## 3. FAT/SAT acceptance checks (SRS section 19)

Run on the station with `progstation selftest`, or from **Settings → System**.

```
  [PASS] configuration loads          /tmp/tmp.H8ofuZPs0b/station.yaml
  [PASS] avrdude backend present      SimulatedAvrdude simulated
  [PASS] database writable            /tmp/tmp.H8ofuZPs0b/progstation.db (1 project(s))
  [PASS] administrator exists         1 admin(s)
  [PASS] programming succeeds         serial 000500 in 0 ms
  [PASS] EEPROM content accurate      32 bytes verified (000001F45231000053454C46...)
  [PASS] serial increments only on PASS held at 501 on FAIL, advanced to 502 on PASS
  [PASS] failures logged              1 failure(s) logged with error codes
  [PASS] report generation            3 records, 66.7 % yield, 1 operator(s)
  [PASS] Excel export                 9089 bytes
  [PASS] search performance           5000 rows, lookup in 1.2 ms
  [PASS] network backup               skipped - backup is disabled in the configuration
  [PASS] panel outputs                simulated GPIO, fixture closed (use --outputs to blink the panel)

13/13 checks passed
```

| SRS 19 criterion | Check | Result |
|---|---|---|
| Verify programming success | `programming succeeds` | PASS |
| EEPROM accuracy | `EEPROM content accurate` — bytes written compared against an independently built block | PASS |
| Serial increment rules | `serial increments only on PASS` — forced failure holds the counter, the number is then reused | PASS |
| Report generation | `report generation` | PASS |
| Search performance | `search performance` — indexed serial lookup over 5 000 rows | PASS |
| Backup functionality | `network backup` | Skipped — backup disabled in this configuration; verified by `test_backup.py` |

## 4. Verified on the target hardware

The following require a Pi 4B with the ISP fixture and are **not** covered by
the automated suite. Record results here during site acceptance:

| # | Check | Method | Result |
|---|---|---|---|
| 4.1 | SPI device present | `ls /dev/spidev0.*` | ☐ |
| 4.2 | Real device signature read | `progstation program` against a known-good board | ☐ |
| 4.3 | Flash programmed and verified on a real AVR | Programming cycle, then read back with avrdude | ☐ |
| 4.4 | EEPROM bytes correct on a real AVR | `avrdude -U eeprom:r:-:h` compared against `progstation project show` | ☐ |
| 4.5 | Green LED (GPIO17) / red LED (GPIO27) | **Settings → Test LEDs and buzzer** | ☐ |
| 4.6 | Buzzer (GPIO22): 1 beep PASS, 3 beeps FAIL | Programming cycle | ☐ |
| 4.7 | Start button (GPIO23) triggers a cycle | Press with `require_hardware_start: true` | ☐ |
| 4.8 | Fixture detect (GPIO24) blocks an open fixture | Start with the fixture open → `E_FIXTURE_OPEN` | ☐ |
| 4.9 | Touchscreen fullscreen and touch-accurate | Visual, all four screens | ☐ |
| 4.10 | Backup reaches the real SMB share | `progstation backup run` | ☐ |
| 4.11 | Station starts on boot | `sudo reboot` | ☐ |
| 4.12 | Power loss mid-cycle does not consume a serial | Pull power during programming; compare counter before/after | ☐ |

## 5. Known limitations

1. **SRS section 18 (Future Roadmap) is not implemented** — QR labels, barcode
   scanner, ERP/MES integration, central server synchronisation and the
   analytics dashboard are out of scope for this release. The `--json` CLI
   output and the stable `ErrorCode` taxonomy are the intended integration
   surface.
2. **The GUI is verified offscreen, not on a physical panel.** Screens build,
   navigate and run cycles correctly under `QT_QPA_PLATFORM=offscreen`;
   touch accuracy and panel rotation must be confirmed on the station (4.9).
3. **Backup is verified against a local directory** standing in for a mounted
   share. The CIFS mount itself must be confirmed on site (4.10).
4. **Timing figures in this report come from the simulated backend** and do not
   represent real cycle times, which are dominated by avrdude.
