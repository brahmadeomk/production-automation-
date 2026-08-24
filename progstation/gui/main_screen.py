"""Production screen -- what the operator looks at all shift (SRS section 12).

The cycle runs on a worker thread so the touchscreen keeps repainting while
avrdude works; results come back through Qt signals, which is the only safe way
to touch widgets from outside the GUI thread.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from ..core.programmer import STEPS, CycleResult
from ..errors import StationError
from .qt import ALIGN_CENTER, QtCore, QtWidgets
from .widgets import Card, notify


class CycleWorker(QtCore.QThread):
    """Runs one programming cycle off the GUI thread."""

    progressed = QtCore.pyqtSignal(str, str, object)
    finished_cycle = QtCore.pyqtSignal(object)

    def __init__(self, engine, project, operator: str, *, skip_fixture_check: bool = False):
        super().__init__()
        self.engine = engine
        self.project = project
        self.operator = operator
        self.skip_fixture_check = skip_fixture_check

    def run(self) -> None:  # pragma: no cover - requires Qt
        def progress(step: str, message: str, ok: Optional[bool]) -> None:
            self.progressed.emit(step, message, ok)

        result = self.engine.run_cycle(
            self.project,
            self.operator,
            progress=progress,
            skip_fixture_check=self.skip_fixture_check,
        )
        self.finished_cycle.emit(result)


class MainScreen(QtWidgets.QWidget):
    counters_changed = QtCore.pyqtSignal()

    def __init__(self, app, session, parent=None):
        super().__init__(parent)
        self.app = app
        self.session = session
        self.worker: Optional[CycleWorker] = None
        self._projects: list = []
        self._build()
        self.refresh_projects()
        self._arm()

    # ------------------------------------------------------------------- UI
    def _build(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ---- left: selection and counters --------------------------------
        left = Card()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setSpacing(10)

        title = QtWidgets.QLabel("Product")
        title.setObjectName("Title")
        left_layout.addWidget(title)

        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        left_layout.addWidget(self.project_combo)

        self.detail = QtWidgets.QLabel("")
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        left_layout.addWidget(self.detail)

        left_layout.addWidget(_separator())

        left_layout.addWidget(QtWidgets.QLabel("Next serial number"))
        self.serial_label = QtWidgets.QLabel("--")
        self.serial_label.setObjectName("BigSerial")
        left_layout.addWidget(self.serial_label)

        self.remaining_label = QtWidgets.QLabel("")
        self.remaining_label.setObjectName("Subtle")
        left_layout.addWidget(self.remaining_label)

        left_layout.addStretch(1)

        counters = QtWidgets.QGridLayout()
        counters.setHorizontalSpacing(14)
        self.count_pass = _counter(counters, 0, "PASS", "#1e8e3e")
        self.count_fail = _counter(counters, 1, "FAIL", "#c5221f")
        self.count_yield = _counter(counters, 2, "YIELD", "#1a73e8")
        left_layout.addLayout(counters)
        layout.addWidget(left, 2)

        # ---- right: status, progress, start ------------------------------
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)

        self.status = QtWidgets.QLabel("READY")
        self.status.setObjectName("StatusIdle")
        self.status.setAlignment(ALIGN_CENTER)
        self.status.setMinimumHeight(120)
        right.addWidget(self.status)

        self.message = QtWidgets.QLabel("Seat a board in the fixture and press START.")
        self.message.setAlignment(ALIGN_CENTER)
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(48)
        right.addWidget(self.message)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, len(STEPS))
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m")
        right.addWidget(self.progress)

        self.steps = QtWidgets.QPlainTextEdit()
        self.steps.setReadOnly(True)
        self.steps.setMaximumHeight(150)
        right.addWidget(self.steps)

        self.start_button = QtWidgets.QPushButton("START")
        self.start_button.setObjectName("Start")
        self.start_button.clicked.connect(self.start_cycle)
        right.addWidget(self.start_button)

        layout.addLayout(right, 3)

    # -------------------------------------------------------------- helpers
    def current_project(self):
        index = self.project_combo.currentIndex()
        if 0 <= index < len(self._projects):
            return self._projects[index]
        return None

    def refresh_projects(self) -> None:
        self._projects = list(self.app.db.list_projects())
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in self._projects:
            self.project_combo.addItem(project["ProjectName"])
        self.project_combo.blockSignals(False)
        self._project_changed()

    def _project_changed(self, *_args) -> None:
        project = self.current_project()
        if project is None:
            self.detail.setText("No project configured. Ask an administrator to add one.")
            self.serial_label.setText("--")
            self.remaining_label.setText("")
            self.start_button.setEnabled(False)
            return

        self.detail.setText(
            f"MCU {project['MCU']}  ·  FW {project['FirmwareVersion'] or '-'}"
            f"  ·  HW {project['HwRevision'] or '-'}  ·  {project['ProductVariant'] or '-'}"
        )
        problems = self.app.engine.validate_project(project)
        if problems:
            self.message.setText("Project not ready: " + "; ".join(problems))
            self.start_button.setEnabled(False)
        else:
            self.start_button.setEnabled(self.worker is None)
        self._update_serial(project)
        self._update_counters(project)

    def _update_serial(self, project) -> None:
        try:
            reservation = self.app.serials.peek(project)
            self.serial_label.setText(reservation.text)
        except StationError as exc:
            self.serial_label.setText("--")
            self.message.setText(str(exc))
            self.start_button.setEnabled(False)
        remaining = self.app.serials.remaining(project)
        self.remaining_label.setText(
            f"{remaining} serial number(s) left in range" if remaining is not None else ""
        )

    def _update_counters(self, project) -> None:
        summary = self.app.reports.daily(project_id=int(project["ProjectId"]))
        self.count_pass.setText(str(summary.passed))
        self.count_fail.setText(str(summary.failed))
        self.count_yield.setText(f"{summary.yield_pct:.1f}%")

    def _arm(self) -> None:
        """Wire the physical start button, when the station has one."""
        io = self.app.io
        if io is None:
            return
        if self.app.config.require_hardware_start or not io.simulated:
            io.on_start_button(lambda: QtCore.QMetaObject.invokeMethod(
                self, "start_cycle", QtCore.Qt.ConnectionType.QueuedConnection
                if hasattr(QtCore.Qt, "ConnectionType") else QtCore.Qt.QueuedConnection
            ))
        if self.app.config.require_hardware_start:
            self.start_button.setText("PRESS THE START BUTTON")
            self.start_button.setEnabled(False)

    # ---------------------------------------------------------------- cycle
    @QtCore.pyqtSlot()
    def start_cycle(self) -> None:
        if self.worker is not None:
            return
        project = self.current_project()
        if project is None:
            return
        problems = self.app.engine.validate_project(project)
        if problems:
            notify(self, "Project not ready", "\n".join(problems), error=True)
            return

        self.steps.clear()
        self.progress.setValue(0)
        self.status.setObjectName("StatusBusy")
        self.status.setText("PROGRAMMING")
        self._restyle(self.status)
        self.message.setText("Do not remove the board.")
        self.start_button.setEnabled(False)

        self.worker = CycleWorker(self.app.engine, project, self.session.username)
        self.worker.progressed.connect(self._on_progress)
        self.worker.finished_cycle.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, step: str, message: str, ok) -> None:
        mark = "..." if ok is None else ("OK " if ok else "!! ")
        self.steps.appendPlainText(f"{mark} {message}")
        if ok:
            self.progress.setValue(min(self.progress.value() + 1, len(STEPS)))

    def _on_finished(self, result: CycleResult) -> None:
        self.worker = None
        if result.ok:
            self.status.setObjectName("StatusPass")
            self.status.setText("PASS")
            self.message.setText(f"Serial {result.serial_no} programmed in {result.duration_ms / 1000:.1f} s")
            self.progress.setValue(len(STEPS))
        else:
            self.status.setObjectName("StatusFail")
            self.status.setText("FAIL")
            self.message.setText(f"{result.operator_hint}\n[{result.error_code}] {result.error_message}")
        self._restyle(self.status)

        project = self.current_project()
        if project is not None:
            # Re-read so the counter shown is the one the database now holds.
            project = self.app.db.get_project(int(project["ProjectId"]))
            self._projects[self.project_combo.currentIndex()] = project
            self._update_serial(project)
            self._update_counters(project)
        self.counters_changed.emit()

        QtCore.QTimer.singleShot(
            int(self.app.config.result_dwell_s * 1000), self._ready_again
        )

    def _ready_again(self) -> None:
        if self.worker is not None:
            return
        self.status.setObjectName("StatusIdle")
        self.status.setText("READY")
        self._restyle(self.status)
        self.message.setText("Seat a board in the fixture and press START.")
        if self.app.io:
            self.app.io.ready()
        if not self.app.config.require_hardware_start:
            self.start_button.setEnabled(self.current_project() is not None)

    @staticmethod
    def _restyle(widget) -> None:
        """Re-apply the stylesheet after changing objectName."""
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def _separator() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setFrameShape(
        QtWidgets.QFrame.Shape.HLine if hasattr(QtWidgets.QFrame, "Shape")
        else QtWidgets.QFrame.HLine
    )
    line.setStyleSheet("color: #dfe2e7;")
    return line


def _counter(grid, column: int, label: str, color: str):
    caption = QtWidgets.QLabel(label)
    caption.setObjectName("Subtle")
    caption.setAlignment(ALIGN_CENTER)
    value = QtWidgets.QLabel("0")
    value.setAlignment(ALIGN_CENTER)
    value.setStyleSheet(f"font-size: 30px; font-weight: 700; color: {color};")
    grid.addWidget(caption, 0, column)
    grid.addWidget(value, 1, column)
    return value
