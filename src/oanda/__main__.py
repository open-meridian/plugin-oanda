"""The OANDA plugin: spot FX through OANDA's v20 API for an Open Meridian
deployment (meridian-design tasks/sdk-contract/an-fx-ccm-plugin-trades-spot,
ruled 2026-10-13).

It runs one class per role it holds (open-meridian 0.35, rulings 1004,
1005, 1009 to 1023) -- `Custody` here, `Trading` (trading.py's `Trader`)
and `Data` (lake.py's `Lake`) -- with `meridian.run` on the sidecar it is
launched beside, declaring its settings and its pages, which `meridian.run`
serves; the settings are applied before anything is heard, and the classes
the instance was not launched with are not started. What the three share --
the connection, the pair map, the raw records, the links and the latency
recorder -- is `Oanda`'s, and the first class to start keeps the
plugin-wide loops: the account scope followed, the accounts listed and
storage tended. On every poll and every settings change it lists
the accounts the token reaches, for an admin to choose from on Connections
(connection.py). As `custody` it reads the account chosen, or the one the
`account_id` setting names, on every poll and every settings change
(custody.py): its sync status,
its statement as two cash legs per open position, and its activity. As
`connectivity` it places the placements naming it at OANDA and reports what
OANDA did with them (trading.py), following the account's transaction
stream. As `data` (from 0.5.0) it records OANDA's prices in the lake
(lake.py): what the lake wants, and each standing want kept current from
the pricing stream. What OANDA answered is kept as raw records in the
storage the deployment grants (raw.py, and the SDK's store for the data
role's), and moved past each kind's window (archive.py). From 0.6.0 its
sessions' latency -- the pricing stream's and the order calls' -- is
recorded on one `meridian.roles.Latency` and shown on Connection's
Sessions; core receives ReportLatency from contract v28 (ruling 515), so
nothing of it is sent yet.

The lake's wants and the book's placements and instructions are heard on
the plugin's one stream, opened at start; placements and instructions heard
before trading starts are left to its start's own reads of the book.

Everything goes through the sidecar. The access token arrives as the
plugin's secret setting, set by an admin of the plugin in the dashboard, and
is never logged, shown, kept or sent anywhere but OANDA. Sample data trades
with and reads the synthetic OANDA (synthetic.py), never OANDA.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import AsyncIterator, Coroutine
from datetime import UTC, datetime
from typing import Any

import meridian
from meridian import edge, roles
from meridian.roles import custody

from .archive import Keeper
from .connection import Connections
from .custody import Syncer
from .declaration import DECLARATION, PORT
from .kept import Kept, file_in
from .lake import Lake
from .linking import Links, failure
from .page import Held, hold, pages
from .pairs import PairMap, pairs_file
from .raw import RawStore, storage_root
from .settings import PRICES, config_from
from .trading import POLL_SECONDS, SYNTHETIC_POLL_SECONDS, Trader

log = logging.getLogger("oanda")


def utc_now() -> datetime:
    return datetime.now(UTC)


def bound(role: Any) -> bool:
    """Whether the instance holds `role`'s role: `meridian.run` registers a
    class only for a role the instance was launched with."""
    try:
        role.plugin  # noqa: B018 - registered, or raised
    except meridian.NotRegistered:
        return False
    return True


class Custody(custody.Role):
    """The custody role's class: the account read on every poll and every
    settings change (`Syncer`), and its external accounts linked (`Links`),
    each making its acts through it."""

    def __init__(self, shared: Oanda) -> None:
        self.shared = shared

    async def on_start(self) -> None:
        await self.shared.started(self)

    async def run(self) -> None:
        await asyncio.gather(*self.shared.hosted(self))


class Trading(Trader):
    """The connectivity role's class: OANDA's `Trader`, with what it shares."""

    shared: Oanda

    async def on_start(self) -> None:
        await self.shared.started(self)

    async def run(self) -> None:
        await asyncio.gather(super().run(), *self.shared.hosted(self))


@dataclasses.dataclass(eq=False)
class Data(Lake):
    """The data role's class: OANDA's `Lake`, with what it shares."""

    shared: Any = None

    async def on_start(self) -> None:
        await self.shared.started(self)

    async def run(self) -> None:
        await asyncio.gather(super().run(), *self.shared.hosted(self))


class Oanda:
    """What the plugin's three role classes share, and the loops that are
    the whole plugin's, kept by the first class to start."""

    def __init__(self) -> None:
        # Each session's latency (contract v27), reported from contract v28
        # through a class holding `report_latency` once one starts.
        self.latency = roles.Latency()
        storage = edge.storage_dir()
        self.raw = RawStore(storage_root(storage), storage)
        self.connections = Connections(storage, utc_now)
        self.pairs = PairMap(pairs_file(storage))
        self.wake = asyncio.Event()
        self.custody = Custody(self)
        self.links = Links(self.custody)
        self.syncer = Syncer(
            self.custody,
            self.connections,
            now=utc_now,
            raw=self.raw,
            pairs=self.pairs,
            linked=lambda external_account_id: (
                self.links.scope.link_of(external_account_id) is not None
            ),
        )
        connections = self.connections
        self.trading = Trading(
            lambda: connections.current,
            now=utc_now,
            pairs=self.pairs,
            raw=self.raw,
            secrets=lambda: connections.config.secrets(),
            sent=Kept(file_in(storage, "sent.json"), []),
            cursor=Kept(file_in(storage, "cursor.json"), {}),
            trades=Kept(file_in(storage, "trades.json"), {}),
            waiting=Kept(file_in(storage, "waiting.json"), []),
            rolling=Kept(file_in(storage, "rolling.json"), {}),
            scope=lambda: self.links.scope,
            latency=self.latency,
            poll_seconds=lambda: (
                SYNTHETIC_POLL_SECONDS if connections.config.synthetic else POLL_SECONDS
            ),
        )
        self.trading.shared = self
        # Placements and instructions are heard on the one stream, opened
        # before there is a connection: those heard before it starts are left
        # to its start's own reads.
        self.trading.waits_to_start = True
        self.data = Data(
            self.connections,
            self.pairs,
            edge.RawStore(storage_root(storage), kind=PRICES),
            latency=self.latency,
            shared=self,
        )
        self.held = Held(
            self.syncer, self.connections, self.links, self.wake, latency=self.latency
        )
        hold(self.held)
        self._keeper: Keeper | None = None
        self._host: Any = None
        self._scopes: AsyncIterator[meridian.AccountScope] | None = None

    @property
    def roles(self) -> list[Any]:
        return [self.custody, self.trading, self.data]

    def keeper(self) -> Keeper:
        """The raw records' keeper, made on the first settings, once the
        classes the instance holds are registered."""
        if self._keeper is None:
            plugin = next(role.plugin for role in self.roles if bound(role))
            prices = self.data.raw if bound(self.data) else None
            self._keeper = Keeper(plugin, self.raw, utc_now, prices)
        return self._keeper

    async def on_settings(self, delivered: Any) -> None:
        """Each delivery of the settings: the connection configured, the
        accounts and the account read again at once, and storage tended."""
        self.connections.configure(config_from(delivered.values, delivered.refused))
        self.wake.set()
        await self.keeper().tend(self.connections.config.windows)

    async def started(self, role: Any) -> None:
        """A class started: the pages told it is held, the latency recorder
        reported through it where it is the first holding `report_latency`,
        and the plugin-wide loops kept by it where it is the first to start,
        the account scope's first delivery read now."""
        if role is self.trading:
            self.held = dataclasses.replace(self.held, trader=self.trading)
        if role is self.data:
            self.held = dataclasses.replace(self.held, lake=self.data)
            # The data role's figures beside custody's on the Summary.
            self.syncer.more_figures = self.data.figures
        hold(self.held)
        if self.latency.role is None and "report_latency" in role.ACTS:
            self.latency.role = role
        if self._host is None:
            self._host = role
            self._scopes = role.plugin.account_scope()
            await self.links.hold(await anext(self._scopes))

    def hosted(self, role: Any) -> list[Coroutine[Any, Any, None]]:
        """The plugin-wide loops `role` keeps beside its own work."""
        kept: list[Coroutine[Any, Any, None]] = []
        if role is self._host:
            kept += [self._watch_links(), self._poll()]
        if role is self.latency.role:
            kept.append(self.latency.run())
        return kept

    async def _watch_links(self) -> None:
        assert self._scopes is not None
        async for scope in self._scopes:
            await self.links.hold(scope)
        raise ConnectionError("the account scope's stream ended")

    async def _poll(self) -> None:
        """The accounts the token reaches, for Connections to pick from, and
        with custody the account read, on every poll and every settings
        change; storage tended after each."""
        syncer = self.syncer
        while True:
            self.wake.clear()
            try:
                await syncer.connections.discover()
                if bound(self.custody):
                    await syncer.run_once()
                await self.keeper().tend(syncer.connections.config.windows)
            except meridian.MeridianError as failed:
                log.warning("this read stopped: %s", failure(failed))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self.wake.wait(), timeout=syncer.connections.config.poll_seconds
                )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    shared = Oanda()
    # The version's whole declaration (declaration.py): its settings, its
    # pages with the levels each serves and their tools, and its
    # documentation. A version registers exactly what it was uploaded with
    # (contract v25).
    meridian.run(
        declaration=DECLARATION,
        roles=shared.roles,
        pages=pages,
        port=PORT,
        on_settings=shared.on_settings,
    )


if __name__ == "__main__":
    main()
