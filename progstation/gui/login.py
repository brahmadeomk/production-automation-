"""Login dialog and forced first-password change (SRS sections 6 and 17)."""

from __future__ import annotations

from typing import Optional

from ..errors import AuthError
from ..security.auth import AuthManager, Session
from .qt import ALIGN_CENTER, QtCore, QtWidgets, exec_dialog
from .widgets import TouchLineEdit


class LoginDialog(QtWidgets.QDialog):
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

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Sign in")
        title.setObjectName("Title")
        title.setAlignment(ALIGN_CENTER)
        layout.addWidget(title)

        if station_id:
            station = QtWidgets.QLabel(station_id)
            station.setObjectName("Subtle")
            station.setAlignment(ALIGN_CENTER)
            layout.addWidget(station)

        layout.addWidget(QtWidgets.QLabel("Username"))
        self.username = TouchLineEdit(placeholder="Username")
        layout.addWidget(self.username)

        layout.addWidget(QtWidgets.QLabel("Password"))
        self.password = TouchLineEdit(placeholder="Password", password=True)
        layout.addWidget(self.password)

        self.message = QtWidgets.QLabel("")
        self.message.setStyleSheet("color: #c5221f; font-weight: 600;")
        self.message.setWordWrap(True)
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

        self.password.edit.returnPressed.connect(self._attempt)
        self.username.edit.returnPressed.connect(self.password.edit.setFocus)

    def _attempt(self) -> None:
        try:
            session = self.auth.login(self.username.text(), self.password.text())
        except AuthError as exc:
            self.message.setText(str(exc))
            self.password.clear()
            return
        if session.must_change_password:
            new_password = ChangePasswordDialog.ask(self, self.auth, session.username)
            if not new_password:
                self.message.setText("A password change is required before you can continue.")
                return
            session = self.auth.login(session.username, new_password)
        self.session = session
        self.accept()

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
        if ExitKioskDialog.authorise(self, self.auth):
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


class ChangePasswordDialog(QtWidgets.QDialog):
    def __init__(self, parent, auth: AuthManager, username: str):
        super().__init__(parent)
        self.auth = auth
        self.username = username
        self.new_password: Optional[str] = None
        self.setWindowTitle("Change Password")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        header = QtWidgets.QLabel(f"Set a new password for '{username}'")
        header.setObjectName("Title")
        header.setWordWrap(True)
        layout.addWidget(header)

        layout.addWidget(QtWidgets.QLabel("New password"))
        self.first = TouchLineEdit(placeholder="New password", password=True)
        layout.addWidget(self.first)
        layout.addWidget(QtWidgets.QLabel("Repeat password"))
        self.second = TouchLineEdit(placeholder="Repeat password", password=True)
        layout.addWidget(self.second)

        self.message = QtWidgets.QLabel("")
        self.message.setStyleSheet("color: #c5221f; font-weight: 600;")
        self.message.setWordWrap(True)
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

    def _save(self) -> None:
        if self.first.text() != self.second.text():
            self.message.setText("The two passwords do not match.")
            return
        try:
            self.auth.set_password(self.username, self.first.text(), actor=self.username)
        except AuthError as exc:
            self.message.setText(str(exc))
            return
        self.new_password = self.first.text()
        self.accept()

    @classmethod
    def ask(cls, parent, auth: AuthManager, username: str) -> Optional[str]:
        dialog = cls(parent, auth, username)
        if exec_dialog(dialog):
            return dialog.new_password
        return None


class ExitKioskDialog(QtWidgets.QDialog):
    """Ask for administrator credentials before leaving kiosk mode."""

    def __init__(self, parent, auth: AuthManager):
        super().__init__(parent)
        self.auth = auth
        self.authorised = False
        self.setWindowTitle("Exit kiosk mode")
        self.setModal(True)
        self.setMinimumWidth(420)
        flags = QtCore.Qt.WindowType if hasattr(QtCore.Qt, "WindowType") else QtCore.Qt
        self.setWindowFlags(flags.FramelessWindowHint | flags.WindowStaysOnTopHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        header = QtWidgets.QLabel("Administrator sign-in required")
        header.setObjectName("Title")
        header.setWordWrap(True)
        layout.addWidget(header)

        explain = QtWidgets.QLabel(
            "Leaving kiosk mode closes the station and shows the desktop."
        )
        explain.setObjectName("Subtle")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        layout.addWidget(QtWidgets.QLabel("Username"))
        self.username = TouchLineEdit(placeholder="Administrator username")
        layout.addWidget(self.username)
        layout.addWidget(QtWidgets.QLabel("Password"))
        self.password = TouchLineEdit(placeholder="Password", password=True)
        layout.addWidget(self.password)

        self.message = QtWidgets.QLabel("")
        self.message.setStyleSheet("color: #c5221f; font-weight: 600;")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(super().reject)
        buttons.addWidget(cancel)
        confirm = QtWidgets.QPushButton("Exit kiosk")
        confirm.setObjectName("Danger")
        confirm.clicked.connect(self._check)
        buttons.addWidget(confirm, 2)
        layout.addLayout(buttons)

    def _check(self) -> None:
        try:
            session = self.auth.login(self.username.text(), self.password.text())
        except AuthError as exc:
            self.message.setText(str(exc))
            self.password.clear()
            return
        if not session.is_admin:
            # Audited: an operator trying to reach the desktop is worth seeing.
            self.auth.db.audit(session.username, "kiosk.exit_denied",
                               detail="not an administrator")
            self.message.setText("Only an administrator can leave kiosk mode.")
            self.password.clear()
            return
        self.auth.db.audit(session.username, "kiosk.exit")
        self.authorised = True
        self.accept()

    @classmethod
    def authorise(cls, parent, auth: AuthManager) -> bool:
        dialog = cls(parent, auth)
        exec_dialog(dialog)
        return dialog.authorised
