"""Touch-optimised stylesheet, scaled to the panel actually fitted.

Everything is sized for a finger on a resistive/capacitive panel: minimum 48 px
touch targets, high contrast for a lit shop floor, and PASS/FAIL colours that
read from a metre away.
"""

COLOR_PASS = "#1e8e3e"
COLOR_FAIL = "#c5221f"
COLOR_BUSY = "#e37400"
COLOR_ACCENT = "#1a73e8"
COLOR_BG = "#f5f6f8"
COLOR_PANEL = "#ffffff"
COLOR_TEXT = "#1f2430"
COLOR_MUTED = "#5f6774"

STYLESHEET = f"""
QWidget {{
    background: {COLOR_BG};
    color: {COLOR_TEXT};
    font-family: "DejaVu Sans", "Noto Sans", sans-serif;
    font-size: 15px;
}}
QFrame#Card, QGroupBox {{
    background: {COLOR_PANEL};
    border: 1px solid #d8dbe0;
    border-radius: 8px;
}}
QGroupBox {{
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}
QLabel#Title      {{ font-size: 22px; font-weight: 700; }}
QLabel#Subtle     {{ color: {COLOR_MUTED}; font-size: 13px; }}
QLabel#BigSerial  {{ font-size: 40px; font-weight: 700; letter-spacing: 2px; }}
QLabel#StatusPass {{ background: {COLOR_PASS}; color: white; font-size: 34px;
                     font-weight: 700; border-radius: 8px; padding: 14px; }}
QLabel#StatusFail {{ background: {COLOR_FAIL}; color: white; font-size: 34px;
                     font-weight: 700; border-radius: 8px; padding: 14px; }}
QLabel#StatusBusy {{ background: {COLOR_BUSY}; color: white; font-size: 30px;
                     font-weight: 700; border-radius: 8px; padding: 14px; }}
QLabel#StatusIdle {{ background: #dfe3e8; color: {COLOR_TEXT}; font-size: 30px;
                     font-weight: 600; border-radius: 8px; padding: 14px; }}
QPushButton {{
    background: {COLOR_PANEL};
    border: 1px solid #c3c8d0;
    border-radius: 8px;
    padding: 12px 18px;
    min-height: 44px;
    font-size: 16px;
}}
QPushButton:hover  {{ background: #eef2fb; }}
QPushButton:pressed {{ background: #dde5f7; }}
QPushButton:disabled {{ color: #9aa0aa; background: #eceef1; }}
QPushButton#Primary {{
    background: {COLOR_ACCENT}; color: white; border: none; font-weight: 700;
}}
QPushButton#Primary:disabled {{ background: #a9c4f0; color: #f0f4fd; }}
QPushButton#Start {{
    background: {COLOR_PASS}; color: white; border: none;
    /* Big enough to hit reliably with a gloved hand, small enough that the
       whole production screen fits a 480 px panel. */
    font-size: 24px; font-weight: 700; min-height: 58px;
}}
QPushButton#Start:disabled {{ background: #a6cdb2; color: #eef5f0; }}
QPushButton#Danger {{ background: {COLOR_FAIL}; color: white; border: none; }}
QPushButton#Nav {{
    background: transparent; border: none; border-bottom: 3px solid transparent;
    border-radius: 0; font-size: 15px; padding: 12px 10px;
    /* The checked state turns the label bold, so reserve a little width to
       stop the text clipping -- but not so much that the four tabs plus the
       user label overflow an 800 px panel. */
    min-width: 88px;
}}
QPushButton#Nav:checked {{
    border-bottom: 3px solid {COLOR_ACCENT}; color: {COLOR_ACCENT}; font-weight: 700;
}}
QLineEdit, QComboBox, QSpinBox, QDateEdit, QPlainTextEdit, QTextEdit {{
    background: white; border: 1px solid #c3c8d0; border-radius: 6px;
    /* Horizontal padding stays modest: the reports filter row has to fit six
       controls plus a button across an 800 px panel. */
    padding: 10px 8px; min-height: 40px; font-size: 15px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {COLOR_ACCENT}; }}
QComboBox::drop-down {{ width: 26px; }}
QTableWidget {{
    background: white; border: 1px solid #d8dbe0; border-radius: 6px;
    gridline-color: #e6e8ec; font-size: 14px;
}}
QHeaderView::section {{
    background: #eef0f4; padding: 10px 8px; border: none;
    border-right: 1px solid #dfe2e7; font-weight: 600;
}}
QTableWidget::item {{ padding: 8px 6px; }}
QTableWidget::item:selected {{ background: #d9e6fb; color: {COLOR_TEXT}; }}
QProgressBar {{
    border: 1px solid #c3c8d0; border-radius: 6px; height: 26px;
    text-align: center; background: white;
}}
QProgressBar::chunk {{ background: {COLOR_ACCENT}; border-radius: 5px; }}
QScrollBar:vertical   {{ width: 18px; background: #eceef1; }}
QScrollBar:horizontal {{ height: 18px; background: #eceef1; }}
QScrollBar::handle {{ background: #b6bcc6; border-radius: 8px; min-height: 40px; }}
QTabBar::tab {{ padding: 12px 20px; font-size: 15px; }}
QStatusBar {{ background: #e7eaee; font-size: 13px; }}
"""


# --------------------------------------------------------------- scaling
import re

#: The stylesheet above is written for the smallest supported panel.
_REFERENCE_HEIGHT = 480
_METRIC_RE = re.compile(r"(font-size|min-height|min-width):\s*(\d+)px")


def scale_for(screen_height: int) -> float:
    """How much to enlarge the interface for the fitted display.

    The sizes above suit a 7-inch 800x480 panel.  On a 10-inch screen those
    same pixel sizes are physically similar but visually lost in the extra
    space, so grow them with the panel.  Clamped: never shrink below the
    designed size, and stop at 1.6 so a large monitor does not end up with
    absurd controls.
    """
    if not screen_height:
        return 1.0
    return max(1.0, min(screen_height / _REFERENCE_HEIGHT, 1.6))


def build_stylesheet(scale: float = 1.0) -> str:
    """Return the stylesheet with its metrics scaled by *scale*."""
    if abs(scale - 1.0) < 0.02:
        return STYLESHEET

    def resize(match: "re.Match") -> str:
        prop, value = match.group(1), int(match.group(2))
        return f"{prop}: {max(10, round(value * scale))}px"

    return _METRIC_RE.sub(resize, STYLESHEET)
