"""Turning stored timestamps into the time an operator sees on the wall.

The station stores every timestamp in UTC (``2026-09-11T09:02:05.123Z``).
That is the right thing to store -- it is unambiguous, it survives a change of
timezone, and two stations in different countries can have their logs merged.

It is the wrong thing to *show*.  A station in India displaying raw UTC is
five and a half hours behind the clock on the wall, so every entry made before
half past five in the morning carries yesterday's date.  An operator reading
that has no reason to think it is a display convention rather than a wrong
clock, and a production record whose dates look wrong is worthless as
evidence.

So: store UTC, display local.  Exports carry the offset as well, because they
leave the station and are read by people who cannot ask which zone it was in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

#: What to show where a timestamp is missing or unreadable.
BLANK = ""


def parse(value) -> Optional[datetime]:
    """Read a stored timestamp as an aware UTC datetime.

    Returns None for anything unparseable rather than raising: a malformed
    timestamp in one old row must not take down the screen showing it.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A stored value without a zone is UTC by this application's convention.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def to_local(value) -> Optional[datetime]:
    parsed = parse(value)
    return parsed.astimezone() if parsed else None


def local(value, *, seconds: bool = True) -> str:
    """A stored UTC timestamp as local time, for the screen."""
    moment = to_local(value)
    if moment is None:
        return BLANK
    return moment.strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def local_with_offset(value) -> str:
    """As above, plus the UTC offset -- for files that leave the station."""
    moment = to_local(value)
    if moment is None:
        return BLANK
    offset = moment.strftime("%z")
    return moment.strftime("%Y-%m-%d %H:%M:%S ") + f"{offset[:3]}:{offset[3:]}"


def zone_name() -> str:
    """What to call the station's timezone in an export header."""
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    name = now.tzname() or "local"
    return f"{name} (UTC{offset[:3]}:{offset[3:]})"
