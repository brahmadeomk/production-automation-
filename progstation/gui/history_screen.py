"""History screen: search the production log by serial, date, operator,
firmware version and result (SRS sections 11 and 12)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..db.database import rows_to_dicts
from ..reports.excel import OPENPYXL_AVAILABLE, default_filename, export_records
from .qt import QtCore, QtWidgets
from .widgets import Card, RecordTable, TouchLineEdit, notify

COLUMNS = (
    ("Timestamp", "Time"),
    ("SerialNo", "Serial"),
    ("Result", "Result"),
    ("ProjectName", "Project"),
    ("Operator", "Operator"),
    ("FirmwareVersion", "Firmware"),
    ("DurationMs", "Cycle"),
    ("ErrorCode", "Error"),
)


def _to_iso(value: date, *, end: bool = False) -> str:
    moment = datetime.combine(value + (timedelta(days=1) if end else timedelta()), time.min)
    return moment.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class HistoryScreen(QtWidgets.QWidget):
    def __init__(self, app, session, parent=None):
        super().__init__(parent)
        self.app = app
        self.session = session
        self._rows: List[Dict[str, Any]] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        filters = Card()
        grid = QtWidgets.QGridLayout(filters)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(QtWidgets.QLabel("Serial number"), 0, 0)
        self.serial = TouchLineEdit(placeholder="full or partial serial")
        grid.addWidget(self.serial, 1, 0)

        grid.addWidget(QtWidgets.QLabel("From"), 0, 1)
        self.date_from = QtWidgets.QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QtCore.QDate.currentDate().addDays(-7))
        grid.addWidget(self.date_from, 1, 1)

        grid.addWidget(QtWidgets.QLabel("To"), 0, 2)
        self.date_to = QtWidgets.QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QtCore.QDate.currentDate())
        grid.addWidget(self.date_to, 1, 2)

        grid.addWidget(QtWidgets.QLabel("Operator"), 0, 3)
        self.operator = QtWidgets.QComboBox()
        self.operator.setEditable(False)
        grid.addWidget(self.operator, 1, 3)

        grid.addWidget(QtWidgets.QLabel("Firmware"), 0, 4)
        self.firmware = QtWidgets.QComboBox()
        grid.addWidget(self.firmware, 1, 4)

        grid.addWidget(QtWidgets.QLabel("Result"), 0, 5)
        self.result = QtWidgets.QComboBox()
        self.result.addItems(["All", "PASS", "FAIL"])
        grid.addWidget(self.result, 1, 5)

        buttons = QtWidgets.QHBoxLayout()
        search = QtWidgets.QPushButton("Search")
        search.setObjectName("Primary")
        search.clicked.connect(self.refresh)
        buttons.addWidget(search)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self._clear)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        self.export_button = QtWidgets.QPushButton("Export to Excel")
        self.export_button.clicked.connect(self._export)
        self.export_button.setEnabled(OPENPYXL_AVAILABLE)
        if not OPENPYXL_AVAILABLE:
            self.export_button.setToolTip("openpyxl is not installed on this station")
        buttons.addWidget(self.export_button)
        detail = QtWidgets.QPushButton("Record detail")
        detail.clicked.connect(self._show_detail)
        buttons.addWidget(detail)
        grid.addLayout(buttons, 2, 0, 1, 6)
        layout.addWidget(filters)

        self.table = RecordTable(COLUMNS)
        self.table.doubleClicked.connect(self._show_detail)
        layout.addWidget(self.table, 1)

        self.count_label = QtWidgets.QLabel("")
        self.count_label.setObjectName("Subtle")
        layout.addWidget(self.count_label)

    # ---------------------------------------------------------------- data
    def _clear(self) -> None:
        self.serial.clear()
        self.date_from.setDate(QtCore.QDate.currentDate().addDays(-7))
        self.date_to.setDate(QtCore.QDate.currentDate())
        self.operator.setCurrentIndex(0)
        self.firmware.setCurrentIndex(0)
        self.result.setCurrentIndex(0)
        self.refresh()

    def _reload_choices(self) -> None:
        for combo, column in ((self.operator, "Operator"), (self.firmware, "FirmwareVersion")):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("All")
            for row in self.app.db.query(
                f"SELECT DISTINCT {column} AS v FROM ProductionLog"
                f" WHERE {column} <> '' ORDER BY v"
            ):
                combo.addItem(row["v"])
            index = combo.findText(current)
            combo.setCurrentIndex(max(index, 0))
            combo.blockSignals(False)

    def refresh(self) -> None:
        self._reload_choices()
        self._rows = rows_to_dicts(
            self.app.db.search_production(
                serial=self.serial.text().strip() or None,
                date_from=_to_iso(self.date_from.date().toPyDate()),
                date_to=_to_iso(self.date_to.date().toPyDate(), end=True),
                operator=self._combo_value(self.operator),
                firmware_version=self._combo_value(self.firmware),
                result=self._combo_value(self.result),
                limit=2000,
            )
        )
        self.table.load(self._rows, formatter=_format_cell)
        self.count_label.setText(f"{len(self._rows)} record(s) — newest first")

    @staticmethod
    def _combo_value(combo) -> Optional[str]:
        value = combo.currentText()
        return None if value in ("All", "") else value

    # -------------------------------------------------------------- actions
    def _export(self) -> None:
        if not self._rows:
            notify(self, "Export", "There is nothing to export.")
            return
        target = (
            QtCore.QDir(self.app.config.reports.export_dir)
            .filePath(default_filename("production-log"))
        )
        try:
            path = export_records(
                self._rows,
                target,
                title="Filtered production records",
                filters={
                    "serial": self.serial.text(),
                    "from": self.date_from.date().toString("yyyy-MM-dd"),
                    "to": self.date_to.date().toString("yyyy-MM-dd"),
                    "operator": self._combo_value(self.operator),
                    "firmware": self._combo_value(self.firmware),
                    "result": self._combo_value(self.result),
                },
            )
        except Exception as exc:
            notify(self, "Export failed", str(exc), error=True)
            return
        self.app.db.audit(self.session.username, "export.records", str(path), f"{len(self._rows)} rows")
        notify(self, "Export complete", f"{len(self._rows)} record(s) written to\n{path}")

    def _show_detail(self) -> None:
        record = self.table.selected_row_data(self._rows)
        if not record:
            return
        lines = [
            f"{key:<16}: {value}"
            for key, value in record.items()
            if value not in (None, "") and key != "LogId"
        ]
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle(f"Record {record.get('SerialNo') or record.get('LogId')}")
        dialog.setText("<pre>" + "\n".join(lines) + "</pre>")
        dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()


def _format_cell(record: Dict[str, Any], key: str) -> str:
    value = record.get(key, "")
    if key == "Timestamp" and value:
        return str(value)[:19].replace("T", " ")
    if key == "DurationMs":
        return f"{int(value or 0) / 1000:.1f} s"
    return str(value or "")
