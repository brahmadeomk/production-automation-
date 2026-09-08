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

    Passwords are case sensitive, so the letter keys must reach both cases:
    an uppercase-only keypad makes a mixed-case password impossible to type
    and locks the operator out. Shift applies to the next key, Caps latches.

    ``numeric`` mode shows a plain keypad, which is what operators use for
    serial and quantity entry.
    """

    _ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm")
    #: Shifted face of the digit row, so symbols in passwords are reachable.
    _SHIFTED_DIGITS = "!@#$%^&*()"
    #: Always available, unaffected by shift.
    _SYMBOLS = ".-_@/:+#"
    _ROWS_NUMERIC = ("789", "456", "123", "0.-")

    def __init__(self, parent=None, *, title: str = "Enter value", text: str = "",
                 numeric: bool = False, password: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._numeric = numeric
        self._shift = False
        self._caps = False
        self._letter_keys: List[tuple] = []          # (button, base character)

        key_height = self._key_height(numeric=numeric, password=password)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)
        layout.addWidget(QtWidgets.QLabel(title))

        self.edit = QtWidgets.QLineEdit(text)
        if password:
            self.edit.setEchoMode(ECHO_PASSWORD)
        layout.addWidget(self.edit)

        if password:
            # Typing a password blind on a touch panel is error prone, and a
            # wrong entry costs a login attempt against the lockout counter.
            self.reveal = QtWidgets.QCheckBox("Show characters")
            self.reveal.toggled.connect(self._set_reveal)
            layout.addWidget(self.reveal)

        keys = QtWidgets.QVBoxLayout()
        keys.setSpacing(6)
        for row in (self._ROWS_NUMERIC if numeric else self._ROWS):
            line = QtWidgets.QHBoxLayout()
            line.setSpacing(6)
            for char in row:
                button = QtWidgets.QPushButton(char)
                button.setObjectName("Key")
                button.setFixedHeight(key_height)
                button.setMinimumWidth(40)
                button.clicked.connect(lambda _=False, b=None, c=char: self._press(c))
                line.addWidget(button)
                if not numeric:
                    self._letter_keys.append((button, char))
            keys.addLayout(line)

        if not numeric:
            symbols = QtWidgets.QHBoxLayout()
            symbols.setSpacing(6)
            for char in self._SYMBOLS:
                button = QtWidgets.QPushButton(char)
                button.setObjectName("Key")
                button.setFixedHeight(key_height)
                button.setMinimumWidth(40)
                button.clicked.connect(lambda _=False, c=char: self._type(c))
                symbols.addWidget(button)
            keys.addLayout(symbols)
        layout.addLayout(keys)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(6)
        if not numeric:
            self.shift_button = QtWidgets.QPushButton("⇧ Shift")
            self.shift_button.setCheckable(True)
            self.shift_button.setFixedHeight(key_height)
            self.shift_button.clicked.connect(self._toggle_shift)
            controls.addWidget(self.shift_button)

            self.caps_button = QtWidgets.QPushButton("Caps")
            self.caps_button.setCheckable(True)
            self.caps_button.setFixedHeight(key_height)
            self.caps_button.clicked.connect(self._toggle_caps)
            controls.addWidget(self.caps_button)

            space = QtWidgets.QPushButton("Space")
            space.setFixedHeight(key_height)
            space.clicked.connect(lambda: self._type(" "))
            controls.addWidget(space, 2)

        backspace = QtWidgets.QPushButton("⌫ Back")
        backspace.setFixedHeight(key_height)
        backspace.clicked.connect(self._backspace)
        controls.addWidget(backspace)
        clear = QtWidgets.QPushButton("Clear")
        clear.setFixedHeight(key_height)
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

        self._refresh_key_faces()

    # ------------------------------------------------------------------ size
    @staticmethod
    def _key_height(*, numeric: bool, password: bool) -> int:
        """Key height that keeps the whole keyboard on screen.

        The dialog is taller than a 600 px panel at default sizes, which puts
        OK and Cancel off the bottom edge -- an operator could type a password
        but never confirm it.  Derive the key height from the screen instead of
        fixing it.
        """
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 600
        rows = 5 if numeric else 6          # key rows plus the control row
        # Title, entry field, optional reveal box, OK/Cancel, margins, spacing.
        overhead = 150 + (30 if password else 0) + rows * 6
        height = (available - overhead) // rows
        # Never below a reliable touch target, never wastefully large.
        return max(38, min(height, 64))

    # ------------------------------------------------------------------ case
    @property
    def upper(self) -> bool:
        """True when the next letter is typed upper case."""
        # Shift and Caps combine the way a physical keyboard does: either one
        # gives upper case, and Shift while Caps is on gives lower.
        return self._caps != self._shift

    def _toggle_shift(self) -> None:
        self._shift = self.shift_button.isChecked()
        self._refresh_key_faces()

    def _toggle_caps(self) -> None:
        self._caps = self.caps_button.isChecked()
        self._refresh_key_faces()

    def _refresh_key_faces(self) -> None:
        """Show on each key exactly what pressing it will type."""
        for button, base in self._letter_keys:
            if base.isalpha():
                button.setText(base.upper() if self.upper else base.lower())
            elif base.isdigit() and self._shift:
                button.setText(self._SHIFTED_DIGITS[self._ROWS[0].index(base)])
            else:
                button.setText(base)

    def _press(self, base: str) -> None:
        if base.isalpha():
            self._type(base.upper() if self.upper else base.lower())
        elif base.isdigit() and self._shift:
            self._type(self._SHIFTED_DIGITS[self._ROWS[0].index(base)])
        else:
            self._type(base)
        if self._shift:
            # Shift applies to one key only, as on a physical keyboard.
            self._shift = False
            self.shift_button.setChecked(False)
            self._refresh_key_faces()

    def _set_reveal(self, shown: bool) -> None:
        self.edit.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Normal
            if hasattr(QtWidgets.QLineEdit, "EchoMode")
            else QtWidgets.QLineEdit.Normal
        ) if shown else self.edit.setEchoMode(ECHO_PASSWORD)

    # ----------------------------------------------------------------- entry
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
