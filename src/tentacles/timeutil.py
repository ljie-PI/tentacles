"""Time helpers. The database stores UTC ISO8601 with fixed-width microseconds,
so stored timestamps compare correctly as strings."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import overload
from zoneinfo import ZoneInfo

_DURATION = re.compile(r"^(\d+)([smhdw])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """Naive datetimes are taken to be UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@overload
def to_iso(dt: datetime) -> str: ...
@overload
def to_iso(dt: None) -> None: ...
def to_iso(dt: datetime | None) -> str | None:
    return None if dt is None else to_utc(dt).isoformat(timespec="microseconds")


@overload
def from_iso(s: str) -> datetime: ...
@overload
def from_iso(s: None) -> None: ...
def from_iso(s: str | None) -> datetime | None:
    return None if s is None else to_utc(datetime.fromisoformat(s))


def is_duration(s: str) -> bool:
    return bool(_DURATION.match(s.strip()))


def parse_duration(s: str) -> timedelta:
    """'90s' / '30m' / '24h' / '7d' / '2w' -> timedelta."""
    m = _DURATION.match(s.strip())
    if not m:
        raise ValueError(f"invalid duration {s!r} (expected e.g. 30m, 24h, 7d)")
    return timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})


def parse_since(s: str, now: datetime, tz: ZoneInfo | None = None) -> datetime:
    """A duration back from `now`, or an ISO8601 timestamp. A timestamp without
    an offset is read in `tz` (the configured display timezone), else UTC."""
    if is_duration(s):
        return now - parse_duration(s)
    try:
        # Python 3.10 的 fromisoformat 不认 "Z"，而 JSON 输出正是这种格式
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)
        return to_utc(dt.replace(tzinfo=tz) if dt.tzinfo is None and tz else dt)
    except ValueError:
        raise ValueError(f"invalid time {s!r} (expected a duration like 24h or an ISO timestamp)")
