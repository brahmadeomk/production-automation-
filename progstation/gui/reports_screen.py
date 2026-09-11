"""Reports screen: daily / weekly / monthly production summaries with
PASS/FAIL counts, yield, operator statistics and project breakdowns
(SRS sections 13 and 14)."""

from __future__ import annotations

from typing import Any, Dict, List

from ..reports.engine import PERIODS
from ..reports.excel import OPENPYXL_AVAILABLE, default_filename, export_period
from .qt import ALIGN_CENTER, QtCore, QtWidgets
from .widgets import Card, RecordTable, notify
from ..timeutil import local as local_time


class ReportsScreen(QtWidgets.QWidget):
    def __init__(self, app, session, parent=None):
        super().__init__(parent)
        self.app = app
        self.session = session
        self._summary = None
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        controls = Card()
        row = QtWidgets.QHBoxLayout(controls)
        row.setSpacing(10)

        row.addWidget(QtWidgets.QLabel("Period"))
        self.period = QtWidgets.QComboBox()
        self.period.addItems([p.capitalize() for p in PERIODS])
        self.period.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.period)

        row.addWidget(QtWidgets.QLabel("Date"))
        self.date = QtWidgets.QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDate(QtCore.QDate.currentDate())
        self.date.dateChanged.connect(self.refresh)
        row.addWidget(self.date)

        row.addWidget(QtWidgets.QLabel("Project"))
        self.project = QtWidgets.QComboBox()
        self.project.addItem("All projects", None)
        for project in self.app.db.list_projects(include_inactive=True):
            self.project.addItem(project["ProjectName"], int(project["ProjectId"]))
        self.project.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.project)

        row.addStretch(1)
        self.export_button = QtWidgets.QPushButton("Export")
        self.export_button.setObjectName("Primary")
        self.export_button.setToolTip("Export the current view to an .xlsx file")
        self.export_button.clicked.connect(self._export)
        self.export_button.setEnabled(OPENPYXL_AVAILABLE)
        if not OPENPYXL_AVAILABLE:
            self.export_button.setToolTip("openpyxl is not installed on this station")
        row.addWidget(self.export_button)
        layout.addWidget(controls)

        # Headline tiles
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(10)
        self.tile_total = _tile(tiles, "PROGRAMMED", "#1f2430")
        self.tile_pass = _tile(tiles, "PASS", "#1e8e3e")
        self.tile_fail = _tile(tiles, "FAIL", "#c5221f")
        self.tile_yield = _tile(tiles, "YIELD", "#1a73e8")
        self.tile_cycle = _tile(tiles, "AVG CYCLE", "#5f6774")
        layout.addLayout(tiles)

        # Breakdown tables
        tabs = QtWidgets.QTabWidget()
        self.by_project = RecordTable(
            (("project", "Project"), ("total", "Total"), ("passed", "PASS"),
             ("failed", "FAIL"), ("yield_pct", "Yield %"), ("avg_cycle_ms", "Avg ms"))
        )
        self.by_operator = RecordTable(
            (("operator", "Operator"), ("total", "Total"), ("passed", "PASS"),
             ("failed", "FAIL"), ("yield_pct", "Yield %"), ("avg_cycle_ms", "Avg ms"))
        )
        self.by_error = RecordTable(
            (("error_code", "Error code"), ("count", "Count"), ("message", "Last message"))
        )
        self.by_day = RecordTable(
            (("day", "Day"), ("total", "Total"), ("passed", "PASS"),
             ("failed", "FAIL"), ("yield_pct", "Yield %"))
        )
        tabs.addTab(self.by_project, "By project")
        tabs.addTab(self.by_operator, "By operator")
        tabs.addTab(self.by_error, "Failures")
        tabs.addTab(self.by_day, "By day")
        layout.addWidget(tabs, 1)

        self.window_label = QtWidgets.QLabel("")
        self.window_label.setObjectName("Subtle")
        layout.addWidget(self.window_label)

    # ---------------------------------------------------------------- data
    def refresh(self) -> None:
        period = PERIODS[self.period.currentIndex()]
        summary = self.app.reports.summary(
            period,
            self.date.date().toPyDate(),
            project_id=self.project.currentData(),
        )
        self._summary = summary

        self.tile_total.setText(str(summary.total))
        self.tile_pass.setText(str(summary.passed))
        self.tile_fail.setText(str(summary.failed))
        self.tile_yield.setText(f"{summary.yield_pct:.1f}%")
        self.tile_cycle.setText(f"{summary.avg_cycle_ms / 1000:.1f}s")

        self.by_project.load(summary.by_project)
        self.by_operator.load(summary.by_operator)
        self.by_error.load(summary.by_error)
        self.by_day.load(summary.by_day)
        self.window_label.setText(
            f"{summary.label}  ·  {local_time(summary.start)}"
            f" to {local_time(summary.end)}"
        )

    def _export(self) -> None:
        if self._summary is None:
            return
        period = PERIODS[self.period.currentIndex()]
        target = (
            QtCore.QDir(self.app.config.reports.export_dir)
            .filePath(default_filename("report", period))
        )
        try:
            path = export_period(
                self.app.db,
                self._summary,
                target,
                company_name=self.app.config.reports.company_name,
            )
        except Exception as exc:
            notify(self, "Export failed", str(exc), error=True)
            return
        self.app.db.audit(self.session.username, "export.report", str(path), period)
        notify(self, "Export complete", f"{period.capitalize()} report written to\n{path}")


def _tile(layout, caption: str, color: str):
    card = QtWidgets.QFrame()
    card.setObjectName("Card")
    inner = QtWidgets.QVBoxLayout(card)
    label = QtWidgets.QLabel(caption)
    label.setObjectName("Subtle")
    label.setAlignment(ALIGN_CENTER)
    value = QtWidgets.QLabel("0")
    value.setAlignment(ALIGN_CENTER)
    value.setStyleSheet(f"font-size: 32px; font-weight: 700; color: {color};")
    inner.addWidget(label)
    inner.addWidget(value)
    layout.addWidget(card)
    return value
