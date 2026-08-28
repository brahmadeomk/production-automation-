"""Scheduled backup of database, reports and logs to an SMB share (SRS section 15).

The database is copied with SQLite's ``VACUUM INTO`` rather than a filesystem
copy, so the backup is a consistent snapshot even if a cycle is committing at
that moment.  Failures are logged to ``BackupLog`` and surfaced on the status
bar rather than interrupting production (SRS section 16).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from ..config import BackupConfig
from ..db.database import Database
from ..errors import BackupError

log = logging.getLogger(__name__)


@dataclass
class BackupResult:
    ok: bool
    destination: str = ""
    bytes_copied: int = 0
    files: int = 0
    detail: str = ""
    duration_ms: int = 0


class BackupManager:
    def __init__(
        self,
        config: BackupConfig,
        db: Database,
        *,
        extra_dirs: Optional[List[str]] = None,
        station_id: str = "",
    ):
        self.config = config
        self.db = db
        self.extra_dirs = extra_dirs or []
        self.station_id = station_id or "station"
        self._timer: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- mount
    def is_mounted(self) -> bool:
        mount_point = Path(self.config.mount_point)
        if not mount_point.is_dir():
            return False
        try:
            return mount_point.is_mount()
        except OSError:  # pragma: no cover - stale mount
            return False

    def mount(self) -> None:
        """Mount the SMB share if we are managing the mount ourselves."""
        cfg = self.config
        if not cfg.manage_mount:
            # The share is mounted by /etc/fstab or an automounter.  The
            # configured path is often a *subdirectory* of that mount, so
            # require a writable directory rather than a mount point.
            target = Path(cfg.mount_point)
            if not target.is_dir():
                raise BackupError(f"{cfg.mount_point} does not exist or is not mounted")
            if not os.access(target, os.W_OK):
                raise BackupError(f"{cfg.mount_point} is not writable")
            return
        if self.is_mounted():
            return
        if not cfg.share:
            raise BackupError("no SMB share configured")

        Path(cfg.mount_point).mkdir(parents=True, exist_ok=True)
        options = [cfg.mount_options] if cfg.mount_options else []
        if cfg.credentials_file:
            options.append(f"credentials={cfg.credentials_file}")
        elif cfg.username:
            options.append(f"username={cfg.username}")
            if cfg.domain:
                options.append(f"domain={cfg.domain}")
        command = ["mount", "-t", "cifs", cfg.share, cfg.mount_point, "-o", ",".join(options)]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackupError(f"mount failed: {exc}") from exc
        if proc.returncode != 0:
            # The password never appears in `command`; it lives in the
            # credentials file, so this message is safe to log.
            raise BackupError(f"mount failed: {(proc.stderr or proc.stdout).strip()}")

    def unmount(self) -> None:
        if self.config.manage_mount and self.is_mounted():
            subprocess.run(
                ["umount", self.config.mount_point], capture_output=True, check=False
            )

    # ---------------------------------------------------------------- backup
    def target_directory(self) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        return (
            Path(self.config.mount_point)
            / self.config.subdirectory
            / self.station_id
            / stamp
        )

    def run_backup(self) -> BackupResult:
        """Copy a database snapshot, exports and logs to the share."""
        started = time.monotonic()
        with self._lock:
            destination = ""
            try:
                self.mount()
                destination_dir = self.target_directory()
                destination = str(destination_dir)
                destination_dir.mkdir(parents=True, exist_ok=True)

                copied = 0
                files = 0

                with tempfile.TemporaryDirectory(prefix="progstation-backup-") as tmp:
                    snapshot = Path(tmp) / f"{Path(self.db.path).stem}.db"
                    self.db.vacuum_into(snapshot)
                    target = destination_dir / snapshot.name
                    shutil.copy2(snapshot, target)
                    copied += target.stat().st_size
                    files += 1

                for source in self.extra_dirs:
                    source_path = Path(source)
                    if not source_path.is_dir():
                        continue
                    target_dir = destination_dir / source_path.name
                    shutil.copytree(source_path, target_dir, dirs_exist_ok=True)
                    for item in target_dir.rglob("*"):
                        if item.is_file():
                            copied += item.stat().st_size
                            files += 1

                self._restore_database_ownership()
                pruned = self.prune()
                detail = f"{files} file(s)"
                if pruned:
                    detail += f", pruned {pruned} old backup(s)"
                self.db.log_backup(destination, True, copied, detail)
                return BackupResult(
                    True,
                    destination,
                    copied,
                    files,
                    detail,
                    int((time.monotonic() - started) * 1000),
                )

            except Exception as exc:
                message = str(exc) or type(exc).__name__
                log.error("backup failed: %s", message)
                try:
                    self.db.log_backup(destination, False, 0, message)
                except Exception:  # pragma: no cover
                    log.exception("could not record the backup failure")
                return BackupResult(
                    False,
                    destination,
                    detail=message,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

    def _restore_database_ownership(self) -> None:
        """Undo root-owned SQLite sidecar files after a backup run as root.

        A manual ``sudo progstation backup run`` opens the production database,
        and in WAL mode that can create ``-wal``/``-shm`` files owned by root.
        Left behind, they stop the station's own account from writing its
        database.  Put them back to whoever owns the database file.
        """
        if os.geteuid() != 0:
            return
        database = Path(self.db.path)
        if not database.exists():
            return
        try:
            stat = database.stat()
            for sidecar in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
                if sidecar.exists():
                    os.chown(sidecar, stat.st_uid, stat.st_gid)
        except OSError as exc:  # pragma: no cover - best effort
            log.warning("could not restore database ownership: %s", exc)

    def prune(self) -> int:
        """Delete backup folders older than ``keep_days``.  Returns the count."""
        if not self.config.keep_days:
            return 0
        root = Path(self.config.mount_point) / self.config.subdirectory / self.station_id
        if not root.is_dir():
            return 0
        cutoff = datetime.now() - timedelta(days=self.config.keep_days)
        removed = 0
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            try:
                when = datetime.strptime(entry.name, "%Y-%m-%d_%H%M%S")
            except ValueError:
                continue  # not one of ours; leave it alone
            if when < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed

    # -------------------------------------------------------------- schedule
    def start_scheduler(self, on_result: Optional[Callable[[BackupResult], None]] = None) -> None:
        """Run :meth:`run_backup` every ``interval_minutes`` in the background."""
        if not self.config.enabled or self._timer is not None:
            return
        interval = max(1, int(self.config.interval_minutes)) * 60
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(interval):
                result = self.run_backup()
                if on_result:
                    try:
                        on_result(result)
                    except Exception:  # pragma: no cover
                        log.exception("backup callback failed")

        self._timer = threading.Thread(target=loop, daemon=True, name="backup-scheduler")
        self._timer.start()
        log.info("backup scheduler started (every %s minutes)", self.config.interval_minutes)

    def stop_scheduler(self) -> None:
        self._stop.set()
        if self._timer:
            self._timer.join(timeout=2.0)
            self._timer = None

    def status(self) -> str:
        if not self.config.enabled:
            return "Backup: disabled"
        last = self.db.last_backup()
        if not last:
            return "Backup: never run"
        return f"Backup: {last['Result']} at {last['Timestamp'][:19].replace('T', ' ')}"
