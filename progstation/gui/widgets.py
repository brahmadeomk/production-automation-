"""Shared touch widgets: on-screen keypad, tables and confirmation helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from .qt import (
    ALIGN_CENTER,
    ECHO_PASSWORD,
    NO_EDIT,
    RESIZE_CONTENTS,
    SELECT_ROWS,
    SINGLE_SELECTION,
    STRETCH,
    QtCore,
    QtWidgets,
    exec_dialog,
)


class Card(QtWidgets.QFrame):
    """A white rounded panel used throughout the screens."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")


class TouchKeyboard(QtWidgets.QDialog):
    """On-screen keyboard so the station needs no physical keyboard.

    ``numeric`` mode shows a keypad, which is what operators use for serial and
    quantity entry; the full layout is only needed on the admin screens.
    """

    _ROWS_ALPHA = ("1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM.-_")
    _ROWS_NUMERIC = ("789", "456", "123", "0.-")

    def __init__(self, parent=None, *, title: str = "Enter value", text: str = "",
                 numeric: bool = False, password: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(QtWidgets.QLabel(title))

        self.edit = QtWidgets.QLineEdit(text)
        if password:
            self.edit.setEchoMode(ECHO_PASSWORD)
        layout.addWidget(self.edit)

        self._shift = False
        keys = QtWidgets.QVBoxLayout()
        keys.setSpacing(6)
        for row in (self._ROWS_NUMERIC if numeric else self._ROWS_ALPHA):
            line = QtWidgets.QHBoxLayout()
            line.setSpacing(6)
            for char in row:
                button = QtWidgets.QPushButton(char)
                button.setMinimumSize(56, 52)
                button.clicked.connect(lambda _=False, c=char: self._type(c))
                line.addWidget(button)
            keys.addLayout(line)
        layout.addLayout(keys)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(6)
        if not numeric:
            space = QtWidgets.QPushButton("Space")
            space.clicked.connect(lambda: self._type(" "))
            controls.addWidget(space, 2)
        backspace = QtWidgets.QPushButton("⌫ Back")
        backspace.clicked.connect(self._backspace)
        controls.addWidget(backspace)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self.edit.clear)
        controls.addWidget(clear)
        layout.addLayout(controls)

        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("OK")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def _type(self, char: str) -> None:
        self.edit.setText(self.edit.text() + char)

    def _backspace(self) -> None:
        self.edit.setText(self.edit.text()[:-1])

    def value(self) -> str:
        return self.edit.text()

    @classmethod
    def ask(cls, parent, title: str, text: str = "", *, numeric: bool = False,
            password: bool = False) -> Optional[str]:
        dialog = cls(parent, title=title, text=text, numeric=numeric, password=password)
        if exec_dialog(dialog):
            return dialog.value()
        return None


class TouchLineEdit(QtWidgets.QWidget):
    """A line edit with a keypad button next to it."""

    def __init__(self, parent=None, *, placeholder: str = "", numeric: bool = False,
                 password: bool = False):
        super().__init__(parent)
        self._numeric = numeric
        self._password = password
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        if password:
            self.edit.setEchoMode(ECHO_PASSWORD)
        layout.addWidget(self.edit, 1)
        button = QtWidgets.QPushButton("⌨")
        button.setFixedWidth(56)
        button.setToolTip("On-screen keyboard")
        button.clicked.connect(self._open_keyboard)
        layout.addWidget(button)

    def _open_keyboard(self) -> None:
        value = TouchKeyboard.ask(
            self,
            self.edit.placeholderText() or "Enter value",
            self.edit.text(),
            numeric=self._numeric,
            password=self._password,
        )
        if value is not None:
            self.edit.setText(value)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:
        self.edit.setText(value)

    def clear(self) -> None:
        self.edit.clear()


class RecordTable(QtWidgets.QTableWidget):
    """Read-only, row-selectable table sized for touch scrolling."""

    def __init__(self, columns: Sequence[tuple[str, str]], parent=None):
        super().__init__(0, len(columns), parent)
        self._columns = list(columns)
        self.setHorizontalHeaderLabels([label for _, label in columns])
        self.setEditTriggers(NO_EDIT)
        self.setSelectionBehavior(SELECT_ROWS)
        self.setSelectionMode(SINGLE_SELECTION)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(40)
        self.setAlternatingRowColors(True)
        header = self.horizontalHeader()
        header.setSectionResizeMode(RESIZE_CONTENTS)
        if columns:
            header.setSectionResizeMode(len(columns) - 1, STRETCH)

    def load(self, rows: Sequence[Any], *, formatter: Optional[Callable[[Dict, str], str]] = None) -> None:
        self.setRowCount(0)
        for record in rows:
            data = dict(record)
            row = self.rowCount()
            self.insertRow(row)
            for column, (key, _) in enumerate(self._columns):
                text = formatter(data, key) if formatter else str(data.get(key, "") or "")
                item = QtWidgets.QTableWidgetItem(text)
                if key in ("Result", "DurationMs", "SerialNo"):
                    item.setTextAlignment(ALIGN_CENTER)
                self.setItem(row, column, item)

    def selected_row_data(self, rows: Sequence[Any]) -> Optional[Dict[str, Any]]:
        index = self.currentRow()
        if 0 <= index < len(rows):
            return dict(rows[index])
        return None


def confirm(parent, title: str, message: str) -> bool:
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    box.setIcon(QtWidgets.QMessageBox.Icon.Question if hasattr(QtWidgets.QMessageBox, "Icon")
                else QtWidgets.QMessageBox.Question)
    yes = box.addButton("Yes", QtWidgets.QMessageBox.ButtonRole.YesRole
                        if hasattr(QtWidgets.QMessageBox, "ButtonRole")
                        else QtWidgets.QMessageBox.YesRole)
    box.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole
                  if hasattr(QtWidgets.QMessageBox, "ButtonRole")
                  else QtWidgets.QMessageBox.RejectRole)
    exec_dialog(box)
    return box.clickedButton() is yes


def notify(parent, title: str, message: str, *, error: bool = False) -> None:
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    icons = getattr(QtWidgets.QMessageBox, "Icon", QtWidgets.QMessageBox)
    box.setIcon(icons.Critical if error else icons.Information)
    exec_dialog(box)
