"""The programming workflow engine (SRS section 9).

One cycle, in order:

1. detect fixture              6. write EEPROM manufacturing data
2. reserve the serial number   7. verify EEPROM
3. read and check the AVR signature
4. program HEX                 8. log the result
5. verify flash                9. increment the serial only after PASS

Steps 8 and 9 share a single database transaction, so the counter can never
advance for a unit that has no PASS record -- and never advances at all for a
FAIL, which is what SRS section 10 requires.

The engine is deliberately free of GUI and GPIO imports.  Callers subscribe to
progress via a callback, which is how both the touchscreen and the CLI drive it.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import StationConfig
from ..db.database import Database, utc_now
from ..errors import (
    ConfigurationError,
    EepromProgramError,
    EepromVerifyError,
    FirmwareMissingError,
    FixtureOpenError,
    FlashProgramError,
    FlashVerifyError,
    NoDeviceError,
    SignatureMismatchError,
    StationError,
)
from ..hw.station_io import StationIO
from .avrdude import (
    AvrdudeBackend,
    classify_failure,
    signature_matches,
    signature_name,
)
from .eeprom import EepromMap, map_for_project
from .ihex import write_hex
from .serials import SerialManager, SerialReservation

log = logging.getLogger(__name__)


def _firmware_problems(hex_path: Path) -> List[str]:
    """Why the firmware cannot be used, if it cannot.

    ``Path.is_file()`` only swallows "does not exist" errors -- a permission
    error propagates.  Firmware kept in an operator's home directory is
    routinely unreadable by the station's service account, so probing it
    naively crashes the caller instead of reporting a fixable problem.
    """
    try:
        if not hex_path.is_file():
            return [f"firmware file not found: {hex_path}"]
    except OSError as exc:
        return [
            f"firmware file cannot be read: {hex_path}"
            f" ({exc.strerror}) - keep firmware somewhere the station account"
            f" can read, such as /var/lib/progstation/firmware"
        ]
    if not os.access(hex_path, os.R_OK):
        return [
            f"firmware file is not readable by this account: {hex_path}"
            f" - keep firmware somewhere the station account can read,"
            f" such as /var/lib/progstation/firmware"
        ]
    return []

#: Ordered workflow steps, surfaced to the GUI progress bar.
STEPS = (
    "fixture",
    "signature",
    "fuses",
    "flash",
    "flash_verify",
    "eeprom",
    "eeprom_verify",
    "log",
)

STEP_LABELS = {
    "fixture": "Checking fixture",
    "signature": "Reading device signature",
    "fuses": "Writing fuses",
    "flash": "Programming firmware",
    "flash_verify": "Verifying firmware",
    "eeprom": "Writing EEPROM data",
    "eeprom_verify": "Verifying EEPROM",
    "log": "Saving result",
}


@dataclass
class CycleResult:
    ok: bool
    serial_no: str = ""
    serial_value: Optional[int] = None
    project_name: str = ""
    operator: str = ""
    duration_ms: int = 0
    error_code: str = ""
    error_message: str = ""
    operator_hint: str = ""
    signature: str = ""
    eeprom_hex: str = ""
    log_id: Optional[int] = None
    steps: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def result_text(self) -> str:
        return "PASS" if self.ok else "FAIL"


ProgressCallback = Callable[[str, str, Optional[bool]], None]


class ProgrammingEngine:
    """Runs programming cycles against a project."""

    def __init__(
        self,
        db: Database,
        config: StationConfig,
        backend: AvrdudeBackend,
        io: Optional[StationIO] = None,
    ):
        self.db = db
        self.config = config
        self.backend = backend
        self.io = io
        self.serials = SerialManager(db)

    # ------------------------------------------------------------ validation
    def validate_project(self, project: Any) -> List[str]:
        """Pre-flight checks an administrator sees before releasing a project."""
        problems: List[str] = []
        if not project["MCU"]:
            problems.append("MCU is not set")
        if not project["HexPath"]:
            problems.append("firmware path is not set")
        else:
            problems.extend(_firmware_problems(Path(project["HexPath"])))
        try:
            map_for_project(project).validate()
        except StationError as exc:
            problems.append(f"EEPROM map: {exc}")
        if int(project["SerialEnd"]) and int(project["SerialEnd"]) < int(project["SerialStart"]):
            problems.append("serial range end is below its start")
        return problems

    # ----------------------------------------------------------------- cycle
    def run_cycle(
        self,
        project: Any,
        operator: str,
        *,
        progress: Optional[ProgressCallback] = None,
        mfg_date: Optional[date] = None,
        skip_fixture_check: bool = False,
    ) -> CycleResult:
        started = time.monotonic()
        steps: List[Dict[str, Any]] = []

        def report(step: str, message: str = "", ok: Optional[bool] = None) -> None:
            steps.append(
                {"step": step, "label": STEP_LABELS.get(step, step), "message": message, "ok": ok}
            )
            if progress:
                try:
                    progress(step, message or STEP_LABELS.get(step, step), ok)
                except Exception:  # a broken UI must not abort production
                    log.exception("progress callback failed")

        result = CycleResult(
            ok=False,
            project_name=project["ProjectName"],
            operator=operator,
            steps=steps,
        )
        reservation: Optional[SerialReservation] = None

        if self.io:
            self.io.busy()

        try:
            # SRS section 9 orders fixture detection first: an open fixture must
            # not even reach the serial counter.
            self._step_fixture(report, skip_fixture_check)

            reservation = self.serials.reserve(project)
            result.serial_no = reservation.text
            result.serial_value = reservation.value

            eeprom_block, eeprom_map = self._build_eeprom(
                project, reservation, mfg_date, operator
            )
            result.eeprom_hex = eeprom_block.hex().upper()

            result.signature = self._step_signature(project, report)
            self._step_fuses(project, report)
            self._step_flash(project, report)
            self._step_eeprom(project, eeprom_map, eeprom_block, report)

            result.ok = True
            result.duration_ms = int((time.monotonic() - started) * 1000)
            report("log", f"PASS - serial {reservation.text}", True)

        except StationError as exc:
            result.ok = False
            # The reserved number was never consumed, so do not show it on the
            # FAIL banner -- the next board will use it.
            result.serial_no = ""
            result.serial_value = None
            result.error_code = exc.code
            result.error_message = str(exc)
            result.operator_hint = exc.operator_hint
            result.duration_ms = int((time.monotonic() - started) * 1000)
            if not steps or steps[-1].get("ok") is not False:
                report(steps[-1]["step"] if steps else "fixture", str(exc), False)
            log.warning("cycle FAILED: %s", exc.as_log_entry())

        except Exception as exc:  # pragma: no cover - unexpected, still logged
            result.ok = False
            result.serial_no = ""
            result.serial_value = None
            result.error_code = "E_UNKNOWN"
            result.error_message = f"{type(exc).__name__}: {exc}"
            result.operator_hint = "Unexpected error. Call maintenance."
            result.duration_ms = int((time.monotonic() - started) * 1000)
            log.exception("cycle raised an unexpected exception")

        result.log_id = self._persist(project, result, reservation)

        if self.io:
            self.io.pass_result() if result.ok else self.io.fail_result()
        return result

    # ------------------------------------------------------------ cycle steps
    def _build_eeprom(
        self,
        project: Any,
        reservation: SerialReservation,
        mfg_date: Optional[date],
        operator: str,
    ) -> tuple[bytes, EepromMap]:
        eeprom_map = map_for_project(project)
        context = {
            "serial": reservation.value,
            "serial_text": reservation.text,
            "hw_revision": project["HwRevision"],
            "product_variant": project["ProductVariant"],
            "firmware_version": project["FirmwareVersion"],
            "mfg_date": mfg_date or date.today(),
            "operator": operator,
            "station_id": self.config.station_id,
            "project_name": project["ProjectName"],
        }
        return eeprom_map.build(context), eeprom_map

    def _step_fixture(self, report: ProgressCallback, skip: bool) -> None:
        report("fixture", "", None)
        if skip or not self.config.require_fixture_detect or self.io is None:
            report("fixture", "fixture check skipped" if skip else "fixture ok", True)
            return
        if not self.io.fixture_present():
            raise FixtureOpenError()
        report("fixture", "fixture closed", True)

    def _step_signature(self, project: Any, report: ProgressCallback) -> str:
        report("signature", "", None)
        avr_result, signature = self.backend.read_signature(project["MCU"])
        if not signature:
            # An unusable part id, port or binary also yields no signature.
            # Reporting that as "no device" would send the operator to the
            # fixture for a fault only an administrator can fix.
            cause = classify_failure(avr_result)
            if cause:
                raise ConfigurationError(
                    f"cannot talk to the programmer: {cause}"
                    f" (MCU '{project['MCU']}')",
                    detail=avr_result.tail(),
                )
            raise NoDeviceError(detail=avr_result.tail())
        expected = project["Signature"] or ""
        if expected and not signature_matches(expected, signature):
            raise SignatureMismatchError(
                expected=expected, found=signature, detail=avr_result.tail()
            )
        if not avr_result.ok and expected:
            raise NoDeviceError(detail=avr_result.tail())
        name = signature_name(signature)
        report("signature", f"{signature}{f' ({name})' if name else ''}", True)
        return signature

    def _step_fuses(self, project: Any, report: ProgressCallback) -> None:
        keys = ("FuseLow", "FuseHigh", "FuseExtended", "LockByte")
        if not any(project[k] for k in keys):
            return
        report("fuses", "", None)
        avr_result = self.backend.write_fuses(
            project["MCU"],
            low=project["FuseLow"],
            high=project["FuseHigh"],
            extended=project["FuseExtended"],
            lock=project["LockByte"],
        )
        if avr_result is not None and not avr_result.ok:
            raise FlashProgramError("fuse programming failed", detail=avr_result.tail())
        report("fuses", "fuses written", True)

    def _step_flash(self, project: Any, report: ProgressCallback) -> None:
        hex_path = Path(project["HexPath"] or "")
        problems = _firmware_problems(hex_path)
        if problems:
            raise FirmwareMissingError(problems[0])

        report("flash", "", None)
        avr_result = self.backend.program_flash(project["MCU"], hex_path)
        if not avr_result.ok:
            raise FlashProgramError(detail=avr_result.tail())
        report("flash", "firmware written", True)

        report("flash_verify", "", None)
        avr_result = self.backend.verify_flash(project["MCU"], hex_path)
        if not avr_result.ok:
            raise FlashVerifyError(detail=avr_result.tail())
        report("flash_verify", "firmware verified", True)

    def _step_eeprom(
        self, project: Any, eeprom_map: EepromMap, block: bytes, report: ProgressCallback
    ) -> None:
        hex_text = write_hex([(eeprom_map.base_address, block)])
        with tempfile.TemporaryDirectory(prefix="progstation-") as tmp:
            eeprom_file = Path(tmp) / "eeprom.hex"
            eeprom_file.write_text(hex_text, encoding="utf-8")

            report("eeprom", "", None)
            avr_result = self.backend.program_eeprom(project["MCU"], eeprom_file)
            if not avr_result.ok:
                raise EepromProgramError(detail=avr_result.tail())
            report("eeprom", f"{len(block)} bytes at 0x{eeprom_map.base_address:04X}", True)

            report("eeprom_verify", "", None)
            avr_result = self.backend.verify_eeprom(project["MCU"], eeprom_file)
            if not avr_result.ok:
                raise EepromVerifyError(detail=avr_result.tail())
            report("eeprom_verify", "EEPROM verified", True)

    # ------------------------------------------------------------ persistence
    def _persist(
        self, project: Any, result: CycleResult, reservation: Optional[SerialReservation]
    ) -> Optional[int]:
        entry = {
            "Timestamp": utc_now(),
            "StationId": self.config.station_id,
            "ProjectId": int(project["ProjectId"]),
            "ProjectName": project["ProjectName"],
            # A failed unit never took the serial number, so do not stamp one on
            # its record -- that number still belongs to the next board.
            "SerialNo": result.serial_no if result.ok else "",
            "SerialValue": result.serial_value if result.ok else None,
            "Result": result.result_text,
            "Operator": result.operator,
            "FirmwareVersion": project["FirmwareVersion"],
            "HwRevision": project["HwRevision"],
            "ProductVariant": project["ProductVariant"],
            "Signature": result.signature,
            "DurationMs": result.duration_ms,
            "ErrorCode": result.error_code,
            "Error": result.error_message,
            "EepromHex": result.eeprom_hex if result.ok else "",
        }
        advance = reservation.next_value if (result.ok and reservation) else None
        try:
            return self.db.record_cycle(entry, advance_serial_to=advance)
        except StationError:
            log.exception("failed to persist cycle result")
            # Surface the database failure to the operator rather than letting a
            # unit leave the station with no traceability record.
            result.ok = False
            result.error_code = "E_DATABASE"
            result.error_message = "production record could not be saved"
            result.operator_hint = "Database error. Production halted."
            return None
