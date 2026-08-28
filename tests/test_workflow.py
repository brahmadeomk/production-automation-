"""Programming workflow and serial-number rules (SRS sections 9 and 10)."""

from datetime import date

import pytest

from progstation.core.eeprom import recommended_map
from progstation.errors import StationError


def test_successful_cycle_writes_expected_eeprom(engine, project, backend, db):
    result = engine.run_cycle(project, "op1", mfg_date=date(2026, 8, 24))
    assert result.ok, result.error_message
    assert result.serial_no == "000100"

    expected = recommended_map().build(
        {
            "serial": 100,
            "serial_text": "000100",
            "hw_revision": "RevA",
            "product_variant": "TS-1",
            "firmware_version": "1.0.0",
            "mfg_date": date(2026, 8, 24),
            "operator": "op1",
            "station_id": "TEST-STN",
            "project_name": "TestSensor",
        }
    )
    assert backend.last_eeprom == expected
    assert result.eeprom_hex == expected.hex().upper()


def test_workflow_runs_steps_in_srs_order(engine, project, backend):
    engine.run_cycle(project, "op1")
    assert backend.calls == [
        "signature", "flash", "flash_verify", "eeprom", "eeprom_verify"
    ]


def test_serial_does_not_advance_on_failure(engine, project, db, backend):
    backend.fail_on = {"eeprom_verify"}
    result = engine.run_cycle(project, "op1")
    assert not result.ok
    assert result.error_code == "E_EEPROM_VERIFY"
    assert db.next_serial_value(int(project["ProjectId"])) == 100


def test_failed_unit_does_not_consume_the_serial(engine, project, db, backend):
    backend.fail_on = {"flash"}
    engine.run_cycle(project, "op1")
    backend.fail_on = set()
    result = engine.run_cycle(project, "op1")
    assert result.serial_no == "000100"          # the number was reused
    assert db.next_serial_value(int(project["ProjectId"])) == 101


def test_fail_record_stores_no_serial(engine, project, db, backend):
    backend.fail_on = {"flash_verify"}
    engine.run_cycle(project, "op1")
    row = db.search_production(result="FAIL")[0]
    assert row["SerialNo"] == ""
    assert row["SerialValue"] is None
    assert row["ErrorCode"] == "E_FLASH_VERIFY"


def test_serial_advances_once_per_pass(engine, project, db):
    for expected in ("000100", "000101", "000102"):
        project = db.get_project(int(project["ProjectId"]))
        assert engine.run_cycle(project, "op1").serial_no == expected
    assert db.next_serial_value(int(project["ProjectId"])) == 103


def test_missing_device_is_reported(engine, project, backend):
    backend.device_present = False
    result = engine.run_cycle(project, "op1")
    assert result.error_code == "E_NO_DEVICE"
    assert "fixture" in result.operator_hint.lower()


def test_signature_mismatch_aborts_before_programming(engine, project, backend):
    backend.signature = "0x1e9403"                # an ATmega16 in an ATmega328P fixture
    result = engine.run_cycle(project, "op1")
    assert result.error_code == "E_SIGNATURE_MISMATCH"
    assert "flash" not in backend.calls


def test_missing_firmware_file(engine, project, db, tmp_path):
    db.upsert_project(
        {"HexPath": str(tmp_path / "gone.hex")}, project_id=int(project["ProjectId"])
    )
    result = engine.run_cycle(db.get_project(int(project["ProjectId"])), "op1")
    assert result.error_code == "E_FIRMWARE_MISSING"


def test_serial_range_exhaustion(engine, project, db):
    db.set_next_serial(int(project["ProjectId"]), 200)     # SerialEnd is 199
    result = engine.run_cycle(db.get_project(int(project["ProjectId"])), "op1")
    assert result.error_code == "E_SERIAL_RANGE"


def test_every_cycle_is_logged(engine, project, db, backend):
    engine.run_cycle(project, "op1")
    backend.fail_on = {"eeprom"}
    engine.run_cycle(db.get_project(int(project["ProjectId"])), "op2")
    rows = db.search_production()
    assert len(rows) == 2
    assert {r["Result"] for r in rows} == {"PASS", "FAIL"}
    assert {r["Operator"] for r in rows} == {"op1", "op2"}


def test_progress_callback_reports_each_step(engine, project):
    seen = []
    engine.run_cycle(project, "op1", progress=lambda s, m, ok: seen.append((s, ok)))
    steps = [s for s, ok in seen if ok is True]
    assert steps == [
        "fixture", "signature", "flash", "flash_verify", "eeprom", "eeprom_verify", "log"
    ]


def test_progress_callback_failure_does_not_break_the_cycle(engine, project):
    def broken(step, message, ok):
        raise RuntimeError("the GUI blew up")

    assert engine.run_cycle(project, "op1", progress=broken).ok


def test_fuses_are_written_when_configured(engine, project, db, backend):
    db.upsert_project(
        {"FuseLow": "0xFF", "FuseHigh": "0xD9"}, project_id=int(project["ProjectId"])
    )
    engine.run_cycle(db.get_project(int(project["ProjectId"])), "op1")
    assert "fuses" in backend.calls
    assert backend.calls.index("fuses") < backend.calls.index("flash")


def test_validate_project_reports_problems(engine, project, db, tmp_path):
    assert engine.validate_project(project) == []
    db.upsert_project(
        {"HexPath": str(tmp_path / "missing.hex")}, project_id=int(project["ProjectId"])
    )
    problems = engine.validate_project(db.get_project(int(project["ProjectId"])))
    assert any("not found" in p for p in problems)


def test_legacy_project_without_map_still_programs(db, engine, firmware):
    project_id = db.upsert_project(
        {
            "ProjectName": "LegacyProduct",
            "MCU": "atmega32",
            "HexPath": str(firmware),
            "SNAddress": 0,
            "MFGAddress": 8,
            "SerialStart": 1,
        }
    )
    result = engine.run_cycle(db.get_project(project_id), "op1", mfg_date=date(2026, 1, 2))
    assert result.ok
    assert result.eeprom_hex == "00000001FFFFFFFF20260102"


# ------------------------------------- configuration faults vs a missing board
def test_unusable_part_id_is_a_config_error_not_no_device(engine, project, backend):
    """A wrong MCU id must not tell the operator to reseat the board.

    avrdude returns no signature for a bad part id exactly as it does for an
    empty fixture, so without classification the operator hunts hardware for a
    fault only an administrator can fix.
    """
    from progstation.core.avrdude import AvrdudeResult

    def bad_part(mcu):
        return AvrdudeResult(False, 1, "", f"AVR Part {mcu} not found"), ""

    backend.read_signature = bad_part
    result = engine.run_cycle(project, "op1")
    assert result.error_code == "E_CONFIG"
    assert "part id" in result.error_message
    assert "TestSensor" not in result.operator_hint          # not a fixture problem


def test_bad_port_specification_is_a_config_error(engine, project, backend):
    from progstation.core.avrdude import AvrdudeResult

    backend.read_signature = lambda mcu: (
        AvrdudeResult(False, 1, "", "linuxspi_open() error: unknown port specification"), ""
    )
    result = engine.run_cycle(project, "op1")
    assert result.error_code == "E_CONFIG"
    assert "/dev/spidev" in result.error_message


def test_missing_avrdude_binary_is_a_config_error(engine, project, backend):
    from progstation.core.avrdude import AvrdudeResult

    backend.read_signature = lambda mcu: (AvrdudeResult(False, 127, "", "not found"), "")
    result = engine.run_cycle(project, "op1")
    assert result.error_code == "E_CONFIG"


def test_a_genuinely_empty_fixture_is_still_no_device(engine, project, backend):
    """A floating bus must keep reporting E_NO_DEVICE, pointing at the fixture."""
    from progstation.core.avrdude import AvrdudeResult

    backend.read_signature = lambda mcu: (
        AvrdudeResult(False, 1, "", "Device signature = 0x000000"), ""
    )
    result = engine.run_cycle(project, "op1")
    assert result.error_code == "E_NO_DEVICE"
    assert "fixture" in result.operator_hint.lower()
