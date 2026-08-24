"""FAT/SAT acceptance checks (SRS section 19).

``progstation selftest`` walks every subsystem the acceptance criteria name --
programming, EEPROM accuracy, serial increment rules, report generation, search
performance and backup -- and prints a pass/fail line per check.  It runs
against a throwaway in-memory database so it never disturbs production data.
"""

from __future__ import annotations

import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List

from .config import StationConfig
from .core.avrdude import SimulatedAvrdude
from .core.eeprom import EepromMap, recommended_map
from .core.ihex import parse_hex
from .core.programmer import ProgrammingEngine
from .db.database import Database
from .reports.engine import ReportEngine

_SAMPLE_HEX = ":100000000C9434000C943E000C943E000C943E0082\n:00000001FF\n"


def _check(name: str, fn: Callable[[], str]) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        detail = fn() or "ok"
        ok = True
    except AssertionError as exc:
        detail, ok = str(exc) or "assertion failed", False
    except Exception as exc:
        detail, ok = f"{type(exc).__name__}: {exc}", False
    return {
        "name": name,
        "ok": ok,
        "detail": detail,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def run_selftest(app, *, exercise_outputs: bool = False) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    tmp = Path(tempfile.mkdtemp(prefix="progstation-selftest-"))
    firmware = tmp / "selftest.hex"
    firmware.write_text(_SAMPLE_HEX, encoding="utf-8")

    # A disposable station: same code paths, simulated programmer, scratch DB.
    db = Database(":memory:")
    config = StationConfig(station_id="SELFTEST", require_fixture_detect=False)
    backend = SimulatedAvrdude()
    engine = ProgrammingEngine(db, config, backend, io=None)
    reports = ReportEngine(db)

    project_id = db.upsert_project(
        {
            "ProjectName": "SELFTEST",
            "MCU": "atmega328p",
            "HexPath": str(firmware),
            "EepromMap": recommended_map().to_dict(),
            "HwRevision": "R1",
            "ProductVariant": "SELFTEST",
            "FirmwareVersion": "0.0.1",
            "SerialStart": 500,
            "SerialEnd": 999999,
            "SerialDigits": 6,
            "Signature": "0x1e950f",
        }
    )
    project = db.get_project(project_id)

    # -- 1. configuration and environment ------------------------------------
    checks.append(_check("configuration loads", lambda: app.config.source_path or "defaults"))
    checks.append(
        _check(
            "avrdude backend present",
            lambda: (
                f"{app.backend.__class__.__name__} {app.backend.version()}"
                if app.backend.is_available()
                else _fail("avrdude executable not found")
            ),
        )
    )
    checks.append(
        _check(
            "database writable",
            lambda: f"{app.db.path} ({len(app.db.list_projects())} project(s))",
        )
    )
    checks.append(
        _check(
            "administrator exists",
            lambda: f"{app.db.count_admins()} admin(s)"
            if app.db.count_admins() > 0
            else _fail("no active administrator"),
        )
    )

    # -- 2. programming succeeds ---------------------------------------------
    def programming_succeeds() -> str:
        result = engine.run_cycle(project, "selftest", skip_fixture_check=True)
        assert result.ok, f"cycle failed: {result.error_code} {result.error_message}"
        assert result.serial_no == "000500", f"unexpected serial {result.serial_no}"
        return f"serial {result.serial_no} in {result.duration_ms} ms"

    checks.append(_check("programming succeeds", programming_succeeds))

    # -- 3. EEPROM accuracy ---------------------------------------------------
    def eeprom_accurate() -> str:
        eeprom_map = recommended_map()
        expected = eeprom_map.build(
            {
                "serial": 500,
                "serial_text": "000500",
                "hw_revision": "R1",
                "product_variant": "SELFTEST",
                "firmware_version": "0.0.1",
                "mfg_date": date.today(),
                "operator": "selftest",
                "station_id": "SELFTEST",
                "project_name": "SELFTEST",
            }
        )
        written = backend.last_eeprom
        assert written == expected, (
            f"EEPROM mismatch: wrote {written.hex().upper()},"
            f" expected {expected.hex().upper()}"
        )
        return f"{len(written)} bytes verified ({written.hex().upper()[:24]}...)"

    checks.append(_check("EEPROM content accurate", eeprom_accurate))

    # -- 4. serial increment rules -------------------------------------------
    def serial_rules() -> str:
        before = db.next_serial_value(project_id)
        backend.fail_on = {"eeprom_verify"}
        failed = engine.run_cycle(project, "selftest", skip_fixture_check=True)
        backend.fail_on = set()
        after_fail = db.next_serial_value(project_id)
        assert not failed.ok, "the forced failure unexpectedly passed"
        assert after_fail == before, (
            f"serial advanced on FAIL: {before} -> {after_fail}"
        )
        passed = engine.run_cycle(project, "selftest", skip_fixture_check=True)
        after_pass = db.next_serial_value(project_id)
        assert passed.ok, "the follow-up cycle failed"
        assert after_pass == before + 1, (
            f"serial did not advance on PASS: {before} -> {after_pass}"
        )
        assert passed.serial_no == f"{before:06d}", (
            f"the failed unit's number was not reused: got {passed.serial_no}"
        )
        return f"held at {before} on FAIL, advanced to {after_pass} on PASS"

    checks.append(_check("serial increments only on PASS", serial_rules))

    # -- 5. failure logging ---------------------------------------------------
    def failures_logged() -> str:
        rows = db.search_production(result="FAIL")
        assert rows, "the forced failure was not written to ProductionLog"
        assert rows[0]["ErrorCode"] == "E_EEPROM_VERIFY", (
            f"unexpected error code {rows[0]['ErrorCode']}"
        )
        assert rows[0]["SerialNo"] == "", "a FAIL record consumed a serial number"
        return f"{len(rows)} failure(s) logged with error codes"

    checks.append(_check("failures logged", failures_logged))

    # -- 6. report generation -------------------------------------------------
    def report_generation() -> str:
        summary = reports.daily()
        assert summary.total >= 3, f"expected >= 3 records, got {summary.total}"
        assert summary.passed + summary.failed == summary.total, "PASS+FAIL != total"
        assert summary.by_operator, "operator statistics are empty"
        return (
            f"{summary.total} records, {summary.yield_pct:.1f} % yield,"
            f" {len(summary.by_operator)} operator(s)"
        )

    checks.append(_check("report generation", report_generation))

    # -- 7. Excel export ------------------------------------------------------
    def excel_export() -> str:
        from .reports.excel import OPENPYXL_AVAILABLE, export_period

        if not OPENPYXL_AVAILABLE:
            return _fail("openpyxl is not installed - XLSX export unavailable")
        target = tmp / "selftest-report.xlsx"
        export_period(db, reports.daily(), target)
        assert target.is_file() and target.stat().st_size > 0, "no workbook was written"
        return f"{target.stat().st_size} bytes"

    checks.append(_check("Excel export", excel_export))

    # -- 8. search performance (SRS section 19) --------------------------------
    def search_performance() -> str:
        # Bulk-load so the timing reflects an index lookup, not an empty table.
        rows = 5000
        with db.transaction() as conn:
            conn.executemany(
                "INSERT INTO ProductionLog (Timestamp, ProjectId, ProjectName, SerialNo,"
                " Result, Operator, FirmwareVersion) VALUES (?, ?, ?, ?, 'PASS', 'perf', '1.0')",
                [
                    (f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z", project_id,
                     "SELFTEST", f"P{i:06d}")
                    for i in range(rows)
                ],
            )
        started = time.monotonic()
        found = db.search_production(serial="P004999", limit=10)
        elapsed_ms = (time.monotonic() - started) * 1000
        assert found, "indexed serial lookup returned nothing"
        assert elapsed_ms < 1000, f"serial search took {elapsed_ms:.0f} ms over {rows} rows"
        return f"{rows} rows, lookup in {elapsed_ms:.1f} ms"

    checks.append(_check("search performance", search_performance))

    # -- 9. backup ------------------------------------------------------------
    def backup_check() -> str:
        if not app.config.backup.enabled:
            return "skipped - backup is disabled in the configuration"
        result = app.backup.run_backup()
        assert result.ok, f"backup failed: {result.detail}"
        return f"{result.files} file(s) to {result.destination}"

    checks.append(_check("network backup", backup_check))

    # -- 10. panel outputs ----------------------------------------------------
    def panel_check() -> str:
        if app.io is None:
            return "skipped - no panel IO in this process"
        state = "simulated" if app.io.simulated else "hardware"
        fixture = "closed" if app.io.fixture_present() else "open"
        if exercise_outputs:
            app.io.green(True); time.sleep(0.4); app.io.green(False)
            app.io.red(True); time.sleep(0.4); app.io.red(False)
            app.io.beep(0.15, count=2)
            return f"{state} GPIO, fixture {fixture}, LEDs and buzzer exercised"
        return f"{state} GPIO, fixture {fixture} (use --outputs to blink the panel)"

    checks.append(_check("panel outputs", panel_check))

    db.close()
    passed = sum(1 for c in checks if c["ok"])
    return {
        "ok": passed == len(checks),
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }


def _fail(message: str):
    raise AssertionError(message)
