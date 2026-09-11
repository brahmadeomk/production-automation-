"""Administrator settings: projects, EEPROM maps, firmware locations, users,
serial counters, backup and the audit trail (SRS sections 6, 7, 15 and 17).

Every tab here is behind :meth:`Session.require_admin`, and every mutation is
written to ``AuditLog``.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional

from ..core.eeprom import EepromMap, legacy_map, map_for_project, recommended_map
from ..db.database import rows_to_dicts
from ..errors import StationError
from ..security.auth import ROLES
from ..timeutil import local as local_time
from .qt import QtCore, QtWidgets, exec_dialog
from .widgets import (
    Card, RecordTable, TouchLineEdit, confirm, dock_keyboard, notify,
)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class AdminScreen(QtWidgets.QWidget):
    projects_changed = QtCore.pyqtSignal()

    def __init__(self, app, session, parent=None):
        super().__init__(parent)
        self.app = app
        self.session = session
        session.require_admin()
        self._projects: List[Any] = []
        self._users: List[Any] = []
        self._networks: List[Any] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._projects_tab(), "Projects")
        tabs.addTab(self._users_tab(), "Users")
        # The System tab carries a long health list; on a 480 px panel it must
        # scroll rather than force the whole window taller than the screen.
        tabs.addTab(_scrollable(self._system_tab()), "System")
        tabs.addTab(_scrollable(self._identity_tab()), "Identity")
        tabs.addTab(self._wifi_tab(), "Wi-Fi")
        tabs.addTab(self._audit_tab(), "Audit log")
        layout.addWidget(tabs)

    # ------------------------------------------------------------- projects
    def _projects_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.project_table = RecordTable(
            (("ProjectName", "Project"), ("MCU", "MCU"), ("FirmwareVersion", "FW"),
             ("HwRevision", "HW"), ("ProductVariant", "Variant"),
             ("NextSerial", "Next serial"), ("Active", "Active"), ("Issues", "Status"))
        )
        layout.addWidget(self.project_table, 1)

        buttons = QtWidgets.QHBoxLayout()
        for label, slot, style in (
            ("New project", self._new_project, "Primary"),
            ("Edit", self._edit_project, ""),
            ("Set serial counter", self._set_serial, ""),
            ("Enable / disable", self._toggle_project, ""),
        ):
            button = QtWidgets.QPushButton(label)
            if style:
                button.setObjectName(style)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _selected_project(self):
        index = self.project_table.currentRow()
        if 0 <= index < len(self._projects):
            return self._projects[index]
        notify(self, "No selection", "Select a project first.")
        return None

    def _new_project(self) -> None:
        if ProjectDialog.edit(self, self.app, None, self.session.username):
            self.refresh()
            self.projects_changed.emit()

    def _edit_project(self) -> None:
        project = self._selected_project()
        if project and ProjectDialog.edit(self, self.app, project, self.session.username):
            self.refresh()
            self.projects_changed.emit()

    def _set_serial(self) -> None:
        project = self._selected_project()
        if not project:
            return
        current = self.app.db.next_serial_value(int(project["ProjectId"])) or 0
        value, ok = QtWidgets.QInputDialog.getInt(
            self,
            "Serial counter",
            f"Next serial number for '{project['ProjectName']}':",
            current,
            0,
            2_000_000_000,
        )
        if not ok or value == current:
            return
        if not confirm(
            self,
            "Confirm serial change",
            f"Change the next serial from {current} to {value}?\n\n"
            "This is recorded in the audit log.",
        ):
            return
        self.app.serials.set_next(
            int(project["ProjectId"]), value, actor=self.session.username
        )
        self.refresh()
        self.projects_changed.emit()

    def _toggle_project(self) -> None:
        project = self._selected_project()
        if not project:
            return
        active = not bool(project["Active"])
        self.app.db.set_project_active(int(project["ProjectId"]), active)
        self.app.db.audit(
            self.session.username,
            "project.activate" if active else "project.deactivate",
            project["ProjectName"],
        )
        self.refresh()
        self.projects_changed.emit()

    # ---------------------------------------------------------------- users
    def _users_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.user_table = RecordTable(
            (("Username", "Username"), ("FullName", "Full name"), ("Role", "Role"),
             ("Active", "Active"), ("LastLoginAt", "Last login"))
        )
        layout.addWidget(self.user_table, 1)

        buttons = QtWidgets.QHBoxLayout()
        for label, slot, style in (
            ("Add user", self._add_user, "Primary"),
            ("Reset password", self._reset_password, ""),
            ("Change role", self._change_role, ""),
            ("Enable / disable", self._toggle_user, ""),
        ):
            button = QtWidgets.QPushButton(label)
            if style:
                button.setObjectName(style)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _selected_user(self):
        index = self.user_table.currentRow()
        if 0 <= index < len(self._users):
            return self._users[index]
        notify(self, "No selection", "Select a user first.")
        return None

    def _add_user(self) -> None:
        if UserDialog.create(self, self.app, self.session.username):
            self.refresh()

    def _reset_password(self) -> None:
        user = self._selected_user()
        if not user:
            return
        password = _ask_password(self, f"New password for '{user['Username']}'")
        if password is None:
            return
        try:
            self.app.auth.set_password(
                user["Username"], password, actor=self.session.username
            )
        except StationError as exc:
            notify(self, "Could not set the password", str(exc), error=True)
            return
        notify(self, "Password updated", f"Password changed for '{user['Username']}'.")
        self.refresh()

    def _change_role(self) -> None:
        user = self._selected_user()
        if not user:
            return
        role, ok = QtWidgets.QInputDialog.getItem(
            self, "Change role", f"Role for '{user['Username']}':",
            list(ROLES), list(ROLES).index(user["Role"]), False,
        )
        if not ok or role == user["Role"]:
            return
        try:
            self.app.auth.set_role(user["Username"], role, actor=self.session.username)
        except StationError as exc:
            notify(self, "Could not change the role", str(exc), error=True)
            return
        self.refresh()

    def _toggle_user(self) -> None:
        user = self._selected_user()
        if not user:
            return
        try:
            self.app.auth.set_active(
                user["Username"], not bool(user["Active"]), actor=self.session.username
            )
        except StationError as exc:
            notify(self, "Could not change the account", str(exc), error=True)
            return
        self.refresh()

    # --------------------------------------------------------------- system
    # ------------------------------------------------------------- identity
    def _identity_tab(self) -> QtWidgets.QWidget:
        """Which physical station this is, and how it is on the network.

        An engineer holding a production log needs to tie it back to a box on
        a bench, so the station id sits next to the board serial and the MAC.
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        summary = Card()
        form = QtWidgets.QFormLayout(summary)
        self.identity_labels: Dict[str, QtWidgets.QLabel] = {}
        for key, caption in (
            ("station_id", "Device ID"),
            ("ssid", "Connected to (SSID)"),
            ("primary_mac", "MAC address"),
            ("hostname", "Hostname"),
            ("model", "Board"),
            ("board_serial", "Board serial"),
            ("clock", "Clock"),
        ):
            label = QtWidgets.QLabel("")
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                if hasattr(QtCore.Qt, "TextInteractionFlag")
                else QtCore.Qt.TextSelectableByMouse
            )
            self.identity_labels[key] = label
            form.addRow(caption, label)
        layout.addWidget(summary)

        self.interface_table = RecordTable(
            (("name", "Interface"), ("kind", "Type"), ("mac", "MAC"),
             ("ipv4", "IPv4"), ("state", "State"), ("ssid", "SSID"))
        )
        # A station has two or three interfaces, so this is sized to its rows.
        # Left to stretch it eats the height the network list needs, and on a
        # 600 px panel the Wi-Fi section falls below the fold.
        self.interface_table.setMaximumHeight(150)
        layout.addWidget(self.interface_table, 0)

        buttons = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self.refresh_identity)
        buttons.addWidget(refresh)
        sync = QtWidgets.QPushButton("Sync clock now")
        sync.clicked.connect(self._sync_clock)
        buttons.addWidget(sync)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _sync_clock(self) -> None:
        """Chase the clock now rather than waiting for the daily pass."""
        QtWidgets.QApplication.setOverrideCursor(
            QtCore.Qt.CursorShape.WaitCursor if hasattr(QtCore.Qt, "CursorShape")
            else QtCore.Qt.WaitCursor
        )
        try:
            reading = self.app.clock.sync()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.refresh_identity()
        self.app.db.audit(
            self.session.username, "time.sync",
            reading.server or reading.source, reading.detail,
        )
        notify(
            self, "Clock" if reading.ok else "Clock not corrected",
            reading.detail, error=not reading.ok,
        )

    # ------------------------------------------------------------------ wi-fi
    def _wifi_tab(self) -> QtWidgets.QWidget:
        """Its own page: the identity card and two tables do not fit 600 px."""
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        self.wifi_table = RecordTable(
            (("in_use", ""), ("ssid", "Network"), ("bars", "Signal"),
             ("security", "Security"))
        )
        self.wifi_table.doubleClicked.connect(self._join_wifi)
        layout.addWidget(self.wifi_table, 1)

        self.wifi_message = QtWidgets.QLabel(
            "Press Scan to list the networks in range."
        )
        self.wifi_message.setObjectName("Subtle")
        self.wifi_message.setWordWrap(True)
        layout.addWidget(self.wifi_message)

        buttons = QtWidgets.QHBoxLayout()
        self.wifi_scan_button = QtWidgets.QPushButton("Scan for networks")
        self.wifi_scan_button.clicked.connect(self.scan_wifi)
        buttons.addWidget(self.wifi_scan_button)
        self.wifi_join_button = QtWidgets.QPushButton("Connect")
        self.wifi_join_button.setObjectName("Primary")
        self.wifi_join_button.setEnabled(False)
        self.wifi_join_button.clicked.connect(self._join_wifi)
        buttons.addWidget(self.wifi_join_button, 2)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    # ------------------------------------------------------------------ wi-fi
    def scan_wifi(self) -> None:
        from ..hw import wifi

        if not wifi.available():
            self._networks = []
            self.wifi_table.load([])
            self.wifi_message.setText(
                "NetworkManager (nmcli) is not installed, so this station "
                "cannot be moved to another network from the panel."
            )
            self.wifi_join_button.setEnabled(False)
            return

        self.wifi_message.setText("Scanning...")
        self.wifi_scan_button.setEnabled(False)
        QtWidgets.QApplication.processEvents()
        try:
            self._networks = wifi.scan()
        finally:
            self.wifi_scan_button.setEnabled(True)

        self.wifi_table.load([
            {
                "in_use": "✓" if network.in_use else "",
                "ssid": network.ssid,
                "bars": network.bars,
                "security": network.security or "open",
            }
            for network in self._networks
        ])
        self.wifi_join_button.setEnabled(bool(self._networks))
        self.wifi_message.setText(
            f"{len(self._networks)} network(s) in range. Select one and press "
            f"Connect." if self._networks else
            "No networks found. The station may have no wireless interface."
        )

    def _selected_network(self):
        index = self.wifi_table.currentRow()
        if 0 <= index < len(getattr(self, "_networks", [])):
            return self._networks[index]
        notify(self, "No selection", "Select a network first.")
        return None

    def _join_wifi(self) -> None:
        network = self._selected_network()
        if network is None:
            return
        if not WifiPasswordDialog.join(self, self.app, network, self.session.username):
            return
        self.scan_wifi()
        self.refresh_identity()

    def refresh_identity(self) -> None:
        from ..hw.identity import gather

        identity = gather(self.app.config.station_id)
        for key, label in self.identity_labels.items():
            label.setText(str(getattr(identity, key, "")))
        self.identity_labels["clock"].setText(
            f"{local_time(_utc_now())}  -  {self.app.time_status()}"
        )
        self.interface_table.load([
            {
                "name": interface.name,
                "kind": "Wi-Fi" if interface.wireless else "wired",
                "mac": interface.mac,
                "ipv4": interface.ipv4,
                "state": interface.state,
                # Blank rather than "unavailable" on a wired row, where the
                # column simply does not apply.
                "ssid": interface.ssid if interface.wireless else "",
            }
            for interface in identity.interfaces
        ])

    def _system_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        info = Card()
        form = QtWidgets.QFormLayout(info)
        self.health_labels: Dict[str, QtWidgets.QLabel] = {}
        for key in (
            "station_id", "config", "database", "avrdude", "avrdude_version",
            "gpio_backend", "simulated", "projects", "users", "backup",
        ):
            label = QtWidgets.QLabel("")
            label.setWordWrap(True)
            self.health_labels[key] = label
            form.addRow(key.replace("_", " ").title(), label)
        layout.addWidget(info)

        buttons = QtWidgets.QHBoxLayout()
        backup_now = QtWidgets.QPushButton("Run backup now")
        backup_now.setObjectName("Primary")
        backup_now.clicked.connect(self._run_backup)
        buttons.addWidget(backup_now)
        selftest = QtWidgets.QPushButton("Run self-test")
        selftest.clicked.connect(self._run_selftest)
        buttons.addWidget(selftest)
        panel = QtWidgets.QPushButton("Test LEDs and buzzer")
        panel.clicked.connect(self._test_panel)
        buttons.addWidget(panel)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.system_output = QtWidgets.QPlainTextEdit()
        self.system_output.setReadOnly(True)
        layout.addWidget(self.system_output, 1)
        return page

    def _run_backup(self) -> None:
        self.system_output.appendPlainText("Running backup...")
        QtWidgets.QApplication.processEvents()
        result = self.app.backup.run_backup()
        self.system_output.appendPlainText(
            f"Backup {'OK' if result.ok else 'FAILED'}: "
            f"{result.detail} ({result.bytes_copied} bytes -> {result.destination})"
        )
        self.refresh()

    def _run_selftest(self) -> None:
        from ..selftest import run_selftest

        self.system_output.appendPlainText("Running self-test...")
        QtWidgets.QApplication.processEvents()
        report = run_selftest(self.app)
        for check in report["checks"]:
            self.system_output.appendPlainText(
                f"[{'PASS' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}"
            )
        self.system_output.appendPlainText(
            f"{report['passed']}/{report['total']} checks passed"
        )

    def _test_panel(self) -> None:
        if not self.app.io:
            notify(self, "No panel", "This process has no panel hardware attached.")
            return
        self.app.io.green(True)
        QtCore.QThread.msleep(400)
        self.app.io.green(False)
        self.app.io.red(True)
        QtCore.QThread.msleep(400)
        self.app.io.red(False)
        self.app.io.beep(0.15, count=2)
        fixture = "closed" if self.app.io.fixture_present() else "open"
        self.system_output.appendPlainText(
            f"Panel test done. Fixture detect reads: {fixture}."
        )

    # ------------------------------------------------------------ audit log
    def _audit_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.audit_table = RecordTable(
            (("Timestamp", "Time"), ("Username", "User"), ("Action", "Action"),
             ("Target", "Target"), ("Detail", "Detail"))
        )
        layout.addWidget(self.audit_table, 1)
        reload_button = QtWidgets.QPushButton("Reload")
        reload_button.clicked.connect(self.refresh)
        layout.addWidget(reload_button)
        return page

    # -------------------------------------------------------------- refresh
    def refresh(self) -> None:
        self._projects = list(self.app.db.list_projects(include_inactive=True))
        rows = []
        for project in self._projects:
            row = dict(project)
            row["NextSerial"] = self.app.db.next_serial_value(int(project["ProjectId"]))
            row["Active"] = "yes" if project["Active"] else "no"
            row["Issues"] = "; ".join(self.app.engine.validate_project(project)) or "ready"
            rows.append(row)
        self.project_table.load(rows)

        self._users = list(self.app.db.list_users())
        self.user_table.load(
            [
                {**dict(u), "Active": "yes" if u["Active"] else "no",
                 "LastLoginAt": local_time(u["LastLoginAt"])}
                for u in self._users
            ]
        )

        for key, value in self.app.health().items():
            if key in self.health_labels:
                self.health_labels[key].setText(str(value))

        self.audit_table.load(
            [
                {**dict(r), "Timestamp": local_time(r["Timestamp"])}
                for r in self.app.db.list_audit(300)
            ]
        )

        self.refresh_identity()


def _heading(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("Title")
    return label


class WifiPasswordDialog(QtWidgets.QDialog):
    """Ask for a network password and join, without blocking the panel.

    Joining takes several seconds and NetworkManager can sit on the request
    far longer, so the attempt runs on a worker thread; a frozen touchscreen
    reads as a crashed station.
    """

    def __init__(self, parent, app, network, actor: str):
        super().__init__(parent)
        self.app = app
        self.network = network
        self.actor = actor
        self.joined = False
        self._worker = None
        self.setWindowTitle("Connect to Wi-Fi")
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)
        header = QtWidgets.QLabel(f"Connect to '{network.ssid}'")
        header.setObjectName("Title")
        header.setWordWrap(True)
        layout.addWidget(header)

        if network.open:
            note = QtWidgets.QLabel(
                "This network is unsecured. Anything the station sends over it, "
                "including its backups, can be read by others in range."
            )
            note.setObjectName("Subtle")
            note.setWordWrap(True)
            layout.addWidget(note)

        self.password = TouchLineEdit(placeholder="Network password", password=True)
        if not network.open:
            layout.addWidget(self.password)
            self.reveal = QtWidgets.QCheckBox("Show characters")
            self.reveal.toggled.connect(self._set_reveal)
            layout.addWidget(self.reveal)
        else:
            self.password.setVisible(False)

        self.message = QtWidgets.QLabel("")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)

        buttons = QtWidgets.QHBoxLayout()
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self._attempt)
        buttons.addWidget(self.connect_button, 2)
        layout.addLayout(buttons)

        dock_keyboard(self)

    def _set_reveal(self, shown: bool) -> None:
        from .qt import ECHO_PASSWORD

        normal = (QtWidgets.QLineEdit.EchoMode.Normal
                  if hasattr(QtWidgets.QLineEdit, "EchoMode")
                  else QtWidgets.QLineEdit.Normal)
        self.password.edit.setEchoMode(normal if shown else ECHO_PASSWORD)

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.password.setEnabled(not busy)

    def _attempt(self) -> None:
        if not self.network.open and not self.password.text():
            self._say("Enter the network password.")
            return
        self._busy(True)
        self._say(f"Connecting to {self.network.ssid}...")
        QtWidgets.QApplication.processEvents()

        self._worker = _WifiWorker(self.network.ssid, self.password.text())
        self._worker.done.connect(self._finished)
        self._worker.start()

    def _finished(self, ok: bool, detail: str) -> None:
        self._busy(False)
        if ok:
            self.joined = True
            # The network the station is on is worth an audit entry; the
            # password it was given is not, and is never recorded anywhere.
            self.app.db.audit(
                self.actor, "network.wifi_connect", self.network.ssid, ""
            )
            self.accept()
            return
        self.app.db.audit(
            self.actor, "network.wifi_failed", self.network.ssid, detail
        )
        self._say(detail)

    @classmethod
    def join(cls, parent, app, network, actor: str) -> bool:
        dialog = cls(parent, app, network, actor)
        exec_dialog(dialog)
        return dialog.joined


class _WifiWorker(QtCore.QThread):
    """Runs the join off the UI thread so the panel keeps repainting."""

    done = QtCore.pyqtSignal(bool, str)

    def __init__(self, ssid: str, password: str):
        super().__init__()
        self._ssid = ssid
        self._password = password

    def run(self) -> None:  # pragma: no cover - exercised through the dialog
        from ..hw import wifi

        result = wifi.connect(self._ssid, self._password)
        # Drop the password as soon as it has been handed over.
        self._password = ""
        self.done.emit(result.ok, result.detail)


def _scrollable(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    """Wrap a tall page so it scrolls on a small panel."""
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(
        QtWidgets.QFrame.Shape.NoFrame if hasattr(QtWidgets.QFrame, "Shape")
        else QtWidgets.QFrame.NoFrame
    )
    area.setWidget(widget)
    return area


def _ask_password(parent, title: str) -> Optional[str]:
    from .qt import ECHO_PASSWORD

    first, ok = QtWidgets.QInputDialog.getText(parent, title, "Password:", ECHO_PASSWORD)
    if not ok:
        return None
    second, ok = QtWidgets.QInputDialog.getText(parent, title, "Repeat password:", ECHO_PASSWORD)
    if not ok:
        return None
    if first != second:
        notify(parent, "Passwords do not match", "Try again.", error=True)
        return None
    return first


class UserDialog(QtWidgets.QDialog):
    def __init__(self, parent, app, actor: str):
        super().__init__(parent)
        self.app = app
        self.actor = actor
        self.setWindowTitle("Add user")
        self.setMinimumWidth(420)
        layout = QtWidgets.QFormLayout(self)

        self.username = TouchLineEdit(placeholder="Username")
        self.full_name = TouchLineEdit(placeholder="Full name")
        self.role = QtWidgets.QComboBox()
        self.role.addItems(list(ROLES))
        self.password = TouchLineEdit(placeholder="Password", password=True)
        self.repeat = TouchLineEdit(placeholder="Repeat password", password=True)
        layout.addRow("Username", self.username)
        layout.addRow("Full name", self.full_name)
        layout.addRow("Role", self.role)
        layout.addRow("Password", self.password)
        layout.addRow("Repeat", self.repeat)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QtWidgets.QPushButton("Create")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addRow(buttons)

        dock_keyboard(self)

    def _save(self) -> None:
        if self.password.text() != self.repeat.text():
            notify(self, "Passwords do not match", "Try again.", error=True)
            return
        try:
            self.app.auth.create_user(
                self.username.text(),
                self.password.text(),
                self.role.currentText(),
                full_name=self.full_name.text(),
                actor=self.actor,
            )
        except StationError as exc:
            notify(self, "Could not create the user", str(exc), error=True)
            return
        self.accept()

    @classmethod
    def create(cls, parent, app, actor: str) -> bool:
        return bool(exec_dialog(cls(parent, app, actor)))


class ProjectDialog(QtWidgets.QDialog):
    """Create or edit a project, including its EEPROM map (SRS sections 7-8)."""

    def __init__(self, parent, app, project, actor: str):
        super().__init__(parent)
        self.app = app
        self.project = project
        self.actor = actor
        self.setWindowTitle("Edit project" if project else "New project")
        self.setMinimumSize(760, 560)

        outer = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._serial_tab(), "Serial numbers")
        tabs.addTab(self._eeprom_tab(), "EEPROM map")
        outer.addWidget(tabs, 1)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        preview = QtWidgets.QPushButton("Preview EEPROM block")
        preview.clicked.connect(self._preview)
        buttons.addWidget(preview)
        save = QtWidgets.QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        outer.addLayout(buttons)

        if project:
            self._load(project)

        dock_keyboard(self)

    # ------------------------------------------------------------ tab: general
    def _general_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        self.name = TouchLineEdit(placeholder="Project name")
        self.mcu = TouchLineEdit(placeholder="avrdude part id, e.g. atmega328p")
        self.signature = TouchLineEdit(placeholder="0x1e950f (optional)")

        hex_row = QtWidgets.QHBoxLayout()
        self.hex_path = TouchLineEdit(placeholder="/opt/firmware/product.hex")
        hex_row.addWidget(self.hex_path, 1)
        browse = QtWidgets.QPushButton("Browse")
        browse.clicked.connect(self._browse_hex)
        hex_row.addWidget(browse)

        self.hw_revision = TouchLineEdit(placeholder="RevC")
        self.variant = TouchLineEdit(placeholder="TS-100")
        self.firmware_version = TouchLineEdit(placeholder="1.4.0")
        self.fuse_low = TouchLineEdit(placeholder="0xFF (optional)")
        self.fuse_high = TouchLineEdit(placeholder="0xD9 (optional)")
        self.fuse_extended = TouchLineEdit(placeholder="0xFD (optional)")
        self.lock_byte = TouchLineEdit(placeholder="0xFF (optional)")

        form.addRow("Project name", self.name)
        form.addRow("MCU", self.mcu)
        form.addRow("Expected signature", self.signature)
        form.addRow("Firmware .hex", hex_row)
        form.addRow("HW revision", self.hw_revision)
        form.addRow("Product variant", self.variant)
        form.addRow("Firmware version", self.firmware_version)
        form.addRow("Fuse low", self.fuse_low)
        form.addRow("Fuse high", self.fuse_high)
        form.addRow("Fuse extended", self.fuse_extended)
        form.addRow("Lock byte", self.lock_byte)
        return page

    def _browse_hex(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select firmware", self.hex_path.text() or "/", "Intel HEX (*.hex);;All files (*)"
        )
        if path:
            self.hex_path.setText(path)

    # ------------------------------------------------------------- tab: serial
    def _serial_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        self.serial_prefix = TouchLineEdit(placeholder="optional prefix, e.g. TS")
        self.serial_start = QtWidgets.QSpinBox()
        self.serial_start.setRange(0, 2_000_000_000)
        self.serial_start.setValue(1)
        self.serial_end = QtWidgets.QSpinBox()
        self.serial_end.setRange(0, 2_000_000_000)
        self.serial_end.setValue(999_999)
        self.serial_digits = QtWidgets.QSpinBox()
        self.serial_digits.setRange(1, 18)
        self.serial_digits.setValue(6)
        form.addRow("Prefix", self.serial_prefix)
        form.addRow("Range start", self.serial_start)
        form.addRow("Range end (0 = unlimited)", self.serial_end)
        form.addRow("Digits", self.serial_digits)

        note = QtWidgets.QLabel(
            "The counter is stored per project and only advances after a PASS.\n"
            "Changing a live counter is done from the Projects tab and is audited."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        form.addRow(note)
        return page

    # ------------------------------------------------------------- tab: eeprom
    def _eeprom_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        mode_row = QtWidgets.QHBoxLayout()
        self.legacy_mode = QtWidgets.QCheckBox("Legacy layout (serial + date only)")
        self.legacy_mode.stateChanged.connect(self._mode_changed)
        mode_row.addWidget(self.legacy_mode)
        mode_row.addStretch(1)
        use_recommended = QtWidgets.QPushButton("Load recommended 32-byte block")
        use_recommended.clicked.connect(
            lambda: self.eeprom_json.setPlainText(recommended_map().to_json())
        )
        mode_row.addWidget(use_recommended)
        layout.addLayout(mode_row)

        legacy_form = QtWidgets.QFormLayout()
        self.sn_address = QtWidgets.QSpinBox()
        self.sn_address.setRange(0, 65535)
        self.mfg_address = QtWidgets.QSpinBox()
        self.mfg_address.setRange(0, 65535)
        self.mfg_address.setValue(8)
        legacy_form.addRow("Serial number address", self.sn_address)
        legacy_form.addRow("Manufacturing date address", self.mfg_address)
        self.legacy_widget = QtWidgets.QWidget()
        self.legacy_widget.setLayout(legacy_form)
        layout.addWidget(self.legacy_widget)

        layout.addWidget(QtWidgets.QLabel("EEPROM map (JSON)"))
        self.eeprom_json = QtWidgets.QPlainTextEdit()
        self.eeprom_json.setPlainText(recommended_map().to_json())
        layout.addWidget(self.eeprom_json, 1)

        self.preview_label = QtWidgets.QLabel("")
        self.preview_label.setObjectName("Subtle")
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            if hasattr(QtCore.Qt, "TextInteractionFlag")
            else QtCore.Qt.TextSelectableByMouse
        )
        layout.addWidget(self.preview_label)
        self._mode_changed()
        return page

    def _mode_changed(self) -> None:
        legacy = self.legacy_mode.isChecked()
        self.legacy_widget.setVisible(legacy)
        self.eeprom_json.setVisible(not legacy)

    def _current_map(self) -> EepromMap:
        if self.legacy_mode.isChecked():
            return legacy_map(self.sn_address.value(), self.mfg_address.value())
        return EepromMap.from_json(self.eeprom_json.toPlainText())

    def _preview(self) -> None:
        try:
            eeprom = self._current_map()
            context = {
                "serial": self.serial_start.value(),
                "serial_text": f"{self.serial_prefix.text()}"
                               f"{self.serial_start.value():0{self.serial_digits.value()}d}",
                "hw_revision": self.hw_revision.text(),
                "product_variant": self.variant.text(),
                "firmware_version": self.firmware_version.text(),
                "mfg_date": date.today(),
                "operator": self.actor,
                "station_id": self.app.config.station_id,
                "project_name": self.name.text(),
            }
            block = eeprom.build(context)
            lines = [
                f"Base 0x{eeprom.base_address:04X}, {eeprom.size} bytes",
                block.hex().upper(),
            ]
            for field in eeprom.describe(context):
                lines.append(
                    f"  0x{field['offset']:04X} +{field['length']:<2}"
                    f" {field['name']:<18} {field['hex']:<18} {field['text']}"
                )
            self.preview_label.setText("\n".join(lines))
        except StationError as exc:
            notify(self, "Invalid EEPROM map", str(exc), error=True)

    # ---------------------------------------------------------------- load/save
    def _load(self, project) -> None:
        self.name.setText(project["ProjectName"])
        self.mcu.setText(project["MCU"])
        self.signature.setText(project["Signature"] or "")
        self.hex_path.setText(project["HexPath"])
        self.hw_revision.setText(project["HwRevision"] or "")
        self.variant.setText(project["ProductVariant"] or "")
        self.firmware_version.setText(project["FirmwareVersion"] or "")
        for widget, key in (
            (self.fuse_low, "FuseLow"), (self.fuse_high, "FuseHigh"),
            (self.fuse_extended, "FuseExtended"), (self.lock_byte, "LockByte"),
        ):
            widget.setText(project[key] or "")
        self.serial_prefix.setText(project["SerialPrefix"] or "")
        self.serial_start.setValue(int(project["SerialStart"]))
        self.serial_end.setValue(int(project["SerialEnd"]))
        self.serial_digits.setValue(int(project["SerialDigits"] or 6))
        self.sn_address.setValue(int(project["SNAddress"]))
        self.mfg_address.setValue(int(project["MFGAddress"]))
        if project["EepromMap"]:
            self.legacy_mode.setChecked(False)
            self.eeprom_json.setPlainText(
                json.dumps(json.loads(project["EepromMap"]), indent=2)
            )
        else:
            self.legacy_mode.setChecked(True)
        self._mode_changed()

    def _save(self) -> None:
        if not self.name.text().strip():
            notify(self, "Missing name", "A project needs a name.", error=True)
            return
        if not self.mcu.text().strip():
            notify(self, "Missing MCU", "Set the avrdude part id.", error=True)
            return
        legacy = self.legacy_mode.isChecked()
        try:
            eeprom = self._current_map()
        except StationError as exc:
            notify(self, "Invalid EEPROM map", str(exc), error=True)
            return

        values = {
            "ProjectName": self.name.text().strip(),
            "MCU": self.mcu.text().strip(),
            "HexPath": self.hex_path.text().strip(),
            "Signature": self.signature.text().strip(),
            "SNAddress": self.sn_address.value(),
            "MFGAddress": self.mfg_address.value(),
            # A legacy project stores no map, which is what tells the engine to
            # synthesise one from the two addresses.
            "EepromMap": None if legacy else eeprom.to_dict(),
            "HwRevision": self.hw_revision.text().strip(),
            "ProductVariant": self.variant.text().strip(),
            "FirmwareVersion": self.firmware_version.text().strip(),
            "SerialPrefix": self.serial_prefix.text().strip(),
            "SerialStart": self.serial_start.value(),
            "SerialEnd": self.serial_end.value(),
            "SerialDigits": self.serial_digits.value(),
            "FuseLow": self.fuse_low.text().strip() or None,
            "FuseHigh": self.fuse_high.text().strip() or None,
            "FuseExtended": self.fuse_extended.text().strip() or None,
            "LockByte": self.lock_byte.text().strip() or None,
        }
        try:
            project_id = self.app.db.upsert_project(
                values,
                project_id=int(self.project["ProjectId"]) if self.project else None,
            )
        except StationError as exc:
            notify(self, "Could not save the project", str(exc), error=True)
            return
        self.app.db.audit(
            self.actor,
            "project.update" if self.project else "project.create",
            values["ProjectName"],
        )
        problems = self.app.engine.validate_project(self.app.db.get_project(project_id))
        if problems:
            notify(
                self,
                "Saved with warnings",
                "The project was saved but is not ready to run:\n\n" + "\n".join(problems),
            )
        self.accept()

    @classmethod
    def edit(cls, parent, app, project, actor: str) -> bool:
        return bool(exec_dialog(cls(parent, app, project, actor)))
