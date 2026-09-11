# Requirements Traceability Matrix

Maps every section of the *SRS — Raspberry Pi Based Sensor Programming &
Manufacturing Traceability Station* to the code that implements it and the tests
that verify it.

| SRS | Requirement | Implementation | Verified by |
|---|---|---|---|
| **1** | Manufacturing-grade touchscreen application on Pi 4B (Trixie 64-bit) for AVR programming, EEPROM writing, serial control, traceability, reporting, backup | Whole application; `progstation/app.py` | `test_cli.py`, `selftest.py` |
| **2** | Eliminate Arduino IDE programming; reduce operator dependency; consistent programming; support legacy EEPROM layouts; full production history | Fixed cycle in `core/programmer.py`; `core/eeprom.legacy_map`; `db/schema.sql` | `test_workflow.py`, `test_eeprom.py::test_legacy_map_*` |
| **3** | Python/PyQt GUI, SQLite DB, AVRDUDE backend, SPI ISP via GPIO, network backup, reporting engine, role-based users | `gui/`, `db/`, `core/avrdude.py`, `hw/`, `backup/`, `reports/`, `security/` | Full suite |
| **4** | Pi 4B, 7-inch touchscreen, level shifter, ISP fixture, green LED, red LED, buzzer, start button, fixture detect | `hw/station_io.py`; `gui/style.py` sized for 800×480 | `test_hardware.py` |
| **5** | GPIO allocation: 10 MOSI, 9 MISO, 11 SCLK, 25 RESET, 17 green, 27 red, 22 buzzer, 23 start, 24 fixture | `config.GpioConfig` defaults; `deploy/avrdude-linuxspi.conf` pins RESET | `test_config.py::test_defaults_match_the_srs_gpio_allocation`, `test_hardware.py::test_pins_are_claimed_on_the_srs_allocation` |
| **6** | Operator and Administrator roles; operators program and view reports; administrators configure projects, EEPROM maps, firmware locations and users | `security/auth.py` (`ROLES`, `Session.require_admin`); `gui/admin_screen.py` | `test_auth.py::test_roles_gate_admin_actions` |
| **7** | Project stores MCU, firmware path, EEPROM addresses, HW revision, product variant, firmware version, serial starting range | `Projects` table; `gui/admin_screen.ProjectDialog` | `test_database.py`, `test_cli.py::test_project_show_previews_the_eeprom_block` |
| **8** | Configurable EEPROM maps for legacy products; recommended 0–31 byte block with serial, HW revision, variant, manufacturing date, reserved bytes | `core/eeprom.py` (`EepromMap`, `recommended_map`, `legacy_map`) | `test_eeprom.py` (16 tests) |
| **9** | Cycle: detect fixture → read signature → program HEX → verify flash → write EEPROM → verify EEPROM → log → increment serial only on PASS | `core/programmer.ProgrammingEngine.run_cycle` | `test_workflow.py::test_workflow_runs_steps_in_srs_order` and 15 others |
| **10** | Independent counters per product; persisted in the database; failed programming shall not increment | `core/serials.py`; `Database.record_cycle(advance_serial_to=...)` commits counter and log in one transaction | `test_workflow.py::test_serial_does_not_advance_on_failure`, `::test_failed_unit_does_not_consume_the_serial`, `test_database.py::test_counter_and_log_commit_together` |
| **11** | Projects, Users, SerialCounters, ProductionLog tables; search by serial, date, operator, firmware version, result | `db/schema.sql`; `Database.search_production` with supporting indexes | `test_database.py::test_search_filters_combine`, `test_schema_creates_all_tables` |
| **12** | Main, history, reports and admin screens optimised for touch | `gui/main_screen.py`, `history_screen.py`, `reports_screen.py`, `admin_screen.py`; `gui/widgets.py` on-screen keyboard | Manual verification, `docs/screenshots/` |
| **13** | Daily, weekly and monthly reports with PASS count, FAIL count, yield, operator statistics, project summaries | `reports/engine.py` | `test_reports.py` (8 tests) |
| **14** | Export filtered records and reports to XLSX | `reports/excel.py` | `test_excel.py` (4 tests) |
| **15** | Automatic database, report and log backup to an SMB share with a configurable schedule | `backup/smb.py`; `deploy/progstation-backup.timer` | `test_backup.py` (6 tests) |
| **16** | Log no device, signature mismatch, flash verify failure, EEPROM verify failure, network backup failure, database errors | `errors.py` taxonomy → `ProductionLog.ErrorCode`; `BackupLog` | `test_workflow.py::test_every_cycle_is_logged`, `test_backup.py::test_failure_is_recorded_not_raised` |
| **17** | Password hashing, role-based access, audit logs, protected configuration pages | `security/auth.py` (PBKDF2-SHA256, salted); `AuditLog`; `AdminScreen` calls `require_admin` | `test_auth.py::test_password_hash_is_salted_and_verifiable`, `::test_actions_are_audited` |
| **18** | Future roadmap: QR labels, barcode scanner, ERP/MES integration, central sync, analytics | Not implemented — out of scope for this release. `--json` on every CLI command and the stable `ErrorCode` taxonomy are the integration surface these will build on | — |
| **19** | FAT/SAT: verify programming success, EEPROM accuracy, serial increment rules, report generation, search performance, backup | `selftest.py` — 13 checks, runnable on the station via `progstation selftest` or **Settings → System** | `test_cli.py::test_selftest_passes` |
| **20** | Deliverables: source code, installer, configuration files, database schema, user manual, maintenance guide, test report | This repository; `install.sh`; `config/station.yaml`; `progstation/db/schema.sql`; `docs/` | — |
| **App. A** | `Projects(ProjectName, MCU, HexPath, SNAddress, MFGAddress)`, `ProductionLog(Timestamp, SerialNo, Result, Operator, Error)`, `Users(Username, Role, PasswordHash)` | `db/schema.sql` — all named columns present, extended with traceability fields | `test_database.py::test_schema_creates_all_tables` |

## Deviations and additions

**Section 18 (Future Roadmap) is not implemented.** The SRS lists it as a
roadmap, not a requirement for this release.

The following were added because the SRS implies them without stating them:

| Addition | Why |
|---|---|
| Expected device signature per project | Section 9 requires reading the signature; without a value to compare against, the read cannot detect a wrong board. Optional per project. |
| Fuse and lock byte programming | Required in practice for a first-time-programmed AVR. Only written when configured. |
| `--simulate` mode and a simulated GPIO backend | Makes FAT, training and development possible without a fixture, and is what keeps the core testable. |
| CLI covering every GUI function | Section 18's roadmap (ERP/MES integration, central sync) needs a scriptable surface; `--json` output provides it. |
| Login throttling and idle auto-logout | Section 17 requires role-based access; an unattended admin session would defeat it. |
| `AuditLog` and `BackupLog` tables | Section 17 requires audit logs, section 16 requires backup failures to be logged; neither is expressible in the Appendix A tables. |
