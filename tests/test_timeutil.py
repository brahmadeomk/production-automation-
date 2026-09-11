"""Stored timestamps are UTC; what an operator reads must be local time.

The station in India was showing audit entries with yesterday's date: the
timestamps were stored correctly as UTC and displayed raw, five and a half
hours behind the clock on the wall.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from progstation.timeutil import (
    local, local_with_offset, parse, to_local, zone_name,
)


@pytest.fixture
def india():
    """Run the test as the station does: TZ=Asia/Kolkata, UTC+05:30."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def test_a_late_evening_utc_stamp_shows_todays_local_date(india):
    """The reported fault: 03:45 IST was displayed as the previous day."""
    stored = "2026-09-10T22:15:30.123Z"
    assert stored[:19].replace("T", " ") == "2026-09-10 22:15:30"   # what it did
    assert local(stored) == "2026-09-11 03:45:30"                   # what it does


def test_local_conversion_applies_the_half_hour_offset(india):
    assert local("2026-09-11T09:02:05Z") == "2026-09-11 14:32:05"


def test_exports_carry_the_offset(india):
    """A file read in another office must not be ambiguous."""
    assert local_with_offset("2026-09-11T09:02:05Z") == "2026-09-11 14:32:05 +05:30"


def test_zone_is_named_for_the_export_header(india):
    assert zone_name() == "IST (UTC+05:30)"


def test_a_stamp_without_a_zone_is_read_as_utc(india):
    """Older rows were written without the trailing Z."""
    assert local("2026-09-11T09:02:05") == "2026-09-11 14:32:05"


def test_an_explicit_offset_is_honoured(india):
    assert local("2026-09-11T09:02:05+00:00") == "2026-09-11 14:32:05"


@pytest.mark.parametrize("value", [None, "", "not a timestamp", "2026-13-45T99:99:99Z"])
def test_unreadable_values_render_blank_rather_than_raising(value):
    """One malformed row must not take down the screen listing it."""
    assert local(value) == ""
    assert local_with_offset(value) == ""
    assert parse(value) is None


def test_a_datetime_passes_through(india):
    moment = datetime(2026, 9, 11, 9, 2, 5, tzinfo=timezone.utc)
    assert local(moment) == "2026-09-11 14:32:05"
    assert to_local(moment).tzinfo is not None


def test_seconds_can_be_dropped_for_narrow_columns(india):
    assert local("2026-09-11T09:02:05Z", seconds=False) == "2026-09-11 14:32"


# --------------------------------------------------------- where it is used
@pytest.fixture(scope="module")
def qt_app_time():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5 import QtWidgets

    # Held for the module: a QApplication with no reference is collected the
    # moment it is made, and the next widget aborts the process.
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_audit_log_on_screen_shows_local_time(qt_app_time, india, tmp_path):
    """End to end: what the administrator actually reads."""
    from progstation.app import StationApp
    from progstation.config import StationConfig
    from progstation.gui.admin_screen import AdminScreen
    from progstation.security.auth import Session

    config = StationConfig(data_dir=str(tmp_path), log_dir=str(tmp_path / "log"))
    config.database.path = str(tmp_path / "s.db")
    config.reports.export_dir = str(tmp_path / "e")
    app = StationApp(config, simulate=True, with_io=False)
    try:
        app.auth.ensure_default_admin()
        app.db.execute(
            "INSERT INTO AuditLog (Timestamp, Username, Action, Target, Detail)"
            " VALUES (?, ?, ?, ?, ?)",
            ("2026-09-10T22:15:30.000Z", "admin", "test.entry", "", ""),
        )
        screen = AdminScreen(app, Session(1, "admin", "A", "admin"))
        shown = [
            screen.audit_table.item(row, 0).text()
            for row in range(screen.audit_table.rowCount())
        ]
        assert "2026-09-11 03:45:30" in shown, shown
        assert "2026-09-10 22:15:30" not in shown
    finally:
        app.close()


def test_exported_records_carry_local_time_and_the_zone(india, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")

    from progstation.reports.excel import export_records

    path = tmp_path / "log.xlsx"
    export_records(
        [{"Timestamp": "2026-09-10T22:15:30.000Z", "SerialNo": "000001",
          "Result": "PASS"}],
        path,
    )
    book = openpyxl.load_workbook(path)
    assert book["Production Log"].cell(row=2, column=1).value == (
        "2026-09-11 03:45:30 +05:30"
    )
    info = {
        row[0].value: row[1].value
        for row in book["Export Info"].iter_rows(min_row=2)
    }
    assert info["Timezone"] == "IST (UTC+05:30)"
