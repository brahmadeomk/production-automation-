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
| 4.1 | SPI device present | `ls /dev/spidev0.*` | **PASS** — commissioning 2026-08-28 |
| 4.2 | Real device signature read | avrdude against a known-good board | **PASS** — `0x1e950f (m328p)`, matches the project |
| 4.3 | Flash programmed and verified on a real AVR | Full cycle, serial 000001 | **PASS** — 8/8 steps, flash written and verified |
| 4.4 | EEPROM bytes correct on a real AVR | `-U eeprom:r:-:h`, compared against the built block | **PASS** — see 4.4a below |
| 4.5 | Green LED (GPIO17) / red LED (GPIO27) | `selftest --outputs` | **PASS** — both exercised |
| 4.6 | Buzzer (GPIO22): 1 beep PASS, 3 beeps FAIL | `selftest --outputs`, programming cycle | **PASS** |
| 4.7 | Start button (GPIO23) triggers a cycle | Press with `require_hardware_start: true` | ☐ not yet tested |
| 4.8 | Fixture detect (GPIO24) blocks an open fixture | Start with the fixture open → `E_FIXTURE_OPEN` | ☐ not yet tested |
| 4.9 | Touchscreen fullscreen and touch-accurate | Visual, all four screens | **PASS** — running full screen on the 1024x600 panel |
| 4.10 | Backup reaches the real SMB share | `progstation backup run` | ☐ not yet configured |
| 4.11 | Station starts on boot | `sudo reboot` | **PASS** — kiosk session starts unattended, 2026-09-09 |
| 4.12 | Power loss mid-cycle does not consume a serial | Pull power during programming | ☐ not yet tested |
| 4.13 | Operator signs in and reaches the programming screen | Kiosk sign-in on the panel | **PASS** — 2026-09-09, see 4.13a |
| 4.14 | Forced password change is completable on the panel | First sign-in of a flagged account | ☐ not confirmed on the station — fixed and verified on the bench rig (4.13a); exercise it on the next new account |
| 4.15 | Kiosk exit requires an administrator | Exit → admin credentials | **PASS** — 2026-09-09, admin accepted. Operator refusal is covered by the automated suite, not yet re-checked on the panel |
| 4.16 | Operator account can be added from the panel | Admin → Users → Add user | **PASS** — 2026-09-09 |

### 4.13a Kiosk interaction evidence

Commissioning found the station unusable on the panel in four places: the
login keyboard was invisible, the Exit button appeared dead, a first sign-in
stopped before the programming screen, and the admin dialogs could not be
filled in. All four had one cause.

Under the kiosk's window manager (`matchbox`), a **frameless** parent window
wedges every child window the application opens; the application stops
responding. Measured on a bench rig reproducing the station's environment
(Xvfb + matchbox at 1024x600), repeated five times:

| Parent window flags | Child window opens |
|---|---|
| plain | 5 / 5 |
| frameless | **0 / 5** |
| always-on-top | 5 / 5 |
| frameless + always-on-top | 1 / 5 |

A plain `QMessageBox` is enough to trigger it, so an ordinary validation
message such as "Passwords do not match" would have frozen the station in
front of an operator.

The frameless hint was removed from the main window and the sign-in dialogs.
`showFullScreen()` covers the panel on its own and the kiosk session starts
matchbox with `-use_titlebar no`, so nothing was lost: the window still
measures 1024x600 at 0,0. Every dialog that asks for typed input now carries
its own keyboard rather than opening one in a second window.

Two regression tests hold this in place: one asserts no kiosk window is
frameless, and a sweep walks every dialog in the package and fails if one has
fields but no keyboard, keeps a separate-window keyboard button, overflows the
panel, or does not type into the focused field. A newly added dialog with no
entry in the sweep fails it rather than being skipped.

Confirmed on the station itself on 2026-09-09: sign-in through to the
programming screen, kiosk exit, and operator addition (4.13, 4.15, 4.16). The
forced password change (4.14) was fixed and verified on the bench rig but has
not been repeated on the station; it appears only on an account's first
sign-in, so exercise it on the next new account.

### 4.4a EEPROM verification evidence (serial 000001)

Read back from the target with
`avrdude -U eeprom:r:-:h` and decoded against the configured map:

| Offset | Field | On chip | Verdict |
|---|---|---|---|
| 0x0000 | serial_number | `00000001` | correct |
| 0x0010 | mfg_date | `20260828` | correct, packed BCD |
| 0x001C | *reserved* | `FFFF` | untouched fill byte, as specified |
| 0x001E | crc | `0EBC` | CRC-16/CCITT recomputed over bytes 0–29 independently: **matches** |
| 0x0020+ | rest of EEPROM | `FF FF FF …` | **untouched** — the station writes only its own block |

The layout, offsets, BCD date encoding, reserved bytes, checksum and write
extent are therefore verified on real silicon.

The ASCII fields (`hw_revision`, `product_variant`, `firmware_version`) read
back as zeros on this unit because a `project add --update` had cleared the
project metadata beforehand — the defect fixed in "Update only the project
fields the operator supplied". The encoding path itself is exercised by the
automated suite and by 4.4's byte-level comparison; re-verify these three
fields on the next unit programmed with the metadata restored.

## 5. Known limitations

1. **SRS section 18 (Future Roadmap) is not implemented** — QR labels, barcode
   scanner, ERP/MES integration, central server synchronisation and the
   analytics dashboard are out of scope for this release. The `--json` CLI
   output and the stable `ErrorCode` taxonomy are the intended integration
   surface.
2. **The GUI is verified on the station's own panel** (4.9, 4.13-4.16) as well
   as offscreen under `QT_QPA_PLATFORM=offscreen`. Panel rotation is not
   exercised. Note that offscreen and windowed testing cannot reproduce the
   kiosk window manager, which is where the faults in 4.13a were found — the
   bench rig in that section exists for exactly that reason.
3. **Backup is verified against a local directory** standing in for a mounted
   share. The CIFS mount itself must be confirmed on site (4.10).
4. **Timing figures in this report come from the simulated backend** and do not
   represent real cycle times, which are dominated by avrdude.
