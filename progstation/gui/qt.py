"""Qt import shim.

The station runs PyQt5 on Raspberry Pi OS (which packages it as
``python3-pyqt5``), but PyQt6 is used on newer desktops.  Importing through this
module keeps the rest of the GUI free of version checks.
"""

from __future__ import annotations

QT_BINDING = ""

try:
    from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore

    QT_BINDING = "PyQt5"
except ImportError:  # pragma: no cover - depends on the host
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets  # type: ignore

        QT_BINDING = "PyQt6"
    except ImportError:
        QtCore = QtGui = QtWidgets = None  # type: ignore
        QT_BINDING = ""

QT_AVAILABLE = bool(QT_BINDING)


def missing_message() -> str:
    return (
        "PyQt is not installed. On Raspberry Pi OS run:\n"
        "    sudo apt install python3-pyqt5\n"
        "or install it into your virtual environment:\n"
        "    pip install PyQt5"
    )


if QT_AVAILABLE:  # pragma: no cover - exercised only with a Qt install
    # PyQt6 moved enums onto scoped types; expose the handful the GUI uses
    # under one name so the screens read the same on both bindings.
    if QT_BINDING == "PyQt6":
        ALIGN_CENTER = QtCore.Qt.AlignmentFlag.AlignCenter
        ALIGN_LEFT = QtCore.Qt.AlignmentFlag.AlignLeft
        ALIGN_RIGHT = QtCore.Qt.AlignmentFlag.AlignRight
        ALIGN_VCENTER = QtCore.Qt.AlignmentFlag.AlignVCenter
        ECHO_PASSWORD = QtWidgets.QLineEdit.EchoMode.Password
        SELECT_ROWS = QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        SINGLE_SELECTION = QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        NO_EDIT = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        STRETCH = QtWidgets.QHeaderView.ResizeMode.Stretch
        RESIZE_CONTENTS = QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        ITEM_ENABLED = QtCore.Qt.ItemFlag.ItemIsEnabled
    else:
        ALIGN_CENTER = QtCore.Qt.AlignCenter
        ALIGN_LEFT = QtCore.Qt.AlignLeft
        ALIGN_RIGHT = QtCore.Qt.AlignRight
        ALIGN_VCENTER = QtCore.Qt.AlignVCenter
        ECHO_PASSWORD = QtWidgets.QLineEdit.Password
        SELECT_ROWS = QtWidgets.QAbstractItemView.SelectRows
        SINGLE_SELECTION = QtWidgets.QAbstractItemView.SingleSelection
        NO_EDIT = QtWidgets.QAbstractItemView.NoEditTriggers
        STRETCH = QtWidgets.QHeaderView.Stretch
        RESIZE_CONTENTS = QtWidgets.QHeaderView.ResizeToContents
        ITEM_ENABLED = QtCore.Qt.ItemIsEnabled


def exec_dialog(dialog) -> int:  # pragma: no cover
    """``exec_()`` on PyQt5, ``exec()`` on PyQt6."""
    return dialog.exec() if QT_BINDING == "PyQt6" else dialog.exec_()


def exec_app(app) -> int:  # pragma: no cover
    return app.exec() if QT_BINDING == "PyQt6" else app.exec_()
