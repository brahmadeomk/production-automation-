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


# ------------------------------------------------------- the clock on screen
def _main_window(tmp_path, kiosk=False):
    from progstation.app import StationApp
    from progstation.config import StationConfig
    from progstation.gui.app import MainWindow
    from progstation.security.auth import Session

    config = StationConfig(data_dir=str(tmp_path), log_dir=str(tmp_path / "log"))
    config.database.path = str(tmp_path / "s.db")
    config.reports.export_dir = str(tmp_path / "e")
    firmware = tmp_path / "fw.hex"
    firmware.write_text(":00000001FF\n")
    app = StationApp(config, simulate=True, with_io=False)
    app.db.upsert_project(
        {"ProjectName": "P", "MCU": "atmega328p", "HexPath": str(firmware)}
    )
    window = MainWindow(app, Session(1, "admin", "A", "admin"), kiosk=kiosk)
    return app, window


def test_the_clock_is_on_every_screen(qt_app_time, india, tmp_path):
    """An operator timing a run should not have to leave the production
    screen to read the clock."""
    app, window = _main_window(tmp_path)
    try:
        assert window.clock_label.isVisibleTo(window)
        assert window.clock_label.text(), "the clock is blank"
    finally:
        app.close()


def test_an_unverified_clock_says_so_rather_than_looking_authoritative(
    qt_app_time, india, tmp_path
):
    """A station that has not reached a time server may be days out. That
    time would be copied on to paperwork."""
    app, window = _main_window(tmp_path)
    try:
        assert app.clock.last is None              # never synced
        window._update_clock()
        assert "⚠" in window.clock_label.text()
        assert window.clock_label.objectName() == "ClockUnset"

        window._update_status()
        message = window.status.currentMessage()
        assert "CLOCK NOT VERIFIED" in message
        # The bar is longer than the panel, so anything appended to it is off
        # the screen. It has to be near the front to be read at all.
        assert message.index("CLOCK NOT VERIFIED") <= message.index("Station"), (
            "the clock warning is past the edge of the panel"
        )
    finally:
        app.close()


def test_a_verified_clock_is_shown_plainly(qt_app_time, india, tmp_path):
    from datetime import datetime, timezone

    from progstation.hw.timesync import SOURCE_LAN, TimeReading

    app, window = _main_window(tmp_path)
    try:
        app.clock.last = TimeReading(
            True, SOURCE_LAN, datetime.now(timezone.utc), server="ntp.plant.local"
        )
        window._update_clock()
        assert "⚠" not in window.clock_label.text()
        assert window.clock_label.objectName() == "Clock"

        window._update_status()
        assert "CLOCK NOT VERIFIED" not in window.status.currentMessage()
    finally:
        app.close()


def test_the_clock_shows_local_time_and_the_date(qt_app_time, india, tmp_path):
    """The fault that started this was a wrong date, not a wrong time, so the
    date has to be on screen too."""
    from datetime import datetime

    app, window = _main_window(tmp_path)
    try:
        window._update_clock()
        expected = datetime.now().strftime("%d %b %H:%M")
        assert window.clock_label.text().startswith(expected)
    finally:
        app.close()


def test_the_clock_does_not_push_the_window_past_the_panel(
    qt_app_time, india, tmp_path
):
    """The nav bar has already overflowed an 800 px panel once."""
    from progstation.gui.style import STYLESHEET

    qt_app_time.setStyleSheet(STYLESHEET)
    app, window = _main_window(tmp_path, kiosk=True)
    try:
        window.show()
        # The worst case is the longer, unverified form.
        window._update_clock()
        assert window.minimumSizeHint().width() <= 800, (
            f"{window.minimumSizeHint().width()} px wide, panel is 800"
        )
    finally:
        app.close()
