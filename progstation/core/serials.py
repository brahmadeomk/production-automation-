"""Serial number allocation (SRS section 10).

Each product keeps an independent counter in ``SerialCounters``.  The station
*reserves* a number before programming and only *commits* it after a PASS -- a
failed cycle leaves the counter untouched, so the next board reuses the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..db.database import Database
from ..errors import SerialRangeExhaustedError


@dataclass(frozen=True)
class SerialReservation:
    project_id: int
    value: int
    text: str

    @property
    def next_value(self) -> int:
        return self.value + 1


def format_serial(value: int, prefix: str = "", digits: int = 6) -> str:
    return f"{prefix}{value:0{max(digits, 1)}d}"


class SerialManager:
    def __init__(self, db: Database):
        self.db = db

    def peek(self, project: Any) -> SerialReservation:
        """Next serial for *project* without touching the counter."""
        project_id = int(project["ProjectId"])
        value = self.db.next_serial_value(project_id)
        if value is None:
            value = int(project["SerialStart"])
            self.db.set_next_serial(project_id, value)
        end = int(project["SerialEnd"])
        if end and value > end:
            raise SerialRangeExhaustedError(
                f"project '{project['ProjectName']}' reached its last serial ({end})"
            )
        return SerialReservation(
            project_id=project_id,
            value=value,
            text=format_serial(
                value, project["SerialPrefix"] or "", int(project["SerialDigits"] or 6)
            ),
        )

    # ``reserve`` is an alias that reads better at the call site in the
    # workflow: nothing is persisted until the PASS commit.
    reserve = peek

    def commit(self, reservation: SerialReservation) -> None:
        """Advance the counter after a PASS.

        The workflow normally commits atomically with the log row via
        ``Database.record_cycle``; this method exists for repair tooling and
        for the admin screen.
        """
        self.db.set_next_serial(reservation.project_id, reservation.next_value)

    def set_next(self, project_id: int, value: int, *, actor: str = "") -> None:
        """Administrative override of a counter (audited)."""
        if value < 0:
            raise SerialRangeExhaustedError("serial number must not be negative")
        previous = self.db.next_serial_value(project_id)
        self.db.set_next_serial(project_id, value)
        self.db.audit(
            actor, "serial.set_next", str(project_id), f"{previous} -> {value}"
        )

    def remaining(self, project: Any) -> Optional[int]:
        """How many serials are left in the configured range, or ``None``."""
        end = int(project["SerialEnd"])
        if not end:
            return None
        value = self.db.next_serial_value(int(project["ProjectId"]))
        if value is None:
            return None
        return max(0, end - value + 1)
