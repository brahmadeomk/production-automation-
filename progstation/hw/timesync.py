"""Keep the station's clock right, in the order a factory can rely on.

Every production record is timestamped, so a wrong clock corrupts the
traceability evidence the station exists to produce -- and a Pi has no
battery-backed clock of its own.  Left alone it boots believing it is still
whenever it was last shut down.

Sources are tried in the order a plant floor should prefer them:

1. **A time server on the local network.**  Closest, works with no route to
   the internet, and is the one the rest of the plant's equipment agrees
   with -- which matters more than being right in absolute terms, because
   records are compared against other systems on that network.
2. **An internet time source**, if the station has a route out.
3. **The hardware RTC**, if a module is fitted.  Not a sync at all but a
   reading: it is what carries the time across a power cut.

Whenever 1 or 2 succeeds the RTC is written back, so the next cold start has
something better than the last shutdown to go on.  That write is the whole
point of having the module.

Nothing here is required for the station to program boards.  A station with
no time source keeps working and says so.
"""
from __future__ import annotations

import logging
import shutil
import struct
import socket
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

log = logging.getLogger(__name__)

#: Seconds between 1900-01-01 (the NTP epoch) and 1970-01-01 (the Unix one).
_NTP_DELTA = 2_208_988_800

#: A clock this far out is treated as wrong rather than drifted, and is worth
#: reporting: it usually means the station came up with no time at all.
SUSPECT_DRIFT_S = 60.0

SOURCE_LAN = "local network time server"
SOURCE_INTERNET = "internet time server"
SOURCE_RTC = "hardware clock"
SOURCE_NONE = "none"


@dataclass
class TimeReading:
    ok: bool
    source: str = SOURCE_NONE
    utc: Optional[datetime] = None
    server: str = ""
    detail: str = ""
    #: How far the system clock was out, in seconds, where that is known.
    drift_s: Optional[float] = None

    @property
    def suspect(self) -> bool:
        return self.drift_s is not None and abs(self.drift_s) >= SUSPECT_DRIFT_S


def query_ntp(server: str, *, timeout: float = 3.0,
              now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
              ) -> Optional[datetime]:
    """Ask one NTP server the time.  Returns None if it does not answer.

    Written out rather than pulled from a library so the station needs no
    package that a locked-down factory image may not carry.
    """
    packet = bytearray(48)
    packet[0] = 0x1B                       # LI 0, version 3, client mode
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(bytes(packet), (server, 123))
            data, _ = sock.recvfrom(48)
    except (OSError, socket.timeout):
        return None
    if len(data) < 48:
        return None
    seconds = struct.unpack("!I", data[40:44])[0]
    fraction = struct.unpack("!I", data[44:48])[0]
    if not seconds:
        return None
    stamp = seconds - _NTP_DELTA + fraction / 2 ** 32
    try:
        return datetime.fromtimestamp(stamp, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def read_rtc(*, run: Callable[..., object] = None) -> Optional[datetime]:
    """Read the hardware clock, if a module is fitted."""
    runner = run or _run
    if shutil.which("hwclock") is None:
        return None
    completed = runner(["hwclock", "--utc", "--show", "--iso-8601=seconds"])
    text = (getattr(completed, "stdout", "") or "").strip()
    if getattr(completed, "returncode", 1) != 0 or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def rtc_present(path: Path = Path("/dev/rtc0")) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _privileged(command: Sequence[str]) -> List[str]:
    """Prefix with sudo unless we are already root.

    Setting the clock needs privilege the station deliberately does not have:
    it runs as an unprivileged service account. install.sh grants exactly two
    commands through sudoers -- date and hwclock -- and nothing else. ``-n``
    so a missing grant fails immediately instead of blocking on a password
    prompt no one is there to answer.
    """
    import os

    if os.geteuid() == 0:
        return list(command)
    return ["sudo", "-n", *command]


def _run(command: Sequence[str], *, timeout: float = 10.0):
    return subprocess.run(
        _privileged(command), capture_output=True, text=True,
        timeout=timeout, check=False,
    )


def set_system_clock(moment: datetime, *, run: Callable[..., object] = None) -> bool:
    runner = run or _run
    stamp = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    completed = runner(["date", "--utc", "--set", stamp])
    return getattr(completed, "returncode", 1) == 0


def write_rtc(*, run: Callable[..., object] = None) -> bool:
    """Copy the system clock into the RTC, so a power cut does not lose it."""
    runner = run or _run
    if shutil.which("hwclock") is None:
        return False
    completed = runner(["hwclock", "--utc", "--systohc"])
    return getattr(completed, "returncode", 1) == 0


class TimeKeeper:
    """Works down the source list, then keeps the RTC in step."""

    def __init__(
        self,
        config,
        *,
        ntp: Callable[[str], Optional[datetime]] = None,
        run: Callable[..., object] = None,
        now: Callable[[], datetime] = None,
    ):
        self.config = config
        self._ntp = ntp or (lambda server: query_ntp(server))
        self._run = run or _run
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.last: Optional[TimeReading] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------- sources
    def _try_servers(self, servers: Sequence[str], source: str) -> Optional[TimeReading]:
        for server in servers:
            if not server:
                continue
            moment = self._ntp(server)
            if moment is not None:
                drift = (self._now() - moment).total_seconds()
                return TimeReading(
                    True, source, moment, server=server, drift_s=drift,
                    detail=f"{server} answered",
                )
            log.info("no reply from %s (%s)", server, source)
        return None

    def sync(self) -> TimeReading:
        """Find the best time available and adopt it.  Never raises."""
        try:
            reading = self._sync()
        except Exception as exc:                       # pragma: no cover
            log.exception("time sync failed")
            reading = TimeReading(False, SOURCE_NONE, detail=str(exc))
        self.last = reading
        return reading

    def _sync(self) -> TimeReading:
        if not getattr(self.config, "enabled", True):
            return TimeReading(False, SOURCE_NONE, detail="time sync is disabled")

        for servers, source in (
            (self.config.lan_servers, SOURCE_LAN),
            (self.config.internet_servers, SOURCE_INTERNET),
        ):
            reading = self._try_servers(servers, source)
            if reading is None:
                continue
            # Only touch the clock when it is actually out: setting it every
            # day for a fraction of a second steps the clock under whatever
            # is mid-cycle for no gain.
            if reading.drift_s is not None and abs(reading.drift_s) >= 1.0:
                if not set_system_clock(reading.utc, run=self._run):
                    reading.detail = (
                        f"{reading.server} answered but the clock could not be "
                        f"set - the station lacks permission (see the polkit "
                        f"and sudoers notes in MAINTENANCE section 2)"
                    )
                    reading.ok = False
                    return reading
            # The reason the RTC is worth having: carry this across a power cut.
            if rtc_present():
                if write_rtc(run=self._run):
                    reading.detail += "; hardware clock updated"
                else:
                    reading.detail += "; hardware clock could not be written"
            return reading

        moment = read_rtc(run=self._run)
        if moment is not None:
            drift = (self._now() - moment).total_seconds()
            return TimeReading(
                True, SOURCE_RTC, moment, drift_s=drift,
                detail="no time server answered; using the hardware clock",
            )

        return TimeReading(
            False, SOURCE_NONE,
            detail=(
                "no time server answered and no hardware clock is fitted - "
                "timestamps will be wrong until the station reaches a time "
                "server"
            ),
        )

    # ----------------------------------------------------------- scheduling
    def start_scheduler(self, on_result: Optional[Callable[[TimeReading], None]] = None) -> None:
        """Sync now, then once every ``interval_hours``."""
        if not getattr(self.config, "enabled", True) or self._thread is not None:
            return
        interval = max(1, int(self.config.interval_hours)) * 3600
        self._stop.clear()

        def loop() -> None:
            # At start-up first: a station that has just been powered on is
            # exactly the one whose clock is wrong.
            while True:
                result = self.sync()
                if on_result:
                    try:
                        on_result(result)
                    except Exception:              # pragma: no cover
                        log.exception("time sync callback failed")
                if self._stop.wait(interval):
                    return

        self._thread = threading.Thread(
            target=loop, daemon=True, name="time-sync"
        )
        self._thread.start()
        log.info("time sync started (every %s hour(s))", self.config.interval_hours)

    def stop_scheduler(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
