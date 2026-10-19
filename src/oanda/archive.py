"""Past the window: each kind's units moved to the archive, kept or deleted, as
the settings say (contract v16; meridian-design spec/an-edge-plugins-older-
records-move-to-the-archive), as SnapTrade's are.

A unit (raw.py: an account's day of reads, or month of trading exchanges or
of activity records) whose period has ended and whose last record is past
its kind's window is, as the kind's past-the-window setting says:

- **archived**, through the SDK's `plugin.archive_unit`, which writes it to
  the archive, checks it landed, records the move through the sidecar and
  only then removes it from storage; with no archive given or allowed it is
  refused before anything moves, kept, and tried again on the next pass;
- **kept**, left where it is; or
- **deleted**, through `plugin.delete_unit`, which records the deletion
  first, so a refusal inside the deployment's hold keeps it.

Each unit is noted in its account's ledger before it is moved. A unit the
SDK's index says moved already is moved again only where it holds what the
ledger noted. A pass ends by saying what each kind holds in storage on the
heartbeat (`plugin.stored`). The stand-in store moves nothing.

From 0.5.0 the data role's raw records, the kind `prices`, are the SDK's
(`meridian.edge.RawStore`, a unit a UTC day, lake.py), and the same pass
moves them with the SDK's own `tend`, past their window as their settings
say, today's never; what they hold is said beside the other three kinds.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import meridian
from meridian import edge
from meridian.v1 import sidecar_pb2

from .moments import of
from .raw import RawStore, Unit
from .settings import ACTIVITY, ARCHIVED, DELETED, PRICES, RESPONSES, TRADING, Windows

log = logging.getLogger("oanda")

KINDS = (ACTIVITY, RESPONSES, TRADING)


class Keeper:
    """Moves the plugin's raw records past their windows, one pass at a time."""

    def __init__(
        self,
        plugin: meridian.Plugin,
        store: RawStore,
        now: Callable[[], datetime],
        prices: edge.RawStore | None = None,
    ):
        self._plugin = plugin
        self._store = store
        self._now = now
        self._prices = prices
        self._lock = asyncio.Lock()
        self._said: dict[str, str] = {}

    async def tend(self, windows: Windows) -> int:
        """One pass; how many units left storage."""
        store = self._store
        if store.storage is None:
            return 0
        moved = 0
        async with self._lock:
            now = self._now()
            ended = {
                RESPONSES: f"{now:%Y-%m-%d}",
                ACTIVITY: f"{now:%Y-%m}",
                TRADING: f"{now:%Y-%m}",
            }
            stored = []
            for kind in KINDS:
                window = windows.of(kind)
                past = of(now - window.length)
                count, first, last = 0, 0, 0
                for unit in store.units(kind):
                    if (
                        unit.path.name < ended[kind]
                        and unit.last_received_ns < past
                        and self._movable(unit)
                        and await self._move(unit, window.past, window.days)
                    ):
                        moved += 1
                        continue
                    count += unit.record_count
                    first = min(first or unit.first_received_ns, unit.first_received_ns)
                    last = max(last, unit.last_received_ns)
                stored.append(
                    meridian.StoredSpan(
                        record_kind=kind,
                        record_count=count,
                        first_received_ns=first,
                        last_received_ns=last,
                    )
                )
            stored.extend(await self._tend_prices(windows, now))
            self._plugin.stored = stored
        return moved

    async def _tend_prices(self, windows: Windows, now: datetime) -> list[Any]:
        """The data role's raw records moved past their window by the SDK's
        own pass, and what they hold: the span it says, or none."""
        if self._prices is None:
            return []
        window = windows.of(PRICES)
        self._plugin.stored = []
        await self._prices.tend(self._plugin, window.days, window.past, now.date())
        return list(getattr(self._plugin, "stored", None) or [])

    def _movable(self, unit: Unit) -> bool:
        if self._plugin.find_record(unit.unit) is None:
            return True
        noted = self._store.noted(unit)
        if noted is not None and set(noted) == set(unit.files):
            return True
        self._once(unit, "kept: the SDK's index holds a move of this unit with other records")
        return False

    async def _move(self, unit: Unit, past: str, days: int) -> bool:
        if past not in (ARCHIVED, DELETED):
            return False
        self._store.note(unit, self._now())
        act = self._plugin.archive_unit if past == ARCHIVED else self._plugin.delete_unit
        try:
            await act(
                unit.kind,
                unit.unit,
                record_count=unit.record_count,
                first_received_ns=unit.first_received_ns,
                last_received_ns=unit.last_received_ns,
            )
        except meridian.CommandRefused as refused:
            within = refused.reason == sidecar_pb2.REFUSAL_REASON_WITHIN_HOLD
            self._once(
                unit,
                "kept inside the deployment's hold"
                if within
                else f"not moved, and kept: {refused}",
            )
            return False
        except (meridian.MeridianError, RuntimeError, OSError, ValueError) as failed:
            self._once(unit, f"not moved, and kept: {failed}")
            return False
        log.info(
            "%s %s (%d records), past its window of %d days",
            past,
            unit.unit,
            unit.record_count,
            days,
        )
        return True

    def _once(self, unit: Unit, said: str) -> None:
        if self._said.get(unit.unit) != said:
            self._said[unit.unit] = said
            log.warning("%s: %s", unit.unit, said)
