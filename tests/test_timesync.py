"""The clock is chased in a fixed order: plant network, internet, then RTC.

A Pi has no battery-backed clock, so left alone it boots believing it is
whenever it was last shut down -- and every production record it writes
carries that wrong time.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from progstation.config import TimeConfig
from progstation.hw import timesync
from progstation.hw.timesync import (
    SOURCE_INTERNET, SOURCE_LAN, SOURCE_NONE, SOURCE_RTC, TimeKeeper,
)

TRUE_TIME = datetime(2026, 9, 11, 9, 2, 5, tzinfo=timezone.utc)
#: What the station believes before it is corrected -- a day behind.
WRONG_NOW = TRUE_TIME - timedelta(days=1)


@pytest.fixture
def config():
    return TimeConfig(
        lan_servers=["ntp.plant.local"],
        internet_servers=["pool.ntp.org"],
        interval_hours=24,
    )


class FakeRun:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        failed = any(token in self.fail for token in command)
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "")

    def ran(self, *tokens):
        return [c for c in self.calls if all(t in " ".join(c) for t in tokens)]


def keeper(config, *, answers=None, run=None, rtc=True, monkeypatch=None):
    answers = answers or {}
    if monkeypatch is not None:
        monkeypatch.setattr(timesync, "rtc_present", lambda *a, **k: rtc)
        monkeypatch.setattr(timesync.shutil, "which", lambda name: "/sbin/" + name)
    return TimeKeeper(
        config,
        ntp=lambda server: answers.get(server),
        run=run or FakeRun(),
        now=lambda: WRONG_NOW,
    )


# --------------------------------------------------------------- preference
def test_the_plant_network_is_preferred_over_the_internet(config, monkeypatch):
    """Records are compared against other equipment on that network, so
    agreeing with it matters more than absolute accuracy."""
    reading = keeper(
        config,
        answers={"ntp.plant.local": TRUE_TIME, "pool.ntp.org": TRUE_TIME},
        monkeypatch=monkeypatch,
    ).sync()
    assert reading.ok
    assert reading.source == SOURCE_LAN
    assert reading.server == "ntp.plant.local"


def test_the_internet_is_used_when_the_plant_server_is_silent(config, monkeypatch):
    reading = keeper(
        config, answers={"pool.ntp.org": TRUE_TIME}, monkeypatch=monkeypatch
    ).sync()
    assert reading.ok
    assert reading.source == SOURCE_INTERNET


def test_the_rtc_is_the_last_resort(config, monkeypatch):
    monkeypatch.setattr(timesync, "read_rtc", lambda **kw: TRUE_TIME)
    reading = keeper(config, answers={}, monkeypatch=monkeypatch).sync()
    assert reading.ok
    assert reading.source == SOURCE_RTC
    assert "no time server answered" in reading.detail


def test_no_source_at_all_is_reported_plainly(config, monkeypatch):
    monkeypatch.setattr(timesync, "read_rtc", lambda **kw: None)
    reading = keeper(config, answers={}, rtc=False, monkeypatch=monkeypatch).sync()
    assert not reading.ok
    assert reading.source == SOURCE_NONE
    assert "timestamps will be wrong" in reading.detail


def test_every_server_in_a_list_is_tried(monkeypatch):
    config = TimeConfig(
        lan_servers=["dead.one", "dead.two", "alive.three"], internet_servers=[]
    )
    reading = keeper(
        config, answers={"alive.three": TRUE_TIME}, monkeypatch=monkeypatch
    ).sync()
    assert reading.ok
    assert reading.server == "alive.three"


# ------------------------------------------------------------------ the RTC
def test_the_rtc_is_written_when_network_time_is_found(config, monkeypatch):
    """The whole reason for fitting one: carry this across a power cut."""
    run = FakeRun()
    reading = keeper(
        config, answers={"ntp.plant.local": TRUE_TIME}, run=run, monkeypatch=monkeypatch
    ).sync()
    assert reading.ok
    assert run.ran("hwclock", "--systohc"), "the hardware clock was not updated"
    assert "hardware clock updated" in reading.detail


def test_the_rtc_is_not_written_when_the_time_came_from_the_rtc(config, monkeypatch):
    """Writing it back to itself proves nothing and hides that it is adrift."""
    monkeypatch.setattr(timesync, "read_rtc", lambda **kw: TRUE_TIME)
    run = FakeRun()
    keeper(config, answers={}, run=run, monkeypatch=monkeypatch).sync()
    assert not run.ran("hwclock", "--systohc")


def test_a_station_with_no_rtc_still_syncs(config, monkeypatch):
    run = FakeRun()
    reading = keeper(
        config, answers={"ntp.plant.local": TRUE_TIME}, run=run, rtc=False,
        monkeypatch=monkeypatch,
    ).sync()
    assert reading.ok
    assert not run.ran("hwclock", "--systohc")


def test_a_failed_rtc_write_does_not_fail_the_sync(config, monkeypatch):
    """The system clock is right either way; only the power-cut carry is lost."""
    run = FakeRun(fail={"--systohc"})
    reading = keeper(
        config, answers={"ntp.plant.local": TRUE_TIME}, run=run, monkeypatch=monkeypatch
    ).sync()
    assert reading.ok
    assert "could not be written" in reading.detail


# ------------------------------------------------------------ setting the clock
def test_the_clock_is_set_when_it_is_out(config, monkeypatch):
    run = FakeRun()
    keeper(config, answers={"ntp.plant.local": TRUE_TIME}, run=run,
           monkeypatch=monkeypatch).sync()
    assert run.ran("date", "--set"), "the system clock was never corrected"


def test_a_clock_already_right_is_left_alone(config, monkeypatch):
    """Stepping the clock daily for a fraction of a second gains nothing and
    lands on whatever is mid-cycle."""
    run = FakeRun()
    subject = TimeKeeper(
        config,
        ntp=lambda server: TRUE_TIME if server == "ntp.plant.local" else None,
        run=run,
        now=lambda: TRUE_TIME,          # already correct
    )
    monkeypatch.setattr(timesync, "rtc_present", lambda *a, **k: True)
    monkeypatch.setattr(timesync.shutil, "which", lambda name: "/sbin/" + name)
    reading = subject.sync()
    assert reading.ok
    assert not run.ran("date", "--set")


def test_being_unable_to_set_the_clock_names_the_cause(config, monkeypatch):
    """Unguessable otherwise: the station runs as an unprivileged account."""
    run = FakeRun(fail={"--set"})
    reading = keeper(
        config, answers={"ntp.plant.local": TRUE_TIME}, run=run, monkeypatch=monkeypatch
    ).sync()
    assert not reading.ok
    assert "permission" in reading.detail


def test_a_day_of_drift_is_flagged_as_suspect(config, monkeypatch):
    """Worth saying: records written before this point carry the wrong time."""
    reading = keeper(
        config, answers={"ntp.plant.local": TRUE_TIME}, monkeypatch=monkeypatch
    ).sync()
    assert reading.suspect
    assert reading.drift_s == pytest.approx(-86400, abs=1)


def test_disabled_sync_does_nothing(monkeypatch):
    run = FakeRun()
    reading = keeper(
        TimeConfig(enabled=False), answers={}, run=run, monkeypatch=monkeypatch
    ).sync()
    assert not reading.ok
    assert "disabled" in reading.detail
    assert not run.calls


def test_sync_never_raises(config, monkeypatch):
    """It runs on a background thread; an exception there is invisible."""
    def explode(server):
        raise OSError("network is down")

    subject = TimeKeeper(config, ntp=explode, run=FakeRun(), now=lambda: WRONG_NOW)
    reading = subject.sync()
    assert not reading.ok


# ------------------------------------------------------------- the NTP packet
def test_ntp_reply_is_decoded(monkeypatch):
    import socket as socket_module
    import struct

    seconds = int(TRUE_TIME.timestamp()) + timesync._NTP_DELTA
    packet = bytearray(48)
    packet[40:44] = struct.pack("!I", seconds)
    packet[44:48] = struct.pack("!I", 0)

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, _):
            pass

        def sendto(self, *a):
            pass

        def recvfrom(self, _):
            return bytes(packet), ("ntp", 123)

    monkeypatch.setattr(socket_module, "socket", FakeSocket)
    assert timesync.query_ntp("ntp.plant.local") == TRUE_TIME


def test_a_silent_server_returns_nothing(monkeypatch):
    import socket as socket_module

    class Timeout:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, _):
            pass

        def sendto(self, *a):
            raise OSError("no route to host")

        def recvfrom(self, _):
            raise AssertionError("should not get here")

    monkeypatch.setattr(socket_module, "socket", Timeout)
    assert timesync.query_ntp("ntp.plant.local") is None
