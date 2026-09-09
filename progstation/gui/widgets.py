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


class KeyboardPad(QtWidgets.QWidget):
    """The keys themselves, as a plain widget.

    Kept separate from any dialog so it can be embedded directly in a screen.
    In kiosk mode the window manager is a minimal one built for single-window
    applications, and a keyboard that is its own top-level window is at its
    mercy -- it opened behind the login screen as an empty frame.  Embedded,
    there is no second window to mismanage.
    """

    _ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm")
    #: Shifted face of the digit row, so symbols in passwords are reachable.
    _SHIFTED_DIGITS = "!@#$%^&*()"
    #: Always available, unaffected by shift.
    _SYMBOLS = ".-_@/:+#"
    _ROWS_NUMERIC = ("789", "456", "123", "0.-")

    def __init__(self, parent=None, *, numeric: bool = False, key_height: int = 44):
        super().__init__(parent)
        self._numeric = numeric
        self._shift = False
        self._caps = False
        self._target: Optional[QtWidgets.QLineEdit] = None
        self._letter_keys: List[tuple] = []
        self.keys: List[QtWidgets.QPushButton] = []
        self.rows = 5 if numeric else 6

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        for row in (self._ROWS_NUMERIC if numeric else self._ROWS):
            line = QtWidgets.QHBoxLayout()
            line.setSpacing(6)
            for char in row:
                button = self._make_key(char)
                button.clicked.connect(lambda _=False, c=char: self._press(c))
                line.addWidget(button)
                self._letter_keys.append((button, char))
            layout.addLayout(line)

        self._symbol_keys: List[QtWidgets.QPushButton] = []
        if not numeric:
            symbols = QtWidgets.QHBoxLayout()
            symbols.setSpacing(6)
            for char in self._SYMBOLS:
                button = self._make_key(char)
                button.clicked.connect(lambda _=False, c=char: self.type_text(c))
                symbols.addWidget(button)
                self._symbol_keys.append(button)
            layout.addLayout(symbols)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(6)
        if not numeric:
            self.shift_button = self._make_key("⇧ Shift")
            self.shift_button.setCheckable(True)
            self.shift_button.clicked.connect(self._toggle_shift)
            controls.addWidget(self.shift_button)

            self.caps_button = self._make_key("Caps")
            self.caps_button.setCheckable(True)
            self.caps_button.clicked.connect(self._toggle_caps)
            controls.addWidget(self.caps_button)

            space = self._make_key("Space")
            space.clicked.connect(lambda: self.type_text(" "))
            controls.addWidget(space, 2)

        backspace = self._make_key("⌫ Back")
        backspace.clicked.connect(self.backspace)
        controls.addWidget(backspace)
        clear = self._make_key("Clear")
        clear.clicked.connect(self.clear_target)
        controls.addWidget(clear)
        layout.addLayout(controls)

        self.set_key_height(key_height)
        self._refresh_key_faces()

    def _make_key(self, text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setObjectName("Key")
        button.setMinimumWidth(36)
        # Never steal focus from the field being typed into, or the target is
        # lost the moment the first key is pressed.
        button.setFocusPolicy(
            QtCore.Qt.FocusPolicy.NoFocus if hasattr(QtCore.Qt, "FocusPolicy")
            else QtCore.Qt.NoFocus
        )
        self.keys.append(button)
        return button

    def drop_symbol_row(self) -> bool:
        """Give up the symbols row to save a row of height.

        Nothing becomes unreachable: Shift on the digit row still types
        !@#$%^&*(), and the always-on symbols are a convenience.  Returns
        False when there is no row left to drop.
        """
        if not self._symbol_keys:
            return False
        for button in self._symbol_keys:
            button.hide()
            if button in self.keys:
                self.keys.remove(button)
        self._symbol_keys = []
        self.rows -= 1
        self._invalidate()
        return True

    # ------------------------------------------------------------------ size
    def set_key_height(self, key_height: int) -> None:
        font = max(12, int(key_height * 0.42))
        self.setStyleSheet(
            f"QPushButton#Key {{ min-height: {key_height}px;"
            f" max-height: {key_height}px; padding: 2px 6px;"
            f" font-size: {font}px; border-radius: 6px; }}"
        )
        for button in self.keys:
            button.setFixedHeight(key_height)
        self._invalidate()

    def _invalidate(self) -> None:
        """Drop the cached layout measurements.

        Qt caches size hints; without this the pad keeps reporting the height
        it had before the keys were resized, and anything measuring it shrinks
        against a number that never moves.
        """
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()

    # ---------------------------------------------------------------- target
    def set_target(self, edit: Optional[QtWidgets.QLineEdit]) -> None:
        self._target = edit

    def target(self) -> Optional[QtWidgets.QLineEdit]:
        return self._target

    def type_text(self, char: str) -> None:
        if self._target is not None:
            self._target.setText(self._target.text() + char)

    def backspace(self) -> None:
        if self._target is not None:
            self._target.setText(self._target.text()[:-1])

    def clear_target(self) -> None:
        if self._target is not None:
            self._target.clear()

    # ------------------------------------------------------------------ case
    @property
    def upper(self) -> bool:
        """True when the next letter is typed upper case."""
        # Shift and Caps combine as on a physical keyboard: either one gives
        # upper case, and Shift while Caps is on gives lower.
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
            elif base.isdigit() and self._shift and not self._numeric:
                button.setText(self._SHIFTED_DIGITS[self._ROWS[0].index(base)])
            else:
                button.setText(base)

    def _press(self, base: str) -> None:
        if base.isalpha():
            self.type_text(base.upper() if self.upper else base.lower())
        elif base.isdigit() and self._shift and not self._numeric:
            self.type_text(self._SHIFTED_DIGITS[self._ROWS[0].index(base)])
        else:
            self.type_text(base)
        if self._shift:
            # Shift applies to one key only, as on a physical keyboard.
            self._shift = False
            self.shift_button.setChecked(False)
            self._refresh_key_faces()


class TouchKeyboard(QtWidgets.QDialog):
    """A keyboard in its own dialog, for screens that ask for one value.

    Used away from the login screen, where a modal prompt is the natural shape.
    The login screen embeds :class:`KeyboardPad` instead.
    """

    def __init__(self, parent=None, *, title: str = "Enter value", text: str = "",
                 numeric: bool = False, password: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

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

        self.pad = KeyboardPad(self, numeric=numeric,
                               key_height=keyboard_key_height(numeric=numeric,
                                                              password=password))
        self.pad.set_target(self.edit)
        layout.addWidget(self.pad)

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

        self._fit_to_screen()

    # Compatibility with the tests and callers that reach for these directly.
    @property
    def _keys(self):
        return self.pad.keys

    @property
    def _letter_keys(self):
        return self.pad._letter_keys

    @property
    def shift_button(self):
        return self.pad.shift_button

    @property
    def caps_button(self):
        return self.pad.caps_button

    @property
    def _shift(self):
        return self.pad._shift

    @property
    def _caps(self):
        return self.pad._caps

    @property
    def upper(self):
        return self.pad.upper

    def _toggle_shift(self):
        self.pad._toggle_shift()

    def _toggle_caps(self):
        self.pad._toggle_caps()

    def _press(self, base: str):
        self.pad._press(base)

    def _fit_to_screen(self) -> None:
        fit_with_keyboard(self, self.pad)

    def _set_reveal(self, shown: bool) -> None:
        normal = (
            QtWidgets.QLineEdit.EchoMode.Normal
            if hasattr(QtWidgets.QLineEdit, "EchoMode")
            else QtWidgets.QLineEdit.Normal
        )
        self.edit.setEchoMode(normal if shown else ECHO_PASSWORD)

    def value(self) -> str:
        return self.edit.text()

    @classmethod
    def ask(cls, parent, title: str, text: str = "", *, numeric: bool = False,
            password: bool = False) -> Optional[str]:
        dialog = cls(parent, title=title, text=text, numeric=numeric, password=password)
        if exec_dialog(dialog):
            return dialog.value()
        return None


def fit_with_keyboard(widget, pad: "KeyboardPad", *, floor: int = 38,
                      margin: int = 10) -> int:
    """Shrink *pad*'s keys until *widget* fits the screen.

    Estimating the surrounding chrome was consistently wrong -- it scales with
    the panel and differs per screen -- so measure the assembled widget and
    take the overflow out of the key rows, the only part that can give.
    Returns the height settled on.
    """
    screen = QtWidgets.QApplication.primaryScreen()
    key_height = pad.keys[0].height() or 44
    if screen is None:
        return key_height
    # A few pixels of slack, so a rounding difference between the measured
    # hint and the drawn widget cannot clip the bottom row.
    available = screen.availableGeometry().height() - margin

    # Style sheets do not reach size hints until a widget is polished, and an
    # unpolished measurement reports the pre-style metrics -- which made this
    # shrink the keyboard to fit space that was never taken.
    widget.ensurePolished()
    for child in widget.findChildren(QtWidgets.QWidget):
        child.ensurePolished()
    def remeasure() -> int:
        """Force every layout between the pad and *widget* to recompute.

        Qt caches size hints, and invalidating only the outer layout leaves the
        nested one holding the pad reporting its old height -- the loop then
        shrinks against a number that never moves, all the way to the floor.
        """
        node = pad
        while node is not None:
            layout = node.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            node.updateGeometry()
            if node is widget:
                break
            node = node.parentWidget()
        QtWidgets.QApplication.processEvents()
        widget.adjustSize()
        return widget.minimumSizeHint().height()

    for _ in range(12):
        # minimumSizeHint is what has to fit; sizeHint is merely preferred, and
        # fitting that shrinks the keyboard further than the screen requires.
        overflow = remeasure() - available
        if overflow <= 0:
            break
        if key_height <= floor:
            # Out of height to give; drop the symbols row rather than shrink
            # the keys past the point where they can be hit reliably.
            if not pad.drop_symbol_row():
                break
            continue
        key_height = max(floor, key_height - max(1, -(-overflow // pad.rows)))
        pad.set_key_height(key_height)

    # Shown at its preferred size a dialog could still exceed the screen, so
    # cap it. A fullscreen kiosk screen is unaffected: it is already the size
    # of the panel.
    widget.adjustSize()
    hint = widget.sizeHint()
    widget.resize(min(hint.width(), screen.availableGeometry().width()),
                  min(hint.height(), available))
    return key_height


def keyboard_key_height(*, numeric: bool, password: bool) -> int:
    """A starting key height suited to the screen.

    The surrounding chrome is scaled with the panel by the stylesheet, so the
    space it takes has to be scaled here too, or the keys are sized against a
    budget that no longer exists.
    """
    from .style import scale_for

    screen = QtWidgets.QApplication.primaryScreen()
    available = screen.availableGeometry().height() if screen else 600
    scale = scale_for(screen.geometry().height() if screen else 0)
    rows = 5 if numeric else 6
    chrome = (150 + (30 if password else 0)) * scale
    height = int((available - chrome - rows * 6) // rows)
    return max(38, min(height, int(64 * scale)))


class TouchLineEdit(QtWidgets.QWidget):
    """A line edit with a keypad button next to it."""

    def __init__(self, parent=None, *, placeholder: str = "", numeric: bool = False,
                 password: bool = False, keyboard_button: bool = True):
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
        self.keyboard_button = None
        if keyboard_button:
            # Omitted where a keyboard is already on the screen, as on the
            # kiosk login: a button that opens a second one is just confusing.
            button = QtWidgets.QPushButton("⌨")
            button.setFixedWidth(56)
            button.setToolTip("On-screen keyboard")
            button.clicked.connect(self._open_keyboard)
            layout.addWidget(button)
            self.keyboard_button = button

    def drop_keyboard_button(self) -> None:
        """Remove the button that opens a keyboard in its own window.

        Used when a keyboard has been docked into the surrounding dialog: the
        second window is not merely redundant there, it is unusable.
        """
        if self.keyboard_button is not None:
            self.layout().removeWidget(self.keyboard_button)
            self.keyboard_button.setParent(None)
            self.keyboard_button.deleteLater()
            self.keyboard_button = None

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


# --------------------------------------------------------------- kiosk keyboard
_KIOSK = False


def set_kiosk(enabled: bool) -> None:
    """Record that the application owns the whole screen.

    Set once at start-up.  Dialogs consult it rather than each being told
    separately, because a dialog that is not told behaves like a desktop one
    and reaches for a keyboard in its own window.
    """
    global _KIOSK
    _KIOSK = bool(enabled)


def kiosk_enabled() -> bool:
    return _KIOSK


def dock_keyboard(dialog, *, scroll: bool = True) -> Optional["KeyboardPad"]:
    """Put a keyboard inside ``dialog`` and point the touch fields at it.

    Returns the pad, or ``None`` outside kiosk mode, where the ordinary
    keyboard button is fine and a real window manager places a second window
    correctly.

    On the kiosk a second top-level window is not an option.  The minimal
    window manager collapses such windows, and over the always-on-top main
    window opening one wedges the X connection outright -- the station stops
    responding.  So everything the operator types has to happen inside the
    window that is already on the screen.

    The form is put in a scroll area first: the panel is 600 px tall and the
    keyboard needs about 250 of them, which is less than the taller dialogs
    ask for on their own.
    """
    if not _KIOSK:
        return None

    fields = dialog.findChildren(TouchLineEdit)
    if not fields:
        return None
    for field in fields:
        field.drop_keyboard_button()

    password = any(f._password for f in fields)
    pad = KeyboardPad(dialog, key_height=keyboard_key_height(numeric=False,
                                                             password=password))
    pad.set_target(fields[0].edit)

    body = dialog.layout()
    if body is not None and scroll:
        # Re-parenting the existing layout on to a container takes it off the
        # dialog, which is what lets a new top-level layout be set below.
        container = QtWidgets.QWidget()
        container.setLayout(body)
        area = QtWidgets.QScrollArea()
        area.setWidget(container)
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame
                           if hasattr(QtWidgets.QFrame, "Shape")
                           else QtWidgets.QFrame.NoFrame)
        outer = QtWidgets.QVBoxLayout(dialog)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)
        outer.addWidget(area, 1)
        outer.addWidget(pad, 0)
    elif body is not None:
        body.addWidget(pad)

    _KeyboardRouter.attach(dialog, pad, fields)

    # A dialog that asked for more than the panel has must not keep asking:
    # under the kiosk window manager the excess is simply cut off.
    dialog.setMinimumSize(0, 0)
    screen = QtWidgets.QApplication.primaryScreen()
    if screen is not None:
        dialog.setGeometry(screen.geometry())
    return pad


class _KeyboardRouter(QtCore.QObject):
    """Points the docked keyboard at whichever field has the focus."""

    def __init__(self, parent, pad: "KeyboardPad"):
        super().__init__(parent)
        self.pad = pad

    @classmethod
    def attach(cls, dialog, pad: "KeyboardPad", fields) -> "_KeyboardRouter":
        router = cls(dialog, pad)
        for field in fields:
            field.edit.installEventFilter(router)
        # Keep it alive for as long as the dialog: an event filter that is
        # collected stops routing and the keys type into nothing.
        dialog._keyboard_router = router
        return router

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        focus_in = (
            QtCore.QEvent.Type.FocusIn if hasattr(QtCore.QEvent, "Type")
            else QtCore.QEvent.FocusIn
        )
        if event.type() == focus_in:
            self.pad.set_target(watched)
        return super().eventFilter(watched, event)
