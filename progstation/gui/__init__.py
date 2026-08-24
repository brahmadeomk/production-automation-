"""Touchscreen GUI package.

The screen modules define Qt classes at import time, so importing them without
PyQt raises.  :func:`run_gui` checks for the binding first and reports how to
install it, which keeps a station missing PyQt from showing a traceback.
"""

from __future__ import annotations

import sys


def run_gui(app, *, fullscreen: bool = True) -> int:
    from .qt import QT_AVAILABLE, missing_message

    if not QT_AVAILABLE:
        print(missing_message(), file=sys.stderr)
        return 1

    from .app import run_gui as _run_gui

    return _run_gui(app, fullscreen=fullscreen)
