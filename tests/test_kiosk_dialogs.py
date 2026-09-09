"""Every dialog reachable on the kiosk must be typable without a second window.

This bug has been found three times in three separate dialogs: the login
screen, the exit prompt and the forced password change. Each time the dialog
relied on a keyboard in its own top-level window, which the kiosk's minimal
window manager either collapses or -- over the always-on-top main window --
wedges outright, freezing the station.

Rather than wait to find the fourth by hand, this sweeps every QDialog in the
package.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

from progstation.gui.qt import QtWidgets

import progstation.gui as gui_pkg
from progstation.gui.widgets import KeyboardPad, TouchKeyboard, TouchLineEdit


def _all_dialog_classes():
    found = {}
    for info in pkgutil.iter_modules(gui_pkg.__path__):
        module = importlib.import_module(f"{gui_pkg.__name__}.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, QtWidgets.QDialog)
                and obj.__module__.startswith(gui_pkg.__name__)
                and obj is not TouchKeyboard  # it *is* the keyboard window
            ):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return found


def test_the_sweep_actually_finds_the_dialogs():
    """A sweep that silently matches nothing would pass forever."""
    names = set(_all_dialog_classes())
    expected = {
        "progstation.gui.login.LoginDialog",
        "progstation.gui.login.ChangePasswordDialog",
        "progstation.gui.login.ExitKioskDialog",
        "progstation.gui.admin_screen.UserDialog",
        "progstation.gui.admin_screen.ProjectDialog",
    }
    missing = expected - names
    assert not missing, f"the sweep stopped seeing {sorted(missing)}"


def _build(cls, auth, app_stub):
    """Construct one dialog the way the kiosk does."""
    name = cls.__qualname__
    if name == "LoginDialog":
        return cls(auth, None, station_id="S", kiosk=True)
    if name == "ChangePasswordDialog":
        return cls(None, auth, "admin", kiosk=True)
    if name == "ExitKioskDialog":
        return cls(None, auth, kiosk=True)
    if name == "UserDialog":
        return cls(None, app_stub, "admin")
    if name == "ProjectDialog":
        return cls(None, app_stub, None, "admin")
    pytest.fail(
        f"{name} is a new dialog with no kiosk construction recipe here. Add "
        f"one, and make sure it is typable on the panel."
    )


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def auth_station():
    from progstation.db.database import Database
    from progstation.security.auth import AuthManager

    db = Database(":memory:")
    auth = AuthManager(db)
    auth.ensure_default_admin()
    auth.set_password("admin", "adminpass1", actor="test")
    yield auth
    db.close()


@pytest.fixture
def station_app(tmp_path):
    """A real StationApp -- the admin dialogs reach into it as they build."""
    from progstation.app import StationApp
    from progstation.config import StationConfig

    config = StationConfig(
        station_id="TEST-STN",
        data_dir=str(tmp_path),
        log_dir=str(tmp_path / "log"),
    )
    config.database.path = str(tmp_path / "test.db")
    config.reports.export_dir = str(tmp_path / "exports")
    app = StationApp(config, simulate=True, with_io=False)
    app.auth.ensure_default_admin()
    yield app
    app.db.close()


@pytest.fixture
def kiosk_mode():
    from progstation.gui import widgets

    widgets.set_kiosk(True)
    yield
    widgets.set_kiosk(False)


@pytest.mark.parametrize("name", sorted(_all_dialog_classes()))
def test_kiosk_dialog_is_typable_without_a_second_window(
    name, qt_app, auth_station, station_app, kiosk_mode
):
    cls = _all_dialog_classes()[name]
    from progstation.gui.style import build_stylesheet, scale_for

    qt_app.setStyleSheet(
        build_stylesheet(scale_for(qt_app.primaryScreen().geometry().height()))
    )
    dialog = _build(cls, auth_station, station_app)
    dialog.show()
    qt_app.processEvents()
    try:
        fields = dialog.findChildren(TouchLineEdit)
        if not fields:
            pytest.skip(f"{name} asks for no typed input")

        # 1. No button that would open a keyboard in its own window.
        strays = [
            b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == "⌨"
        ]
        assert not strays, (
            f"{name} still offers a keyboard in a separate window; the kiosk "
            f"window manager cannot show it"
        )

        # 2. A keyboard is actually on the dialog.
        pads = dialog.findChildren(KeyboardPad)
        assert pads, f"{name} has fields to fill in but no keyboard on it"

        # 3. It fits the panel.
        screen = qt_app.primaryScreen().geometry()
        hint = dialog.minimumSizeHint()
        assert hint.height() <= screen.height(), (
            f"{name} demands {hint.height()} px on a {screen.height()} px panel"
        )
        assert hint.width() <= screen.width(), (
            f"{name} demands {hint.width()} px on a {screen.width()} px panel"
        )

        # 4. The keys reach the field the operator tapped.
        pad = pads[0]
        target = fields[0]
        target.edit.setFocus()
        qt_app.processEvents()
        for char in "ab7":
            pad._press(char)
        assert target.text().endswith("ab7"), (
            f"{name}: keys did not reach the focused field"
        )
    finally:
        dialog.close()
