"""Login dialog and forced first-password change (SRS sections 6 and 17)."""

from __future__ import annotations

from typing import Optional

from ..errors import AuthError
from ..security.auth import AuthManager, Session
from .qt import ALIGN_CENTER, QtCore, QtWidgets, exec_dialog
from .qt import ECHO_PASSWORD
from .widgets import (
    KeyboardPad,
    TouchLineEdit,
    fit_with_keyboard,
    keyboard_key_height,
)


class _MessageMixin:
    """Shows an error line only when there is an error.

    Reserving a blank line costs height that the on-screen keyboard needs on a
    short panel.
    """

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))


class LoginDialog(_MessageMixin, QtWidgets.QDialog):
    def __init__(self, auth: AuthManager, parent=None, *, station_id: str = "",
                 kiosk: bool = False):
        super().__init__(parent)
        self.auth = auth
        self.session: Optional[Session] = None
        self.kiosk = kiosk
        self._exit_authorised = False
        self.setWindowTitle("Operator Login")
        self.setModal(True)
        self.setMinimumWidth(420)
        if kiosk:
            flags = QtCore.Qt.WindowType if hasattr(QtCore.Qt, "WindowType") else QtCore.Qt
            self.setWindowFlags(flags.FramelessWindowHint | flags.WindowStaysOnTopHint)

        # In kiosk the dialog covers the whole panel, so the form is centred at
        # a readable width instead of stretching a two-field login across a
        # metre of screen.
        outer = QtWidgets.QHBoxLayout(self)
        outer.addStretch(1)
        column = QtWidgets.QWidget()
        column.setMaximumWidth(560)
        outer.addWidget(column, 0)
        outer.addStretch(1)

        layout = QtWidgets.QVBoxLayout(column)
        # The kiosk login carries a keyboard as well as the form, so it is laid
        # out compactly: one title line, and the fields identified by their
        # placeholders rather than separate labels.  Without that the Sign in
        # button ends up below the bottom of the panel.
        layout.setSpacing(6 if kiosk else 12)

        title = QtWidgets.QLabel(
            f"Sign in — {station_id}" if (kiosk and station_id) else "Sign in"
        )
        title.setObjectName("Title")
        title.setAlignment(ALIGN_CENTER)
        layout.addWidget(title)

        if station_id and not kiosk:
            station = QtWidgets.QLabel(station_id)
            station.setObjectName("Subtle")
            station.setAlignment(ALIGN_CENTER)
            layout.addWidget(station)

        if not kiosk:
            layout.addWidget(QtWidgets.QLabel("Username"))
        self.username = TouchLineEdit(placeholder="Username", keyboard_button=not kiosk)
        layout.addWidget(self.username)

        if not kiosk:
            layout.addWidget(QtWidgets.QLabel("Password"))
        self.password = TouchLineEdit(
            placeholder="Password", password=True, keyboard_button=not kiosk
        )
        layout.addWidget(self.password)

        self.reveal = QtWidgets.QCheckBox("Show characters")
        self.reveal.toggled.connect(self._set_reveal)
        layout.addWidget(self.reveal)

        self.message = QtWidgets.QLabel("")
        self.message.setStyleSheet("color: #c5221f; font-weight: 600;")
        self.message.setWordWrap(True)
        # Reserving a line for an error that is not there costs height the
        # keyboard needs; show it only when there is something to say.
        self.message.setVisible(False)
        layout.addWidget(self.message)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Exit kiosk" if kiosk else "Exit")
        cancel.clicked.connect(self._request_exit)
        buttons.addWidget(cancel)
        sign_in = QtWidgets.QPushButton("Sign in")
        sign_in.setObjectName("Primary")
        sign_in.setDefault(True)
        sign_in.clicked.connect(self._attempt)
        buttons.addWidget(sign_in, 2)
        layout.addLayout(buttons)

        if kiosk:
            # There is no desktop keyboard behind a kiosk, and a keyboard in
            # its own window is at the mercy of a minimal window manager, so
            # put it on the screen itself.
            self.pad = KeyboardPad(
                column, key_height=keyboard_key_height(numeric=False, password=True)
            )
            self.pad.set_target(self.username.edit)
            # Above the buttons: the operator types, then presses Sign in.
            layout.insertWidget(layout.count() - 1, self.pad)
            self.username.edit.installEventFilter(self)
            self.password.edit.installEventFilter(self)
            self.username.edit.setFocus()
            # Compact the inputs and buttons: at the panel's scale two fields
            # alone took 144 px, which the keyboard needs more than they do.
            # The pad styles its own keys, and #Key wins over this rule.
            self.setStyleSheet(
                "QLineEdit { min-height: 30px; max-height: 40px; padding: 4px 8px; }"
                "QPushButton { min-height: 38px; max-height: 46px; padding: 4px 10px; }"
            )
            # The fit runs on first show, not here: a style sheet does not
            # reach size hints until the widget is polished, so measuring now
            # would size the keyboard against the old, larger metrics.
            self._fitted = False

        self.password.edit.returnPressed.connect(self._attempt)
        self.username.edit.returnPressed.connect(self.password.edit.setFocus)

    def _attempt(self) -> None:
        try:
            session = self.auth.login(self.username.text(), self.password.text())
        except AuthError as exc:
            self._say(str(exc))
            self.password.clear()
            return
        if session.must_change_password:
            new_password = ChangePasswordDialog.ask(
                self, self.auth, session.username, kiosk=self.kiosk
            )
            if not new_password:
                self._say("A password change is required before you can continue.")
                return
            session = self.auth.login(session.username, new_password)
        self.session = session
        self.accept()

    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        if getattr(self, "pad", None) is not None and not self._fitted:
            self._fitted = True
            fit_with_keyboard(self, self.pad)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        """Point the embedded keyboard at whichever field the operator tapped."""
        focus_in = (
            QtCore.QEvent.Type.FocusIn if hasattr(QtCore.QEvent, "Type")
            else QtCore.QEvent.FocusIn
        )
        if event.type() == focus_in and getattr(self, "pad", None) is not None:
            self.pad.set_target(watched)
        return super().eventFilter(watched, event)

    def _set_reveal(self, shown: bool) -> None:
        normal = (
            QtWidgets.QLineEdit.EchoMode.Normal
            if hasattr(QtWidgets.QLineEdit, "EchoMode")
            else QtWidgets.QLineEdit.Normal
        )
        self.password.edit.setEchoMode(normal if shown else ECHO_PASSWORD)

    # ------------------------------------------------------------------ exit
    def _request_exit(self) -> None:
        """Leave the application.

        In kiosk mode the station is the only thing on the screen, so quitting
        exposes the desktop.  That needs an administrator, not a stray tap on
        a button next to the password field.
        """
        if not self.kiosk:
            super().reject()
            return
        if ExitKioskDialog.authorise(self, self.auth, kiosk=self.kiosk):
            self._exit_authorised = True
            super().reject()

    def reject(self) -> None:
        """Swallow Escape and window-close while in kiosk mode.

        QDialog rejects on Escape, which would drop the operator to the
        desktop without so much as a prompt.
        """
        if self.kiosk and not self._exit_authorised:
            return
        super().reject()

    @classmethod
    def ask(cls, auth: AuthManager, parent=None, *, station_id: str = "",
            kiosk: bool = False) -> Optional[Session]:
        dialog = cls(auth, parent, station_id=station_id, kiosk=kiosk)
        if kiosk:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is not None:
                dialog.setGeometry(screen.geometry())
        if exec_dialog(dialog):
            return dialog.session
        return None


class ChangePasswordDialog(_MessageMixin, QtWidgets.QDialog):
    """Set a new password, on the way in when the account is flagged for it.

    This sits in the middle of the sign-in path, so on the kiosk it is laid
    out like the login screen and carries its own keys.  Relying on a separate
    keyboard window left the operator on a dialog they could not fill in and
    could not get past, so the programming screen never arrived.
    """

    def __init__(self, parent, auth: AuthManager, username: str,
                 *, kiosk: bool = False):
        super().__init__(parent)
        self.auth = auth
        self.username = username
        self.kiosk = kiosk
        self.new_password: Optional[str] = None
        self.setWindowTitle("Change Password")
        self.setModal(True)
        if kiosk:
            flags = QtCore.Qt.WindowType if hasattr(QtCore.Qt, "WindowType") else QtCore.Qt
            self.setWindowFlags(flags.FramelessWindowHint | flags.WindowStaysOnTopHint)
        else:
            self.setMinimumWidth(420)

        outer = QtWidgets.QHBoxLayout(self)
        outer.addStretch(1)
        column = QtWidgets.QWidget()
        column.setMaximumWidth(560)
        outer.addWidget(column, 0)
        outer.addStretch(1)

        layout = QtWidgets.QVBoxLayout(column)
        layout.setSpacing(6 if kiosk else 10)
        header = QtWidgets.QLabel(f"Set a new password for '{username}'")
        header.setObjectName("Title")
        header.setWordWrap(True)
        if kiosk:
            header.setAlignment(ALIGN_CENTER)
        layout.addWidget(header)

        if not kiosk:
            layout.addWidget(QtWidgets.QLabel("New password"))
        self.first = TouchLineEdit(
            placeholder="New password", password=True, keyboard_button=not kiosk
        )
        layout.addWidget(self.first)
        if not kiosk:
            layout.addWidget(QtWidgets.QLabel("Repeat password"))
        self.second = TouchLineEdit(
            placeholder="Repeat password", password=True, keyboard_button=not kiosk
        )
        layout.addWidget(self.second)

        self.message = QtWidgets.QLabel("")
        self.message.setStyleSheet("color: #c5221f; font-weight: 600;")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QtWidgets.QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(save, 2)
        layout.addLayout(buttons)

        self._fitted = True
        if kiosk:
            self.setStyleSheet(
                "QLineEdit { min-height: 30px; max-height: 40px; padding: 4px 8px; }"
                "QPushButton { min-height: 38px; max-height: 46px; padding: 4px 10px; }"
            )
            self.pad = KeyboardPad(
                column, key_height=keyboard_key_height(numeric=False, password=True)
            )
            self.pad.set_target(self.first.edit)
            layout.insertWidget(layout.count() - 1, self.pad)
            self.first.edit.installEventFilter(self)
            self.second.edit.installEventFilter(self)
            self.first.edit.setFocus()
            self._fitted = False

    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        if getattr(self, "pad", None) is not None and not self._fitted:
            self._fitted = True
            fit_with_keyboard(self, self.pad)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        focus_in = (
            QtCore.QEvent.Type.FocusIn if hasattr(QtCore.QEvent, "Type")
            else QtCore.QEvent.FocusIn
        )
        if event.type() == focus_in and getattr(self, "pad", None) is not None:
            self.pad.set_target(watched)
        return super().eventFilter(watched, event)

    def _save(self) -> None:
        if self.first.text() != self.second.text():
            self._say("The two passwords do not match.")
            return
        try:
            self.auth.set_password(self.username, self.first.text(), actor=self.username)
        except AuthError as exc:
            self._say(str(exc))
            return
        self.new_password = self.first.text()
        self.accept()

    @classmethod
    def ask(cls, parent, auth: AuthManager, username: str,
            *, kiosk: bool = False) -> Optional[str]:
        dialog = cls(parent, auth, username, kiosk=kiosk)
        if kiosk:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is not None:
                dialog.setGeometry(screen.geometry())
        if exec_dialog(dialog):
            return dialog.new_password
        return None


class ExitKioskDialog(_MessageMixin, QtWidgets.QDialog):
    """Ask for administrator credentials before leaving kiosk mode.

    Laid out like the kiosk login, and for the same reason: the kiosk window
    manager collapsed this dialog to a 30 px sliver and gave it no usable
    keyboard.  It sizes itself to the panel and carries its own keys.
    """

    def __init__(self, parent, auth: AuthManager, *, kiosk: bool = True):
        super().__init__(parent)
        self.auth = auth
        self.kiosk = kiosk
        self.authorised = False
        self.setWindowTitle("Exit kiosk mode")
        self.setModal(True)
        if kiosk:
            flags = QtCore.Qt.WindowType if hasattr(QtCore.Qt, "WindowType") else QtCore.Qt
            self.setWindowFlags(flags.FramelessWindowHint | flags.WindowStaysOnTopHint)

        outer = QtWidgets.QHBoxLayout(self)
        outer.addStretch(1)
        column = QtWidgets.QWidget()
        column.setMaximumWidth(560)
        outer.addWidget(column, 0)
        outer.addStretch(1)

        layout = QtWidgets.QVBoxLayout(column)
        layout.setSpacing(6 if kiosk else 10)

        header = QtWidgets.QLabel("Administrator sign-in required")
        header.setObjectName("Title")
        header.setAlignment(ALIGN_CENTER)
        header.setWordWrap(True)
        layout.addWidget(header)

        explain = QtWidgets.QLabel(
            "Leaving kiosk mode closes the station and shows the desktop."
        )
        explain.setObjectName("Subtle")
        explain.setAlignment(ALIGN_CENTER)
        explain.setWordWrap(True)
        layout.addWidget(explain)

        self.username = TouchLineEdit(
            placeholder="Administrator username", keyboard_button=not kiosk
        )
        layout.addWidget(self.username)
        self.password = TouchLineEdit(
            placeholder="Password", password=True, keyboard_button=not kiosk
        )
        layout.addWidget(self.password)

        self.message = QtWidgets.QLabel("")
        self.message.setStyleSheet("color: #c5221f; font-weight: 600;")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self._cancel)
        buttons.addWidget(cancel)
        confirm = QtWidgets.QPushButton("Exit kiosk")
        confirm.setObjectName("Danger")
        confirm.clicked.connect(self._check)
        buttons.addWidget(confirm, 2)
        layout.addLayout(buttons)

        self._fitted = True
        if kiosk:
            self.setStyleSheet(
                "QLineEdit { min-height: 30px; max-height: 40px; padding: 4px 8px; }"
                "QPushButton { min-height: 38px; max-height: 46px; padding: 4px 10px; }"
            )
            self.pad = KeyboardPad(
                column, key_height=keyboard_key_height(numeric=False, password=True)
            )
            self.pad.set_target(self.username.edit)
            layout.insertWidget(layout.count() - 1, self.pad)
            self.username.edit.installEventFilter(self)
            self.password.edit.installEventFilter(self)
            self.username.edit.setFocus()
            self._fitted = False

    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        if getattr(self, "pad", None) is not None and not self._fitted:
            self._fitted = True
            fit_with_keyboard(self, self.pad)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        focus_in = (
            QtCore.QEvent.Type.FocusIn if hasattr(QtCore.QEvent, "Type")
            else QtCore.QEvent.FocusIn
        )
        if event.type() == focus_in and getattr(self, "pad", None) is not None:
            self.pad.set_target(watched)
        return super().eventFilter(watched, event)

    def _cancel(self) -> None:
        self.authorised = False
        super().reject()

    def reject(self) -> None:
        """Escape backs out to the locked login, never past it.

        Cancelling the prompt is safe -- the station stays in kiosk mode -- but
        it must leave ``authorised`` False whichever way it was dismissed.
        """
        self._cancel()

    def _check(self) -> None:
        try:
            session = self.auth.login(self.username.text(), self.password.text())
        except AuthError as exc:
            self._say(str(exc))
            self.password.clear()
            return
        if not session.is_admin:
            # Audited: an operator trying to reach the desktop is worth seeing.
            self.auth.db.audit(session.username, "kiosk.exit_denied",
                               detail="not an administrator")
            self._say("Only an administrator can leave kiosk mode.")
            self.password.clear()
            return
        self.auth.db.audit(session.username, "kiosk.exit")
        self.authorised = True
        self.accept()

    @classmethod
    def authorise(cls, parent, auth: AuthManager, *, kiosk: bool = True) -> bool:
        dialog = cls(parent, auth, kiosk=kiosk)
        if kiosk:
            # Sized to the panel before it is shown: left to itself the kiosk
            # window manager collapsed this to a 30 px sliver.
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is not None:
                dialog.setGeometry(screen.geometry())
        exec_dialog(dialog)
        return dialog.authorised
