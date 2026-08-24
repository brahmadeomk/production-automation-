"""SQLite persistence layer.

One :class:`Database` instance owns one connection.  All writes go through
short transactions so that a power loss mid-cycle can never leave a serial
counter incremented without a matching PASS record -- the counter bump and the
production log row are committed together (see :meth:`record_cycle`).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from ..errors import DatabaseError

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1


def utc_now() -> str:
    """Timestamp format used everywhere in the database (sortable, UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Database:
    def __init__(self, path: str | Path, busy_timeout_s: float = 10.0):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, timeout=busy_timeout_s, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        # Durability matters more than throughput on a station that can lose
        # mains power between cycles.
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_s * 1000)}")
        self.migrate()

    # ------------------------------------------------------------------ core
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                with self._conn:
                    yield self._conn
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                raise DatabaseError(str(exc)) from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            try:
                return list(self._conn.execute(sql, params))
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid if cur.lastrowid is not None else cur.rowcount

    def migrate(self) -> None:
        with self.transaction() as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def vacuum_into(self, destination: str | Path) -> None:
        """Consistent hot copy of the database, used by the backup module."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        with self._lock:
            self._conn.execute("VACUUM INTO ?", (str(destination),))

    # -------------------------------------------------------------- projects
    def list_projects(self, include_inactive: bool = False) -> List[sqlite3.Row]:
        sql = "SELECT * FROM Projects"
        if not include_inactive:
            sql += " WHERE Active = 1"
        sql += " ORDER BY ProjectName COLLATE NOCASE"
        return self.query(sql)

    def get_project(self, project_id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM Projects WHERE ProjectId = ?", (project_id,))

    def get_project_by_name(self, name: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM Projects WHERE ProjectName = ?", (name,))

    _PROJECT_FIELDS = (
        "ProjectName", "MCU", "HexPath", "SNAddress", "MFGAddress", "EepromMap",
        "HwRevision", "ProductVariant", "FirmwareVersion", "SerialPrefix",
        "SerialStart", "SerialEnd", "SerialDigits", "Signature",
        "FuseLow", "FuseHigh", "FuseExtended", "LockByte", "Active",
    )

    def upsert_project(self, values: Dict[str, Any], project_id: Optional[int] = None) -> int:
        payload = {k: v for k, v in values.items() if k in self._PROJECT_FIELDS}
        if isinstance(payload.get("EepromMap"), (dict, list)):
            payload["EepromMap"] = json.dumps(payload["EepromMap"])
        if not payload:
            raise DatabaseError("no project fields supplied")
        with self.transaction() as conn:
            if project_id is None:
                cols = ", ".join(payload)
                marks = ", ".join("?" for _ in payload)
                cur = conn.execute(
                    f"INSERT INTO Projects ({cols}) VALUES ({marks})", list(payload.values())
                )
                project_id = int(cur.lastrowid)
                start = int(payload.get("SerialStart", 1))
                conn.execute(
                    "INSERT INTO SerialCounters (ProjectId, NextSerial, UpdatedAt)"
                    " VALUES (?, ?, ?)",
                    (project_id, start, utc_now()),
                )
            else:
                assigns = ", ".join(f"{c} = ?" for c in payload)
                conn.execute(
                    f"UPDATE Projects SET {assigns}, UpdatedAt = ? WHERE ProjectId = ?",
                    [*payload.values(), utc_now(), project_id],
                )
                conn.execute(
                    "INSERT OR IGNORE INTO SerialCounters (ProjectId, NextSerial, UpdatedAt)"
                    " VALUES (?, ?, ?)",
                    (project_id, int(payload.get("SerialStart", 1)), utc_now()),
                )
        return int(project_id)

    def set_project_active(self, project_id: int, active: bool) -> None:
        self.execute(
            "UPDATE Projects SET Active = ?, UpdatedAt = ? WHERE ProjectId = ?",
            (1 if active else 0, utc_now(), project_id),
        )

    # ----------------------------------------------------------------- users
    def list_users(self, include_inactive: bool = True) -> List[sqlite3.Row]:
        sql = "SELECT * FROM Users"
        if not include_inactive:
            sql += " WHERE Active = 1"
        return self.query(sql + " ORDER BY Username COLLATE NOCASE")

    def get_user(self, username: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM Users WHERE Username = ?", (username,))

    def count_admins(self) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM Users WHERE Role = 'admin' AND Active = 1"
        )
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------ production
    def next_serial_value(self, project_id: int) -> Optional[int]:
        row = self.query_one(
            "SELECT NextSerial FROM SerialCounters WHERE ProjectId = ?", (project_id,)
        )
        return int(row["NextSerial"]) if row else None

    def set_next_serial(self, project_id: int, value: int) -> None:
        self.execute(
            "INSERT INTO SerialCounters (ProjectId, NextSerial, UpdatedAt) VALUES (?, ?, ?)"
            " ON CONFLICT(ProjectId) DO UPDATE SET NextSerial = excluded.NextSerial,"
            " UpdatedAt = excluded.UpdatedAt",
            (project_id, int(value), utc_now()),
        )

    def record_cycle(self, entry: Dict[str, Any], *, advance_serial_to: Optional[int] = None) -> int:
        """Append a production record, optionally advancing the serial counter.

        Both statements share one transaction: the serial number can only move
        forward if its PASS record is durably written, which is the guarantee
        SRS section 10 asks for.
        """
        columns = (
            "Timestamp", "StationId", "ProjectId", "ProjectName", "SerialNo",
            "SerialValue", "Result", "Operator", "FirmwareVersion", "HwRevision",
            "ProductVariant", "Signature", "DurationMs", "ErrorCode", "Error",
            "EepromHex",
        )
        row = {c: entry.get(c) for c in columns}
        row["Timestamp"] = row["Timestamp"] or utc_now()
        for c in ("StationId", "ProjectName", "Operator", "FirmwareVersion",
                  "HwRevision", "ProductVariant", "Signature", "ErrorCode",
                  "Error", "EepromHex"):
            row[c] = row[c] or ""
        row["DurationMs"] = int(row["DurationMs"] or 0)
        with self.transaction() as conn:
            cur = conn.execute(
                f"INSERT INTO ProductionLog ({', '.join(columns)})"
                f" VALUES ({', '.join('?' for _ in columns)})",
                [row[c] for c in columns],
            )
            if advance_serial_to is not None and row["ProjectId"] is not None:
                conn.execute(
                    "UPDATE SerialCounters SET NextSerial = ?, LastIssued = ?, UpdatedAt = ?"
                    " WHERE ProjectId = ?",
                    (int(advance_serial_to), row["SerialValue"], utc_now(), row["ProjectId"]),
                )
            return int(cur.lastrowid)

    def search_production(
        self,
        *,
        serial: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        operator: Optional[str] = None,
        firmware_version: Optional[str] = None,
        result: Optional[str] = None,
        project_id: Optional[int] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> List[sqlite3.Row]:
        """Search the production log (SRS section 11)."""
        clauses: List[str] = []
        params: List[Any] = []
        if serial:
            clauses.append("SerialNo LIKE ?")
            params.append(f"%{serial}%")
        if date_from:
            clauses.append("Timestamp >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("Timestamp <= ?")
            params.append(date_to)
        if operator:
            clauses.append("Operator = ?")
            params.append(operator)
        if firmware_version:
            clauses.append("FirmwareVersion = ?")
            params.append(firmware_version)
        if result:
            clauses.append("Result = ?")
            params.append(result.upper())
        if project_id is not None:
            clauses.append("ProjectId = ?")
            params.append(project_id)
        sql = "SELECT * FROM ProductionLog"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY Timestamp DESC, LogId DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        return self.query(sql, params)

    def serial_history(self, serial_no: str) -> List[sqlite3.Row]:
        return self.query(
            "SELECT * FROM ProductionLog WHERE SerialNo = ? ORDER BY Timestamp", (serial_no,)
        )

    # ----------------------------------------------------------------- audit
    def audit(self, username: str, action: str, target: str = "", detail: str = "") -> None:
        self.execute(
            "INSERT INTO AuditLog (Timestamp, Username, Action, Target, Detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (utc_now(), username or "", action, target, detail),
        )

    def list_audit(self, limit: int = 200) -> List[sqlite3.Row]:
        return self.query(
            "SELECT * FROM AuditLog ORDER BY Timestamp DESC, AuditId DESC LIMIT ?", (limit,)
        )

    def log_backup(self, destination: str, ok: bool, size: int = 0, detail: str = "") -> None:
        self.execute(
            "INSERT INTO BackupLog (Timestamp, Destination, Result, Bytes, Detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (utc_now(), destination, "PASS" if ok else "FAIL", int(size), detail),
        )

    def last_backup(self) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM BackupLog ORDER BY BackupId DESC LIMIT 1")


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]
