"""Shared stand-ins: the clock, and the SDK's restore route as `connect`
declares it on an edge plugin's host.

The sidecar is stood in for by the SDK's own suites' `Recorder` (the
plugin's typed operations, recorded rather than sent, through the SDK's real
conversion), and OANDA by the synthetic OANDA (src/oanda/synthetic.py).
SIMULATED ONLY: nothing in the tests reaches OANDA, practice or live, or
any network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from meridian import edge

from oanda.page import pages

# The restore route the SDK declares on every edge plugin's host as it
# connects (contract v16), as __main__'s connect does.
edge._restore_route(pages, ())

# A Tuesday, 15:00 UTC: 11:00 in New York, before the day's 17:00 roll.
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)


class Clock:
    """A clock a test moves."""

    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def clock(at: datetime = NOW) -> Callable[[], datetime]:
    return Clock(at)
