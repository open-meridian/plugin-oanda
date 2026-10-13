"""OANDA's times, read exactly, and the trading day OANDA states.

OANDA writes each time as RFC 3339 in UTC with up to nine places of a
second (`2026-10-13T14:00:00.123456789Z`), asked for with the
`Accept-Datetime-Format: RFC3339` header. `ns` reads one to the nanosecond,
never through a float.

**OANDA's trading day.** OANDA's day ends at 17:00 America/New_York: its
daily candles align there by default (`dailyAlignment` 17 in
`alignmentTimezone` America/New_York, its pricing reference) and a position
held past 17:00 ET is financed for the next day (its US financing FAQ). So
the trading day a time falls on, as OANDA states it, is New York's date, the
next one from 17:00. `trading_day` is that, OANDA's own convention, used
where OANDA states a time and the contract asks a date of it. A value date
is never worked out here: it is core's, through the date read
(`plugin.compute_date`).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
#: The hour OANDA's trading day ends, in New York.
DAY_ENDS = 17

_RFC3339 = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def ns(text: object) -> int:
    """An RFC 3339 time as nanoseconds since the epoch; 0 for none."""
    if not isinstance(text, str):
        return 0
    found = _RFC3339.match(text.strip())
    if found is None:
        return 0
    day, hour, minute, second, fraction, zone = found.groups()
    whole = datetime.fromisoformat(
        f"{day}T{hour}:{minute}:{second}" + ("+00:00" if zone == "Z" else zone)
    )
    nanos = int((fraction or "").ljust(9, "0"))
    return int(whole.timestamp()) * 1_000_000_000 + nanos


def moment(at_ns: int) -> datetime:
    """Nanoseconds as an aware UTC datetime, to the microsecond."""
    return datetime.fromtimestamp(at_ns // 1_000_000_000, UTC) + timedelta(
        microseconds=(at_ns % 1_000_000_000) // 1000
    )


def of(when: datetime) -> int:
    """An aware datetime as nanoseconds since the epoch."""
    return int(when.timestamp()) * 1_000_000_000 + when.microsecond * 1000


def rfc3339(at_ns: int) -> str:
    """Nanoseconds as OANDA writes a time."""
    whole = datetime.fromtimestamp(at_ns // 1_000_000_000, UTC)
    return f"{whole:%Y-%m-%dT%H:%M:%S}.{at_ns % 1_000_000_000:09d}Z"


def trading_day(at_ns: int) -> date:
    """The trading day a time falls on, as OANDA states its day: New York's
    date, the next one from 17:00 New York."""
    local = moment(at_ns).astimezone(NEW_YORK)
    return (local + timedelta(days=1)).date() if local.hour >= DAY_ENDS else local.date()
