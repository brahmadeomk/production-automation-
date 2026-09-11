"""Main window: navigation between the four screens plus the status bar."""

from __future__ import annotations

import logging
from datetime import datetime
import sys
from typing import Optional

from .. import APP_NAME, __version__
from ..security.auth import Session
from .qt import ALIGN_CENTER, QtCore, QtWidgets, exec_app
from .style import build_stylesheet, scale_for
from .widgets import set_kiosk

log = logging.getLogger(__name__)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, app, session: Session, *, kiosk: bool = False):
        super().__init__()
        self.app = app
        self.session = session
        self.kiosk = kiosk
        self.setWindowTitle(f"{APP_NAME} — {app.config.station_id}")
        self.resize(800, 480)
        if kiosk:
            # A production station owns the panel: nothing may cover it and
            # there is no title bar to drag.
            # Deliberately NOT FramelessWindowHint.  Under the kiosk's window
            # manager a frameless parent wedges every child window the station
            # opens -- dialogs, keyboards and message boxes alike -- and the
            # station stops responding.  showFullScreen() already covers the
            # panel, and the session starts matchbox with no title bar, so the
            # hint bought nothing and cost every dialog.
            flags = QtCore.Qt.WindowType if hasattr(QtCore.Qt, "WindowType") else QtCore.Qt
            self.setWindowFlags(flags.WindowStaysOnTopHint)

        from .admin_screen import AdminScreen
        from .history_screen import HistoryScreen
        from .main_screen import MainScreen
        from .reports_screen import ReportsScreen

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- navigation bar ------------------------------------------------
        nav = QtWidgets.QWidget()
        nav.setStyleSheet("background: #ffffff; border-bottom: 1px solid #d8dbe0;")
        nav_layout = QtWidgets.QHBoxLayout(nav)
        nav_layout.setContentsMargins(8, 0, 8, 0)
        nav_layout.setSpacing(4)

        self.stack = QtWidgets.QStackedWidget()
        self.main_screen = MainScreen(app, session)
        self.history_screen = HistoryScreen(app, session)
        self.reports_screen = ReportsScreen(app, session)
        self.stack.addWidget(self.main_screen)
        self.stack.addWidget(self.history_screen)
        self.stack.addWidget(self.reports_screen)

        self.nav_buttons = []
        pages = [("Production", 0), ("History", 1), ("Reports", 2)]
        self.admin_screen = None
        if session.is_admin:
            self.admin_screen = AdminScreen(app, session)
            self.stack.addWidget(self.admin_screen)
            pages.append(("Settings", 3))
            self.admin_screen.projects_changed.connect(self.main_screen.refresh_projects)

        for label, index in pages:
            button = QtWidgets.QPushButton(label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, i=index: self._show(i))
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)

        nav_layout.addStretch(1)
        # An operator timing a run, or writing a lot number on a traveller,
        # should not have to leave the production screen to read the clock.
        self.clock_label = QtWidgets.QLabel()
        self.clock_label.setObjectName("Clock")
        self.clock_label.setAlignment(ALIGN_CENTER)
        nav_layout.addWidget(self.clock_label)
        # Never let it butt against the user name: squeezed, the two ran
        # together as "13:admin".
        nav_layout.addSpacing(12)

        # The panel is only 800 px wide, so keep this short: the full name and
        # role go in the tooltip and the status bar.  It must still be visible
        # -- the operator has to be able to see who the station will record
        # against every unit they program.
        user_label = QtWidgets.QLabel(session.username)
        user_label.setObjectName("Subtle")
        user_label.setToolTip(
            f"{session.full_name or session.username} ({session.role})"
        )
        user_label.setMaximumWidth(120)
        nav_layout.addWidget(user_label)
        logout = QtWidgets.QPushButton("Log out")
        logout.clicked.connect(self.close)
        nav_layout.addWidget(logout)

        layout.addWidget(nav)
        layout.addWidget(self.stack, 1)

        # ---- status bar ----------------------------------------------------
        self.status = self.statusBar()
        self._update_status()
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(15_000)

        # Ticked every second so the minute rolls over when it should, though
        # only minutes are shown: a seconds counter on a production screen is
        # movement in the corner of the eye for no benefit.
        self._update_clock()
        self._clock_timer = QtCore.QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

        self.main_screen.counters_changed.connect(self.history_screen.refresh)
        self.main_screen.counters_changed.connect(self.reports_screen.refresh)

        # Idle auto-logout keeps an unattended admin session from staying open.
        self._idle_timer: Optional[QtCore.QTimer] = None
        if app.config.security.session_timeout_min:
            self._idle_timer = QtCore.QTimer(self)
            self._idle_timer.setSingleShot(True)
            self._idle_timer.timeout.connect(self._idle_logout)
            self._reset_idle()

        app.backup.start_scheduler(lambda result: log.info("scheduled backup: %s", result.detail))

    # ------------------------------------------------------------------ nav
    def _show(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)
        widget = self.stack.widget(index)
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _update_clock(self) -> None:
        """The wall clock, and whether it can be believed.

        A station that has not reached a time server may be hours or days out.
        Showing that time plainly would be worse than showing none -- it looks
        authoritative and it would be written on to paperwork -- so an
        unverified clock says so and is coloured as a fault.
        """
        reading = self.app.clock.last
        verified = reading is not None and reading.ok
        # Date over time, in two short lines. On one line at the scale a
        # 1080-tall screen picks it was 233 px wide and pushed the whole
        # navigation bar past a 1024 px window, so Qt squeezed it and drew the
        # time clipped into the user name. Stacked it is half that, and still
        # carries the date -- the fault that started this was a wrong date.
        now = datetime.now()
        self.clock_label.setText(
            now.strftime("%d %b") + "\n" + now.strftime("%H:%M")
            + ("" if verified else " ⚠")
        )
        name = "Clock" if verified else "ClockUnset"
        if self.clock_label.objectName() != name:
            self.clock_label.setObjectName(name)
            # A style sheet is matched at polish time, so the new object name
            # only takes effect once the widget is re-polished.
            self.clock_label.style().unpolish(self.clock_label)
            self.clock_label.style().polish(self.clock_label)
        self.clock_label.setToolTip(
            self.app.time_status() if reading else "the clock has not been checked"
        )

    def _update_status(self) -> None:
        health = self.app.health()
        parts = [
            f"{self.session.username} ({self.session.role})",
            f"Station {health['station_id']}",
            f"GPIO {health['gpio_backend']}",
            f"avrdude {health['avrdude_version'] or 'not found'}",
            health["backup"],
        ]
        reading = self.app.clock.last
        if reading is None or not reading.ok:
            # At the front: the bar is longer than the panel and anything
            # appended to it is simply not on the screen.
            parts.insert(0, "CLOCK NOT VERIFIED")
        if health["simulated"]:
            # Kept short so the whole bar fits the 800 px panel.
            parts.insert(0, "SIMULATION")
        self.status.showMessage("  |  ".join(parts))

    # ----------------------------------------------------------------- idle
    def _reset_idle(self) -> None:
        if self._idle_timer:
            self._idle_timer.start(self.app.config.security.session_timeout_min * 60_000)

    def _idle_logout(self) -> None:
        if self.main_screen.worker is not None:
            self._reset_idle()  # never log out mid-cycle
            return
        log.info("idle timeout - logging out '%s'", self.session.username)
        self.close()

    def event(self, event):  # pragma: no cover - Qt event plumbing
        type_enum = QtCore.QEvent.Type if hasattr(QtCore.QEvent, "Type") else QtCore.QEvent
        if event.type() in (
            type_enum.MouseButtonPress,
            type_enum.KeyPress,
            type_enum.TouchBegin,
        ):
            self._reset_idle()
        return super().event(event)

    def closeEvent(self, event):  # pragma: no cover - Qt event plumbing
        self.app.backup.stop_scheduler()
        self.app.auth.logout(self.session)
        if self.app.io:
            self.app.io.all_off()
        super().closeEvent(event)


def _fill_screen(window) -> None:
    """Make the window occupy the whole panel.

    showFullScreen() alone can leave a window sized to its previous geometry
    under some window managers, so set it to the screen rectangle explicitly
    first.  Without this the station shows a small window on a large panel.
    """
    screen = QtWidgets.QApplication.primaryScreen()
    if screen is not None:
        window.setGeometry(screen.geometry())
    window.showFullScreen()
    window.raise_()
    window.activateWindow()


def run_gui(app, *, fullscreen: bool = True, kiosk: bool = False) -> int:
    """Start the touchscreen application.  Returns a process exit code."""
    from .login import LoginDialog

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    # Told once, so no dialog has to be told separately -- an untold dialog
    # reaches for a keyboard in its own window, which the kiosk cannot show.
    set_kiosk(kiosk)
    qt_app.setApplicationVersion(__version__)

    # Grow the interface to suit the fitted panel.  The stylesheet is written
    # for the smallest supported screen (800x480); on a 10-inch display those
    # sizes are legible but lost in the extra space.
    screen = qt_app.primaryScreen()
    geometry = screen.geometry() if screen else None
    scale = scale_for(geometry.height() if geometry else 0)
    qt_app.setStyleSheet(build_stylesheet(scale))
    # Report it the way `xdpyinfo | grep dimensions` does, so the two can be
    # compared directly when a panel is not rendering as expected.
    log.info(
        "display %s, UI scale %.2f",
        f"{geometry.width()}x{geometry.height()}" if geometry else "unknown",
        scale,
    )

    # A station that has just been powered on is exactly the one whose clock
    # is wrong, so this syncs immediately and then daily.
    app.clock.start_scheduler()

    password = app.bootstrap_admin()
    if password:
        QtWidgets.QMessageBox.information(
            None,
            "First run",
            "A default administrator account was created.\n\n"
            f"    username: admin\n    password: {password}\n\n"
            "You will be asked to change this password at login.",
        )

    # Log out returns here, so an operator hand-over does not need a restart.
    while True:
        session = LoginDialog.ask(
            app.auth, station_id=app.config.station_id, kiosk=kiosk
        )
        if session is None:
            return 0
        window = MainWindow(app, session, kiosk=kiosk)
        if fullscreen or kiosk:
            _fill_screen(window)
        else:
            window.show()
        exec_app(qt_app)
        # exec returns when the window closes -- i.e. on log out.  Loop back to
        # the login dialog so a shift hand-over needs no restart; "Exit" there
        # returns None and ends the process.
