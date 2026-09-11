"""Application context: wires configuration, database, hardware and services.

Both the touchscreen GUI and the CLI build one of these, so they always agree
about which database, backend and pin map are in use.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from .backup.smb import BackupManager
from .config import StationConfig, load_config
from .core.avrdude import AvrdudeBackend, SimulatedAvrdude
from .core.programmer import ProgrammingEngine
from .core.serials import SerialManager
from .db.database import Database
from .hw.gpio import available_backend
from .hw.station_io import StationIO
from .reports.engine import ReportEngine
from .security.auth import AuthManager, Session

from .hw.timesync import TimeKeeper
from .timeutil import local as local_time

log = logging.getLogger(__name__)


def configure_logging(config: StationConfig, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(stream)
    log_dir = Path(config.log_dir)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "progstation.log", maxBytes=5 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
    except OSError as exc:  # a read-only /var must not stop production
        log.warning("file logging disabled: %s", exc)


class StationApp:
    def __init__(
        self,
        config: Optional[StationConfig] = None,
        *,
        simulate: bool = False,
        with_io: bool = True,
    ):
        self.config = config or load_config()
        self.config.ensure_directories()
        self.simulate = simulate
        if simulate:
            # --simulate means "no hardware required", so it has to cover the
            # panel as well as the programmer.  Without this the station still
            # tries to claim real GPIO and fails on a machine that has pins but
            # no permission to drive them.
            self.config.gpio.backend = "simulated"

        self.db = Database(self.config.database.path, self.config.database.busy_timeout_s)
        self.auth = AuthManager(self.db, self.config.security)
        self.serials = SerialManager(self.db)
        self.reports = ReportEngine(self.db, day_start_hour=self.config.reports.day_start_hour)

        # SRS section 5 defines the pin map under `gpio`; keep avrdude's copy of
        # the reset line in step so the two can never drift apart.
        self.config.avrdude.reset_gpio = self.config.gpio.reset
        self.config.avrdude.gpiochip = f"/dev/gpiochip{self.config.gpio.chip}"

        self.backend: AvrdudeBackend = (
            SimulatedAvrdude(self.config.avrdude)
            if simulate
            else AvrdudeBackend(self.config.avrdude)
        )
        self.io: Optional[StationIO] = StationIO(self.config.gpio) if with_io else None
        if simulate and self.io:
            # Nothing physical to close the fixture on a bench run.
            self.io.simulate_fixture(True)

        self.engine = ProgrammingEngine(self.db, self.config, self.backend, self.io)
        self.backup = BackupManager(
            self.config.backup,
            self.db,
            extra_dirs=[self.config.reports.export_dir, self.config.log_dir],
            station_id=self.config.station_id,
        )
        self.clock = TimeKeeper(self.config.time)
        self.session: Optional[Session] = None

    # ------------------------------------------------------------------ misc
    def bootstrap_admin(self) -> Optional[str]:
        """Create the first administrator on a fresh database."""
        return self.auth.ensure_default_admin()

    def health(self) -> dict:
        """Everything the status bar and the ``status`` command report."""
        # Only program/gui/selftest open the panel.  For the others, say which
        # backend would be used rather than a bare "none", which reads like a
        # fault when it only means "this command did not touch the GPIO".
        if self.io is not None:
            io_backend = self.io.backend.name
            gpio_simulated = self.io.simulated
        else:
            probed = available_backend()
            io_backend = f"{probed} (not opened by this command)"
            gpio_simulated = probed == "simulated"
        return {
            "station_id": self.config.station_id,
            "config": self.config.source_path or "built-in defaults",
            "database": self.db.path,
            "avrdude": self.backend.__class__.__name__,
            "avrdude_available": self.backend.is_available(),
            "avrdude_version": self.backend.version() if self.backend.is_available() else "",
            "gpio_backend": io_backend,
            "simulated": self.simulate or gpio_simulated,
            "projects": len(self.db.list_projects()),
            "users": len(self.db.list_users()),
            "backup": self.backup.status(),
            "time_source": self.time_status(),
        }

    def time_status(self) -> str:
        """What the clock is currently trusting, for the status line."""
        reading = self.clock.last
        if reading is None:
            return "not checked yet"
        if not reading.ok:
            return reading.detail
        stamp = local_time(reading.utc) if reading.utc else ""
        suffix = "  (clock was well out - check the records around it)" \
            if reading.suspect else ""
        return f"{reading.source} at {stamp}{suffix}"

    def close(self) -> None:
        self.clock.stop_scheduler()
        self.backup.stop_scheduler()
        if self.io:
            self.io.close()
        self.db.close()

    def __enter__(self) -> "StationApp":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
