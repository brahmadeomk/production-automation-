"""Station configuration (YAML backed, dataclass typed).

The station ships with a working default configuration that matches the GPIO
allocation in SRS section 5, so a fresh Pi boots into a usable state.  Sites
override values in ``/etc/progstation/station.yaml``; anything absent falls back
to the defaults below.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG_PATHS = (
    Path(os.environ.get("PROGSTATION_CONFIG", "")) if os.environ.get("PROGSTATION_CONFIG") else None,
    Path("/etc/progstation/station.yaml"),
    Path.home() / ".config" / "progstation" / "station.yaml",
    Path(__file__).resolve().parent.parent / "config" / "station.yaml",
)


@dataclass
class GpioConfig:
    """Pin allocation from SRS section 5.

    ``backend`` selects the driver: ``lgpio`` (preferred on Trixie), ``gpiozero``
    or ``simulated``.  ``auto`` probes them in that order, which lets the same
    code run on a developer laptop and on the station.
    """

    backend: str = "auto"
    chip: int = 0
    mosi: int = 10
    miso: int = 9
    sclk: int = 11
    reset: int = 25
    green_led: int = 17
    red_led: int = 27
    buzzer: int = 22
    start_button: int = 23
    fixture_detect: int = 24
    # Both switches are wired to ground with an internal pull-up, so a closed
    # contact reads low.
    button_active_low: bool = True
    fixture_active_low: bool = True
    debounce_ms: int = 40


@dataclass
class AvrdudeConfig:
    binary: str = "avrdude"
    programmer: str = "linuxspi"
    #: SPI device.  avrdude 7.x wants the reset line named in the port itself,
    #: as ``/dev/spidevX.Y:/dev/gpiochipN[:resetno]``; a bare ``/dev/spidev0.0``
    #: is the avrdude 6.x form and 7.x rejects it with "unknown port
    #: specification".  Leave this as the plain device and let ``gpiochip`` and
    #: ``reset_gpio`` below complete it -- the station appends them for you, so
    #: the reset pin stays defined once, under ``gpio``.
    port: str = "/dev/spidev0.0"
    #: GPIO character device holding the reset line (avrdude 7.x linuxspi).
    gpiochip: str = "/dev/gpiochip0"
    #: BCM pin driving the target's RESET.  Kept in step with ``gpio.reset``.
    reset_gpio: int = 25
    #: SPI clock for ISP.  Must stay below F_CPU/4 of the target; 200 kHz is safe
    #: for a 1 MHz factory-fused AVR.
    baudrate: int = 200000
    bitclock: Optional[float] = None
    #: Extra ``avrdude`` flags appended verbatim to every invocation.
    extra_args: List[str] = field(default_factory=list)
    timeout_s: int = 120
    #: Optional extra avrdude config fragment.  Not needed on avrdude 7.x --
    #: the reset line comes from the port -- but avrdude 6.x sites can point
    #: this at a fragment carrying ``reset = <pin>;``.
    config_file: Optional[str] = None
    #: Disable avrdude's own safemode/auto-erase behaviour changes here if a
    #: legacy product needs it.
    disable_auto_erase: bool = False


@dataclass
class DatabaseConfig:
    path: str = "/var/lib/progstation/progstation.db"
    #: Seconds SQLite waits on a locked database before raising.
    busy_timeout_s: float = 10.0


@dataclass
class BackupConfig:
    """SMB network backup (SRS section 15)."""

    enabled: bool = False
    share: str = ""                       # e.g. //fileserver/production
    mount_point: str = "/mnt/progstation-backup"
    subdirectory: str = "progstation"
    username: str = ""
    domain: str = ""
    credentials_file: str = ""            # smb credentials file, chmod 600
    #: How often the scheduler runs, in minutes.  1440 == daily.
    interval_minutes: int = 1440
    #: Retention for backup copies on the share.
    keep_days: int = 90
    #: Mount the share ourselves, or assume /etc/fstab already did.
    manage_mount: bool = True
    mount_options: str = "vers=3.0,uid=0,gid=0,file_mode=0640,dir_mode=0750"


@dataclass
class ReportConfig:
    export_dir: str = "/var/lib/progstation/exports"
    company_name: str = ""
    #: Shift boundary used when grouping "daily" production, 24h clock.
    day_start_hour: int = 0


@dataclass
class SecurityConfig:
    pbkdf2_iterations: int = 240_000
    #: Auto-logout after this many idle minutes (0 disables).
    session_timeout_min: int = 30
    min_password_length: int = 6
    max_failed_logins: int = 5
    lockout_minutes: int = 10


@dataclass
class TimeConfig:
    """Where the station gets the time (SRS section 11 traceability).

    Tried in order: a server on the plant network first, because records are
    compared against other equipment on that network and agreeing with it
    matters more than absolute accuracy; then the internet; then the RTC.
    """
    enabled: bool = True
    #: Time servers on the plant network. Put yours here -- often the domain
    #: controller, the MES host or the gateway.
    lan_servers: List[str] = field(default_factory=lambda: ["ntp.local", "gateway"])
    internet_servers: List[str] = field(default_factory=lambda: [
        "pool.ntp.org", "time.google.com",
    ])
    #: How often to re-check. Daily is plenty for a clock that is written to
    #: an RTC each time.
    interval_hours: int = 24


@dataclass
class StationConfig:
    station_id: str = "STATION-01"
    data_dir: str = "/var/lib/progstation"
    log_dir: str = "/var/log/progstation"
    #: Require the physical start button instead of the on-screen START.
    require_hardware_start: bool = False
    #: Refuse to start a cycle while the fixture-detect switch reads open.
    require_fixture_detect: bool = True
    #: Seconds the PASS/FAIL result stays on screen before the station re-arms.
    result_dwell_s: float = 2.0
    gpio: GpioConfig = field(default_factory=GpioConfig)
    avrdude: AvrdudeConfig = field(default_factory=AvrdudeConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    reports: ReportConfig = field(default_factory=ReportConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    time: TimeConfig = field(default_factory=TimeConfig)
    #: Where this configuration was loaded from (informational).
    source_path: Optional[str] = None

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("source_path", None)
        return data

    def save(self, path: str | os.PathLike) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.to_dict(), fh, sort_keys=False, default_flow_style=False)

    def ensure_directories(self) -> None:
        for p in (
            self.data_dir,
            self.log_dir,
            self.reports.export_dir,
            str(Path(self.database.path).parent),
        ):
            if p:
                Path(p).mkdir(parents=True, exist_ok=True)


_SECTIONS = {
    "gpio": GpioConfig,
    "avrdude": AvrdudeConfig,
    "database": DatabaseConfig,
    "backup": BackupConfig,
    "reports": ReportConfig,
    "security": SecurityConfig,
}


def _build(cls, values: Dict[str, Any]):
    known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    unknown = set(values) - known
    if unknown:
        raise ValueError(
            f"unknown {cls.__name__} option(s): {', '.join(sorted(unknown))}"
        )
    return cls(**values)


def from_dict(data: Dict[str, Any]) -> StationConfig:
    data = copy.deepcopy(data or {})
    # Allow either a flat mapping or one nested under a `station:` key.
    if "station" in data and isinstance(data["station"], dict):
        station = data.pop("station")
        station.update(data)
        data = station
    sections = {}
    for name, cls in _SECTIONS.items():
        sections[name] = _build(cls, data.pop(name, {}) or {})
    top_known = {
        f.name
        for f in StationConfig.__dataclass_fields__.values()  # type: ignore[attr-defined]
    } - set(_SECTIONS)
    unknown = set(data) - top_known
    if unknown:
        raise ValueError(f"unknown station option(s): {', '.join(sorted(unknown))}")
    return StationConfig(**data, **sections)


def load_config(path: str | os.PathLike | None = None) -> StationConfig:
    """Load configuration from *path*, or the first default location that exists."""

    candidates = [Path(path)] if path else [p for p in DEFAULT_CONFIG_PATHS if p]
    for candidate in candidates:
        if candidate.is_file():
            with open(candidate, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            cfg = from_dict(data)
            cfg.source_path = str(candidate)
            return cfg
    if path:
        raise FileNotFoundError(f"configuration file not found: {path}")
    return StationConfig()
