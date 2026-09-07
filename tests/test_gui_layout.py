"""Touch keyboard and panel-fit checks.

These need PyQt and run headless under the offscreen platform.  They exist
because both faults they cover were found on the real station, not in review:
an uppercase-only keyboard cannot type a mixed-case password, and a window
whose minimum size exceeds the panel clips its own controls.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

from PyQt5 import QtWidgets  # noqa: E402

from progstation.gui.widgets import TouchKeyboard  # noqa: E402

#: The station's 7-inch panel.
PANEL = (800, 480)


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


# ------------------------------------------------------------------ keyboard
def test_keyboard_types_lower_case_by_default(qt_app):
    kb = TouchKeyboard(title="Password", password=True)
    for char in "mecha":
        kb._press(char)
    assert kb.value() == "mecha"


def test_shift_applies_to_one_key_then_releases(qt_app):
    """A password like 'Mecha123' must be typable."""
    kb = TouchKeyboard(title="Password", password=True)
    kb.shift_button.setChecked(True)
    kb._toggle_shift()
    for char in "mecha":
        kb._press(char)
    for char in "123":
        kb._press(char)
    assert kb.value() == "Mecha123"
    assert kb._shift is False


def test_caps_latches_until_turned_off(qt_app):
    kb = TouchKeyboard()
    kb.caps_button.setChecked(True)
    kb._toggle_caps()
    for char in "abc":
        kb._press(char)
    kb.caps_button.setChecked(False)
    kb._toggle_caps()
    kb._press("d")
    assert kb.value() == "ABCd"


def test_shift_while_caps_gives_lower_case(qt_app):
    """Same combining rule as a physical keyboard."""
    kb = TouchKeyboard()
    kb.caps_button.setChecked(True)
    kb._toggle_caps()
    kb.shift_button.setChecked(True)
    kb._toggle_shift()
    kb._press("a")
    kb._press("b")
    assert kb.value() == "aB"


def test_shifted_digits_reach_password_symbols(qt_app):
    kb = TouchKeyboard(password=True)
    kb.shift_button.setChecked(True)
    kb._toggle_shift()
    kb._press("1")
    assert kb.value() == "!"


def test_key_faces_show_what_they_will_type(qt_app):
    kb = TouchKeyboard()
    letters = {base: button for button, base in kb._letter_keys if base.isalpha()}
    assert letters["q"].text() == "q"
    kb.caps_button.setChecked(True)
    kb._toggle_caps()
    assert letters["q"].text() == "Q"


def test_numeric_keypad_has_no_shift(qt_app):
    kb = TouchKeyboard(numeric=True)
    assert not hasattr(kb, "shift_button")
    kb._press("7")
    assert kb.value() == "7"


# --------------------------------------------------------------- panel fit
def _station(tmp_path):
    from progstation.app import StationApp
    from progstation.config import StationConfig

    cfg = StationConfig(data_dir=str(tmp_path), log_dir=str(tmp_path / "log"))
    cfg.database.path = str(tmp_path / "p.db")
    cfg.reports.export_dir = str(tmp_path / "exports")
    firmware = tmp_path / "fw.hex"
    firmware.write_text(":00000001FF\n")
    app = StationApp(cfg, simulate=True, with_io=False)
    app.db.upsert_project(
        {"ProjectName": "P", "MCU": "atmega328p", "HexPath": str(firmware)}
    )
    return app


def test_window_fits_the_panel(qt_app, tmp_path):
    """Every screen must fit 800x480 or its own controls get clipped."""
    from progstation.gui.app import MainWindow
    from progstation.gui.style import STYLESHEET
    from progstation.security.auth import Session

    qt_app.setStyleSheet(STYLESHEET)
    app = _station(tmp_path)
    try:
        window = MainWindow(
            app, Session(1, "admin", "Default Administrator", "admin"), kiosk=True
        )
        window.show()
        minimum = window.minimumSizeHint()
        assert minimum.width() <= PANEL[0], f"{minimum.width()} px wide, panel is {PANEL[0]}"
        assert minimum.height() <= PANEL[1], f"{minimum.height()} px tall, panel is {PANEL[1]}"
        for index in range(window.stack.count()):
            page = window.stack.widget(index)
            hint = page.minimumSizeHint()
            assert hint.width() <= PANEL[0], f"{type(page).__name__} is {hint.width()} px wide"
        window.close()
    finally:
        app.close()


def test_kiosk_mode_is_frameless_and_on_top(qt_app, tmp_path):
    from PyQt5 import QtCore

    from progstation.gui.app import MainWindow
    from progstation.security.auth import Session

    app = _station(tmp_path)
    try:
        kiosk = MainWindow(app, Session(1, "admin", "A", "admin"), kiosk=True)
        flags = int(kiosk.windowFlags())
        assert flags & int(QtCore.Qt.FramelessWindowHint)
        assert flags & int(QtCore.Qt.WindowStaysOnTopHint)
        kiosk.close()

        windowed = MainWindow(app, Session(1, "admin", "A", "admin"), kiosk=False)
        assert not int(windowed.windowFlags()) & int(QtCore.Qt.FramelessWindowHint)
        windowed.close()
    finally:
        app.close()


# ------------------------------------------------------------------ scaling
@pytest.mark.parametrize(
    "screen_height, expected",
    [(480, 1.00), (600, 1.25), (768, 1.60), (800, 1.60), (1080, 1.60), (0, 1.00)],
)
def test_ui_scale_matches_the_panel(screen_height, expected):
    """Sizes are written for 800x480; larger panels grow, nothing shrinks."""
    from progstation.gui.style import scale_for

    assert scale_for(screen_height) == pytest.approx(expected, abs=0.01)


def test_scaled_stylesheet_enlarges_metrics():
    from progstation.gui.style import STYLESHEET, build_stylesheet

    import re

    def start_font(css):
        return int(re.search(r"QPushButton#Start.*?font-size: (\d+)px", css, re.S).group(1))

    base = start_font(STYLESHEET)
    assert start_font(build_stylesheet(1.0)) == base
    assert start_font(build_stylesheet(1.6)) > base


def test_layout_fits_a_ten_inch_panel(qt_app, tmp_path):
    """The 10-inch HDMI panels in use are 1024x600 and 1280x800."""
    from progstation.gui.app import MainWindow
    from progstation.gui.style import build_stylesheet, scale_for
    from progstation.security.auth import Session

    app = _station(tmp_path)
    try:
        for width, height in ((800, 480), (1024, 600), (1280, 800)):
            qt_app.setStyleSheet(build_stylesheet(scale_for(height)))
            window = MainWindow(app, Session(1, "admin", "A", "admin"), kiosk=True)
            window.setGeometry(0, 0, width, height)
            window.show()
            minimum = window.minimumSizeHint()
            assert minimum.width() <= width, (
                f"{minimum.width()} px wide at scale for {width}x{height}"
            )
            assert minimum.height() <= height, (
                f"{minimum.height()} px tall at scale for {width}x{height}"
            )
            window.close()
    finally:
        app.close()
