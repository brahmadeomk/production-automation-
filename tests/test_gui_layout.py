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

from PyQt5 import QtCore, QtWidgets  # noqa: E402

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


def test_kiosk_mode_is_on_top_but_never_frameless(qt_app, tmp_path):
    """The kiosk must stay on top -- and must NOT be frameless.

    Under the kiosk's window manager a frameless parent wedges every child
    window the station opens: dialogs, keyboards and message boxes alike stop
    the application dead. Measured over repeated runs against matchbox on a
    1024x600 panel, a frameless parent wedged the child 5 times out of 5,
    while the same window without the hint worked every time and still covered
    the panel exactly. showFullScreen() gives the full screen on its own, and
    the kiosk session starts matchbox with no title bar, so the hint bought
    nothing and cost every dialog.
    """
    from PyQt5 import QtCore

    from progstation.gui.app import MainWindow
    from progstation.security.auth import Session

    app = _station(tmp_path)
    try:
        kiosk = MainWindow(app, Session(1, "admin", "A", "admin"), kiosk=True)
        flags = int(kiosk.windowFlags())
        assert flags & int(QtCore.Qt.WindowStaysOnTopHint)
        assert not flags & int(QtCore.Qt.FramelessWindowHint), (
            "a frameless kiosk window freezes the station on the first dialog"
        )
        kiosk.close()

        windowed = MainWindow(app, Session(1, "admin", "A", "admin"), kiosk=False)
        assert not int(windowed.windowFlags()) & int(QtCore.Qt.FramelessWindowHint)
        windowed.close()
    finally:
        app.close()


def test_no_kiosk_dialog_is_frameless(qt_app, auth_station):
    """Same hazard one level down: these dialogs open children of their own."""
    from PyQt5 import QtCore
    from progstation.gui.login import ChangePasswordDialog, ExitKioskDialog, LoginDialog

    for dialog in (
        LoginDialog(auth_station, station_id="S", kiosk=True),
        ChangePasswordDialog(None, auth_station, "admin", kiosk=True),
        ExitKioskDialog(None, auth_station, kiosk=True),
    ):
        flags = int(dialog.windowFlags())
        assert not flags & int(QtCore.Qt.FramelessWindowHint), (
            f"{type(dialog).__name__} is frameless; anything it opens will wedge"
        )
        dialog.close()


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


@pytest.mark.parametrize("panel_height", [480, 600, 800])
def test_keyboard_fits_on_screen(qt_app, panel_height):
    """OK and Cancel must be reachable.

    The stylesheet has to be applied for this to mean anything -- the sizes
    that decide the dialog's height live there.
    """
    from progstation.gui.style import build_stylesheet, scale_for

    qt_app.setStyleSheet(build_stylesheet(scale_for(panel_height)))
    screen = qt_app.primaryScreen().availableGeometry()
    try:
        for numeric in (False, True):
            for password in (False, True):
                kb = TouchKeyboard(title="Password", numeric=numeric, password=password)
                kb.show()
                hint = kb.sizeHint()
                assert hint.height() <= screen.height(), (
                    f"keyboard is {hint.height()} px tall at the {panel_height} px"
                    f" scale, screen is {screen.height()}"
                    f" (numeric={numeric}, password={password})"
                )
                kb.close()
    finally:
        qt_app.setStyleSheet("")


def test_keyboard_keys_are_not_crushed(qt_app):
    """Every key must stay a usable touch target.

    Fitting the dialog is not enough on its own: a style sheet ``min-height``
    overrides ``setFixedHeight``, and with it set to 0 the layout squeezed the
    keys to 16 px while the control buttons stayed at 92 px.  The dialog then
    *passed* a height check, because crushed keys make it smaller.

    The scale is taken from the real screen: scaling for a panel taller than
    the one actually present is a contradiction the application cannot create,
    since it derives the scale from the screen it is running on.
    """
    from progstation.gui.style import build_stylesheet, scale_for

    panel_height = qt_app.primaryScreen().geometry().height()
    qt_app.setStyleSheet(build_stylesheet(scale_for(panel_height)))
    try:
        kb = TouchKeyboard(title="Password", password=True)
        kb.show()
        heights = {button.height() for button in kb._keys}
        assert len(heights) == 1, f"keyboard rows differ in height: {sorted(heights)}"
        assert heights.pop() >= 36, "keys are too small to hit reliably"
        kb.close()
    finally:
        qt_app.setStyleSheet("")


def test_keyboard_keys_stay_a_usable_touch_target(qt_app):
    """Shrinking to fit must not produce keys too small to hit."""
    from progstation.gui.widgets import keyboard_key_height

    assert 38 <= keyboard_key_height(numeric=False, password=True) <= 110


# --------------------------------------------------------------- kiosk exit
@pytest.fixture
def auth_station(tmp_path):
    from progstation.db.database import Database
    from progstation.security.auth import AuthManager

    db = Database(":memory:")
    auth = AuthManager(db)
    auth.ensure_default_admin()
    auth.set_password("admin", "adminpass1", actor="test")
    auth.create_user("op1", "operator1", "operator", actor="test")
    yield auth
    db.close()


def test_escape_cannot_leave_kiosk(qt_app, auth_station):
    """QDialog rejects on Escape, which would drop the operator to the desktop.

    Visibility is the observable that matters: QDialog.Rejected is 0, the same
    as a dialog that has not finished, so result() cannot tell them apart.
    """
    from progstation.gui.login import LoginDialog

    dialog = LoginDialog(auth_station, station_id="S", kiosk=True)
    dialog.show()
    assert dialog.isVisible()
    dialog.reject()
    assert dialog.isVisible(), "kiosk login closed on Escape"
    assert not dialog._exit_authorised
    dialog._exit_authorised = True          # what an authorised exit sets
    dialog.reject()
    assert not dialog.isVisible(), "authorised exit did not close the dialog"


def test_escape_still_works_outside_kiosk(qt_app, auth_station):
    from progstation.gui.login import LoginDialog

    dialog = LoginDialog(auth_station, station_id="S", kiosk=False)
    dialog.show()
    dialog.reject()
    assert not dialog.isVisible()


def test_exit_button_asks_for_authorisation_in_kiosk(qt_app, auth_station, monkeypatch):
    """The Exit button must go through the admin prompt, not straight out."""
    from progstation.gui import login as login_module

    asked = []
    monkeypatch.setattr(
        login_module.ExitKioskDialog, "authorise",
        classmethod(lambda cls, parent, auth, **kw: asked.append(kw) or False),
    )
    dialog = login_module.LoginDialog(auth_station, station_id="S", kiosk=True)
    dialog.show()
    dialog._request_exit()
    assert asked, "Exit did not ask for administrator credentials"
    assert asked[0].get("kiosk") is True, (
        "the exit prompt was not told it is running under the kiosk window "
        "manager, so it would open as a separate window the WM collapses"
    )
    assert dialog.isVisible(), "Exit closed the station without authorisation"


def test_operator_cannot_authorise_a_kiosk_exit(qt_app, auth_station):
    """Leaving kiosk mode exposes the desktop; that is an admin decision."""
    from progstation.gui.login import ExitKioskDialog

    dialog = ExitKioskDialog(None, auth_station)
    dialog.username.setText("op1")
    dialog.password.setText("operator1")
    dialog._check()
    assert dialog.authorised is False
    assert "administrator" in dialog.message.text().lower()


def test_wrong_password_cannot_authorise_a_kiosk_exit(qt_app, auth_station):
    from progstation.gui.login import ExitKioskDialog

    dialog = ExitKioskDialog(None, auth_station)
    dialog.username.setText("admin")
    dialog.password.setText("not-the-password")
    dialog._check()
    assert dialog.authorised is False


def test_administrator_can_authorise_a_kiosk_exit(qt_app, auth_station):
    from progstation.gui.login import ExitKioskDialog

    dialog = ExitKioskDialog(None, auth_station)
    dialog.username.setText("admin")
    dialog.password.setText("adminpass1")
    dialog._check()
    assert dialog.authorised is True


def test_kiosk_exit_attempts_are_audited(qt_app, auth_station):
    """A refused attempt to reach the desktop is worth seeing afterwards."""
    from progstation.gui.login import ExitKioskDialog

    denied = ExitKioskDialog(None, auth_station)
    denied.username.setText("op1")
    denied.password.setText("operator1")
    denied._check()

    allowed = ExitKioskDialog(None, auth_station)
    allowed.username.setText("admin")
    allowed.password.setText("adminpass1")
    allowed._check()

    actions = {(row["Action"], row["Username"]) for row in auth_station.db.list_audit(20)}
    assert ("kiosk.exit_denied", "op1") in actions
    assert ("kiosk.exit", "admin") in actions


# ------------------------------------------------- embedded kiosk keyboard
def _kiosk_login(qt_app, auth):
    from progstation.gui.login import LoginDialog
    from progstation.gui.style import build_stylesheet, scale_for

    qt_app.setStyleSheet(
        build_stylesheet(scale_for(qt_app.primaryScreen().geometry().height()))
    )
    dialog = LoginDialog(auth, station_id="STATION-01", kiosk=True)
    dialog.show()
    qt_app.processEvents()
    return dialog


def test_kiosk_login_has_the_keyboard_on_screen(qt_app, auth_station):
    """A keyboard in its own window is at the mercy of the kiosk's minimal
    window manager -- it opened behind the login as an empty frame. Embedded,
    there is no second window to mismanage."""
    dialog = _kiosk_login(qt_app, auth_station)
    try:
        assert getattr(dialog, "pad", None) is not None
        assert dialog.pad.isVisibleTo(dialog)
        # And no button that would open a second one.
        assert not any(
            button.text() == "⌨"
            for button in dialog.findChildren(QtWidgets.QPushButton)
        )
    finally:
        dialog.close()


def test_kiosk_login_fits_the_panel(qt_app, auth_station):
    """With the keyboard on it, the login must still fit -- otherwise Sign in
    sits below the bottom edge."""
    dialog = _kiosk_login(qt_app, auth_station)
    try:
        screen = qt_app.primaryScreen().availableGeometry()
        assert dialog.minimumSizeHint().height() <= screen.height(), (
            f"kiosk login needs {dialog.minimumSizeHint().height()} px,"
            f" panel is {screen.height()}"
        )
        assert len({b.height() for b in dialog.pad.keys}) == 1
        assert dialog.pad.keys[0].height() >= 36
    finally:
        dialog.close()


def test_kiosk_keyboard_types_into_the_focused_field(qt_app, auth_station):
    """Tapping a field must redirect the keyboard to it."""
    dialog = _kiosk_login(qt_app, auth_station)
    try:
        dialog.username.edit.setFocus()
        qt_app.processEvents()
        for char in "op1":
            dialog.pad._press(char)

        dialog.password.edit.setFocus()
        qt_app.processEvents()
        for char in "sec":
            dialog.pad._press(char)

        assert dialog.username.text() == "op1"
        assert dialog.password.text() == "sec"
    finally:
        dialog.close()


def test_keys_do_not_steal_focus_from_the_field(qt_app, auth_station):
    """A key that takes focus loses the target on the first press."""
    dialog = _kiosk_login(qt_app, auth_station)
    try:
        no_focus = (
            QtCore.Qt.FocusPolicy.NoFocus if hasattr(QtCore.Qt, "FocusPolicy")
            else QtCore.Qt.NoFocus
        )
        assert all(button.focusPolicy() == no_focus for button in dialog.pad.keys)
    finally:
        dialog.close()


def test_error_line_takes_no_room_until_there_is_an_error(qt_app, auth_station):
    """A reserved blank line costs height the keyboard needs."""
    from progstation.gui.login import ChangePasswordDialog, ExitKioskDialog, LoginDialog

    for dialog in (
        LoginDialog(auth_station, station_id="S", kiosk=True),
        ChangePasswordDialog(None, auth_station, "admin"),
        ExitKioskDialog(None, auth_station),
    ):
        dialog.show()
        assert dialog.message.isHidden(), f"{type(dialog).__name__} reserves a blank line"
        dialog._say("something went wrong")
        assert not dialog.message.isHidden()
        assert dialog.message.text() == "something went wrong"
        dialog._say("")
        assert dialog.message.isHidden()
        dialog.close()


# ------------------------------------------------ embedded exit-prompt keyboard
def _kiosk_exit(qt_app, auth):
    from progstation.gui.login import ExitKioskDialog
    from progstation.gui.style import build_stylesheet, scale_for

    qt_app.setStyleSheet(
        build_stylesheet(scale_for(qt_app.primaryScreen().geometry().height()))
    )
    dialog = ExitKioskDialog(None, auth, kiosk=True)
    dialog.setGeometry(qt_app.primaryScreen().geometry())
    dialog.show()
    qt_app.processEvents()
    return dialog


def test_kiosk_exit_prompt_carries_its_own_keyboard(qt_app, auth_station):
    """The exit prompt needs an admin password typed on a touchscreen. Asking
    for it through a second keyboard window left the operator with a dialog
    they could not fill in, so the Exit button did nothing they could see."""
    dialog = _kiosk_exit(qt_app, auth_station)
    try:
        assert getattr(dialog, "pad", None) is not None
        assert dialog.pad.isVisibleTo(dialog)
        assert not any(
            button.text() == "⌨"
            for button in dialog.findChildren(QtWidgets.QPushButton)
        )
    finally:
        dialog.close()


def test_kiosk_exit_prompt_fits_the_panel(qt_app, auth_station):
    """Under matchbox the prompt was mapped 420x30 -- a sliver with no fields
    on it. It has to ask for its full height and stay inside the screen."""
    dialog = _kiosk_exit(qt_app, auth_station)
    try:
        screen = qt_app.primaryScreen().geometry()
        assert dialog.minimumSizeHint().height() <= screen.height(), (
            f"exit prompt needs {dialog.minimumSizeHint().height()} px,"
            f" panel is {screen.height()}"
        )
        assert dialog.username.isVisibleTo(dialog)
        assert dialog.password.isVisibleTo(dialog)
        assert len({b.height() for b in dialog.pad.keys}) == 1
        assert dialog.pad.keys[0].height() >= 36
    finally:
        dialog.close()


def test_kiosk_exit_keyboard_types_into_the_focused_field(qt_app, auth_station):
    dialog = _kiosk_exit(qt_app, auth_station)
    try:
        dialog.username.edit.setFocus()
        qt_app.processEvents()
        for char in "admin":
            dialog.pad._press(char)

        dialog.password.edit.setFocus()
        qt_app.processEvents()
        for char in "pw":
            dialog.pad._press(char)

        assert dialog.username.text() == "admin"
        assert dialog.password.text() == "pw"
    finally:
        dialog.close()


def test_windowed_exit_prompt_keeps_the_keyboard_button(qt_app, auth_station):
    """Off the kiosk there is a real window manager and a desktop keyboard is
    fine, so the compact embedded pad is not forced on the maintainer."""
    from progstation.gui.login import ExitKioskDialog

    dialog = ExitKioskDialog(None, auth_station, kiosk=False)
    dialog.show()
    qt_app.processEvents()
    try:
        assert getattr(dialog, "pad", None) is None
        assert any(
            button.text() == "⌨"
            for button in dialog.findChildren(QtWidgets.QPushButton)
        )
    finally:
        dialog.close()


# ------------------------------------------ forced password change on the way in
def _kiosk_change_password(qt_app, auth):
    from progstation.gui.login import ChangePasswordDialog
    from progstation.gui.style import build_stylesheet, scale_for

    qt_app.setStyleSheet(
        build_stylesheet(scale_for(qt_app.primaryScreen().geometry().height()))
    )
    dialog = ChangePasswordDialog(None, auth, "admin", kiosk=True)
    dialog.setGeometry(qt_app.primaryScreen().geometry())
    dialog.show()
    qt_app.processEvents()
    return dialog


def test_forced_password_change_has_a_keyboard_in_kiosk(qt_app, auth_station):
    """This dialog sits between Sign in and the programming screen. Without
    keys on it the operator cannot get past it, and the station looks like it
    ignored the sign-in."""
    dialog = _kiosk_change_password(qt_app, auth_station)
    try:
        assert getattr(dialog, "pad", None) is not None
        assert dialog.pad.isVisibleTo(dialog)
        assert not any(
            button.text() == "⌨"
            for button in dialog.findChildren(QtWidgets.QPushButton)
        )
    finally:
        dialog.close()


def test_forced_password_change_fits_the_panel(qt_app, auth_station):
    dialog = _kiosk_change_password(qt_app, auth_station)
    try:
        screen = qt_app.primaryScreen().geometry()
        assert dialog.minimumSizeHint().height() <= screen.height(), (
            f"change-password needs {dialog.minimumSizeHint().height()} px,"
            f" panel is {screen.height()}"
        )
        assert dialog.first.isVisibleTo(dialog)
        assert dialog.second.isVisibleTo(dialog)
        assert len({b.height() for b in dialog.pad.keys}) == 1
        assert dialog.pad.keys[0].height() >= 36
    finally:
        dialog.close()


def test_forced_password_change_types_into_both_fields(qt_app, auth_station):
    dialog = _kiosk_change_password(qt_app, auth_station)
    try:
        dialog.first.edit.setFocus()
        qt_app.processEvents()
        for char in "newpass99":
            dialog.pad._press(char)
        dialog.second.edit.setFocus()
        qt_app.processEvents()
        for char in "newpass99":
            dialog.pad._press(char)
        assert dialog.first.text() == "newpass99"
        assert dialog.second.text() == "newpass99"
    finally:
        dialog.close()


def test_windowed_password_change_keeps_the_keyboard_button(qt_app, auth_station):
    from progstation.gui.login import ChangePasswordDialog

    dialog = ChangePasswordDialog(None, auth_station, "admin")
    dialog.show()
    qt_app.processEvents()
    try:
        assert getattr(dialog, "pad", None) is None
        assert any(
            button.text() == "⌨"
            for button in dialog.findChildren(QtWidgets.QPushButton)
        )
    finally:
        dialog.close()


def test_kiosk_login_hands_the_kiosk_flag_to_the_password_change(qt_app, auth_station,
                                                                monkeypatch):
    """A first sign-in must not drop out of the kiosk layout half way."""
    from progstation.gui import login as login_module

    auth_station.create_user(
        "newop", "temppass1", "operator", actor="test", must_change_password=True
    )
    asked = []
    monkeypatch.setattr(
        login_module.ChangePasswordDialog, "ask",
        classmethod(lambda cls, parent, auth, username, **kw: asked.append(kw) or None),
    )
    dialog = login_module.LoginDialog(auth_station, station_id="S", kiosk=True)
    dialog.show()
    dialog.username.setText("newop")
    dialog.password.setText("temppass1")
    dialog._attempt()
    try:
        assert asked, "a flagged account was let past without a password change"
        assert asked[0].get("kiosk") is True, (
            "the password-change dialog was not told it is on the kiosk, so it "
            "would rely on a separate keyboard window the operator cannot use"
        )
    finally:
        dialog.close()
