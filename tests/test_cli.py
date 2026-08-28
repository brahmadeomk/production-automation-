"""End-to-end CLI coverage -- the same paths the touchscreen drives."""

import json

import pytest

from progstation.cli import main

SAMPLE_HEX = ":100000000C9434000C943E000C943E000C943E0082\n:00000001FF\n"


@pytest.fixture
def station(tmp_path):
    """A configured station on disk, with one project and one operator."""
    firmware = tmp_path / "fw.hex"
    firmware.write_text(SAMPLE_HEX, encoding="utf-8")
    config = tmp_path / "station.yaml"
    config.write_text(
        f"""
station_id: TEST-LINE
data_dir: {tmp_path}
log_dir: {tmp_path}/log
require_fixture_detect: false
database:
  path: {tmp_path}/station.db
reports:
  export_dir: {tmp_path}/exports
""",
        encoding="utf-8",
    )

    def run(*args, expect=0):
        code = main(["--config", str(config), "--simulate", *args])
        assert code == expect, f"`{' '.join(args)}` exited {code}, expected {expect}"
        return code

    run("init")
    run(
        "project", "add", "--name", "TempSensor", "--mcu", "atmega328p",
        "--hex", str(firmware), "--recommended-map", "--signature", "0x1e950f",
        "--hw-revision", "RevC", "--variant", "TS-100",
        "--firmware-version", "1.4.0", "--serial-start", "1000",
    )
    run("user", "add", "op1", "--role", "operator", "--password", "secret123")
    run.tmp_path = tmp_path
    run.firmware = firmware
    return run


def test_status_reports_health(station, capsys):
    station("status")
    out = capsys.readouterr().out
    assert "TEST-LINE" in out and "SimulatedAvrdude" in out


def test_program_then_history_and_report(station, capsys):
    station("program", "--project", "TempSensor", "--operator", "op1", "--count", "3")
    assert "PASS" in capsys.readouterr().out

    station("history")
    out = capsys.readouterr().out
    assert "001000" in out and "001002" in out and "3 record(s)" in out

    station("report", "--period", "daily")
    out = capsys.readouterr().out
    assert "PASS / FAIL  : 3 / 0" in out and "100.00 %" in out


def test_serial_counter_read_and_override(station, capsys):
    station("program", "--project", "TempSensor", "--operator", "op1")
    capsys.readouterr()
    station("project", "serial", "TempSensor")
    assert capsys.readouterr().out.strip() == "1001"

    station("project", "serial", "TempSensor", "--set", "5000")
    capsys.readouterr()
    station("program", "--project", "TempSensor", "--operator", "op1")
    assert "005000" in capsys.readouterr().out


def test_trace_shows_full_genealogy(station, capsys):
    station("program", "--project", "TempSensor", "--operator", "op1")
    capsys.readouterr()
    station("trace", "001000")
    out = capsys.readouterr().out
    assert "TempSensor" in out and "RevC" in out and "TEST-LINE" in out
    assert "EepromHex" in out


def test_trace_of_an_unknown_serial_fails(station):
    station("trace", "999999", expect=1)


def test_json_output_is_machine_readable(station, capsys):
    station("--json", "program", "--project", "TempSensor", "--operator", "op1")
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "PASS" and payload["serial_no"] == "001000"
    assert len(payload["eeprom_hex"]) == 64


def test_exports_are_written(station, capsys, tmp_path):
    station("program", "--project", "TempSensor", "--operator", "op1", "--count", "2")
    capsys.readouterr()
    station("report", "--period", "daily", "--export", str(tmp_path / "r.xlsx"))
    station("history", "--export", str(tmp_path / "h.xlsx"))
    assert (tmp_path / "r.xlsx").is_file()
    assert (tmp_path / "h.xlsx").is_file()


def test_project_show_previews_the_eeprom_block(station, capsys):
    station("project", "show", "TempSensor")
    out = capsys.readouterr().out
    assert "serial_number" in out and "RevC" in out
    assert "0x0000 (32 bytes)" in out


def test_unknown_project_is_an_error(station):
    station("program", "--project", "Nope", "--operator", "op1", expect=1)


def test_duplicate_project_needs_update_flag(station, capsys):
    station(
        "project", "add", "--name", "TempSensor", "--mcu", "atmega328p",
        "--hex", str(station.firmware), expect=1,
    )
    station(
        "project", "add", "--name", "TempSensor", "--mcu", "atmega8",
        "--hex", str(station.firmware), "--update",
    )
    capsys.readouterr()
    station("project", "list")
    assert "atmega8" in capsys.readouterr().out


def test_project_with_missing_firmware_refuses_to_run(station, capsys, tmp_path):
    station(
        "project", "add", "--name", "Broken", "--mcu", "atmega328p",
        "--hex", str(tmp_path / "missing.hex"),
    )
    capsys.readouterr()
    station("program", "--project", "Broken", "--operator", "op1", expect=1)
    assert "not ready" in capsys.readouterr().err


def test_selftest_passes(station, capsys):
    station("selftest")
    out = capsys.readouterr().out
    assert "checks passed" in out
    assert "[FAIL]" not in out


def test_audit_log_records_administration(station, capsys):
    station("audit")
    out = capsys.readouterr().out
    assert "project.create" in out and "user.create" in out


def test_user_management(station, capsys):
    station("user", "add", "op2", "--role", "admin", "--password", "secret456")
    station("user", "set", "op2", "--role", "operator")
    capsys.readouterr()
    station("user", "list")
    out = capsys.readouterr().out
    assert "op2" in out and "PasswordHash" not in out


def test_backup_status_when_disabled(station, capsys):
    station("backup", "status")
    assert "disabled" in capsys.readouterr().out


def test_actor_accepted_before_or_after_the_subcommand(station, capsys, tmp_path):
    """`--actor` is global, but typing it after the subcommand is natural.

    Both positions must work, and both must reach the audit log.
    """
    station("user", "add", "jane", "--role", "operator",
            "--password", "secret123", "--actor", "trailing.name")
    station("--actor", "leading.name", "user", "passwd", "jane",
            "--password", "othersecret")
    capsys.readouterr()

    station("audit", "--limit", "20")
    out = capsys.readouterr().out
    assert "trailing.name" in out
    assert "leading.name" in out


def test_trailing_actor_overrides_the_global_one(station, capsys):
    """When given in both places, the subcommand's value wins."""
    station("--actor", "global.name", "user", "add", "bob", "--role", "operator",
            "--password", "secret123", "--actor", "specific.name")
    capsys.readouterr()

    station("audit", "--limit", "10")
    out = capsys.readouterr().out
    create_line = next(l for l in out.splitlines() if "user.create" in l and "bob" in l)
    assert "specific.name" in create_line
    assert "global.name" not in create_line


def test_password_policy_rejects_a_short_password(station, capsys):
    station("user", "passwd", "admin", "--password", "Mecha", expect=1)
    assert "at least 6 characters" in capsys.readouterr().err


def test_doctor_shows_the_real_command_and_mcu(station, capsys):
    """`doctor` must surface the exact avrdude invocation and part id.

    Without it, diagnosing a station means hand-running avrdude and guessing
    what the application would have done differently.
    """
    station("doctor", "--project", "TempSensor")
    out = capsys.readouterr().out
    assert "avrdude" in out and "-c linuxspi" in out
    assert "/dev/spidev0.0:/dev/gpiochip0:25" in out     # the avrdude 7.x form
    assert "'atmega328p'" in out                         # quoted, so stray case shows
    assert "0x1e950f" in out


def test_doctor_reports_a_configuration_fault_plainly(station, capsys, tmp_path):
    """A bad part id must be named as such, not blamed on the fixture."""
    from progstation.core.avrdude import AvrdudeResult
    import progstation.core.avrdude as avrdude_module

    original = avrdude_module.SimulatedAvrdude.read_signature
    avrdude_module.SimulatedAvrdude.read_signature = lambda self, mcu: (
        AvrdudeResult(False, 1, "", f"AVR Part {mcu} not found"), ""
    )
    try:
        station("doctor", "--project", "TempSensor", expect=1)
        out = capsys.readouterr().out
        assert "FAILED" in out and "part id" in out
        assert "floating" not in out            # not misreported as a bus problem
    finally:
        avrdude_module.SimulatedAvrdude.read_signature = original


def test_doctor_json_output(station, capsys):
    import json

    station("--json", "doctor")
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["ok"] is True
    assert payload[0]["signature"] == "0x1e950f"
    assert "gpiochip" in payload[0]["command"]


def test_update_preserves_fields_that_were_not_supplied(station, capsys, tmp_path):
    """An update must never blank product metadata.

    Silently clearing HwRevision or FirmwareVersion stamps empty
    manufacturing data into the EEPROM of every unit programmed afterwards --
    the traceability record would be wrong, not merely missing.
    """
    station("project", "add", "--name", "TempSensor", "--update", "--mcu", "atmega328p")
    capsys.readouterr()

    station("project", "show", "TempSensor")
    out = capsys.readouterr().out
    assert "RevC" in out                       # HwRevision survived
    assert "TS-100" in out                     # ProductVariant survived
    assert "1.4.0" in out                      # FirmwareVersion survived
    assert "0x1e950f" in out                   # Signature survived
    assert "1000" in out                       # SerialStart survived


def test_update_reports_only_what_it_changed(station, capsys):
    station("project", "add", "--name", "TempSensor", "--update",
            "--firmware-version", "2.0.0")
    out = capsys.readouterr().out
    assert "changed: FirmwareVersion" in out

    capsys.readouterr()
    station("project", "show", "TempSensor")
    shown = capsys.readouterr().out
    assert "2.0.0" in shown
    assert "RevC" in shown                     # everything else untouched


def test_update_records_the_changed_columns_in_the_audit_log(station, capsys):
    station("project", "add", "--name", "TempSensor", "--update",
            "--hw-revision", "RevD", "--actor", "eng")
    capsys.readouterr()
    station("audit", "--limit", "5")
    out = capsys.readouterr().out
    assert "project.update" in out and "HwRevision" in out


def test_creating_a_project_still_requires_mcu_and_hex(station, capsys):
    station("project", "add", "--name", "Incomplete", expect=1)
    assert "--mcu" in capsys.readouterr().err


def test_new_project_gets_documented_defaults(station, capsys, tmp_path):
    firmware = tmp_path / "other.hex"
    firmware.write_text(":00000001FF\n")
    station("project", "add", "--name", "Bare", "--mcu", "atmega8",
            "--hex", str(firmware))
    capsys.readouterr()
    station("project", "show", "Bare")
    out = capsys.readouterr().out
    assert "SerialStart     : 1" in out
    assert "SerialDigits    : 6" in out
