"""The OANDA plugin: spot FX through OANDA's v20 API for an Open Meridian
deployment (meridian-design tasks/sdk-contract/an-fx-ccm-plugin-trades-spot,
ruled 2026-10-13).

It connects to the sidecar it is launched beside, declares its settings and
its pages, and serves them. On every poll and every settings change it lists
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

With `data` and `connectivity` both, the lake's wants and the book's
placements and instructions are heard on one stream, opened at start;
placements and instructions heard before trading starts are left to its
start's own reads of the book.

Everything goes through the sidecar. The access token arrives as the
plugin's secret setting, set by an admin of the plugin in the dashboard, and
is never logged, shown, kept or sent anywhere but OANDA. Sample data trades
with and reads the synthetic OANDA (synthetic.py), never OANDA.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import meridian
from meridian import edge, roles

from .archive import Keeper
from .connection import Connections
from .custody import Syncer
from .declaration import DECLARATION, PORT
from .kept import Kept, file_in
from .lake import Lake
from .linking import Links, failure
from .page import Held, serve
from .pairs import PairMap, pairs_file
from .raw import RawStore, storage_root
from .settings import PRICES, config_from
from .trading import POLL_SECONDS, SYNTHETIC_POLL_SECONDS, Trader

log = logging.getLogger("oanda")


def utc_now() -> datetime:
    return datetime.now(UTC)


async def watch_settings(
    plugin: meridian.Plugin,
    connections: Connections,
    keeper: Keeper,
    configured: asyncio.Event,
    wake: asyncio.Event,
) -> None:
    async for delivered in plugin.settings():
        connections.configure(config_from(delivered.values, delivered.refused))
        configured.set()
        wake.set()
        await keeper.tend(connections.config.windows)


async def watch_links(scopes: AsyncIterator[meridian.AccountScope], links: Links) -> None:
    async for scope in scopes:
        await links.hold(scope)


async def poll(
    syncer: Syncer, keeper: Keeper, configured: asyncio.Event, wake: asyncio.Event
) -> None:
    await configured.wait()
    while True:
        wake.clear()
        try:
            # The accounts the token reaches, for Connections to pick from.
            await syncer.connections.discover()
            await syncer.run_once()
            await keeper.tend(syncer.connections.config.windows)
        except meridian.MeridianError as failed:
            log.warning("this read stopped: %s", failure(failed))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), timeout=syncer.connections.config.poll_seconds)


async def trade(trader: Trader, connections: Connections, hears: bool) -> None:
    try:
        await trader.run(
            lambda: SYNTHETIC_POLL_SECONDS if connections.config.synthetic else POLL_SECONDS,
            hears=hears,
        )
    except meridian.MeridianError as failed:
        log.warning("trading stopped: %s", failure(failed))


async def again(work: Callable[[], Awaitable[None]], what: str) -> None:
    """Run `work` until cancelled, started again a few seconds after it
    stops, each stop said."""
    while True:
        try:
            await work()
        except asyncio.CancelledError:
            raise
        except Exception as failed:  # noqa: BLE001 - said, and started again
            log.warning("%s stopped: %s", what, failed)
        await asyncio.sleep(5)


async def hear(plugin: meridian.Plugin, lake: Lake, trader: Trader | None) -> None:
    """The lake's wants, and with connectivity the book's placements and
    instructions, on one stream."""
    hearing: dict[str, Any] = {
        "observations_wanted": lake.on_want,
        "want_withdrawn": lake.on_withdrawn,
    }
    if trader is not None:
        hearing["placement_updated"] = trader.on_placement
        hearing["trading_instruction_changed"] = trader.on_instruction
    await plugin.receive(**hearing, seed=False)


async def run() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, stopped.set)

    port = PORT
    # The version's whole declaration (declaration.py): its settings, its
    # pages with the levels each serves and their tools, and its documentation.
    # A version registers exactly what it was uploaded with (contract v25).
    async with await meridian.connect(declaration=DECLARATION) as plugin:
        log.info(
            "registered as %s, roles %s",
            plugin.identity.instance_id,
            ", ".join(plugin.identity.roles) or "none",
        )
        # Each session's latency (contract v27): recorded, not yet reported.
        latency = roles.Latency(plugin)
        storage = edge.storage_dir()
        raw = RawStore(storage_root(storage), storage)
        connections = Connections(storage, utc_now)
        pairs = PairMap(pairs_file(storage))
        links = Links(plugin)
        scopes = plugin.account_scope()
        await links.hold(await anext(scopes))
        following = asyncio.create_task(watch_links(scopes, links))
        syncer = Syncer(
            plugin,
            connections,
            now=utc_now,
            raw=raw,
            pairs=pairs,
            linked=lambda external_account_id: (
                links.scope.link_of(external_account_id) is not None
            ),
        )
        lake: Lake | None = None
        if "data" in plugin.identity.roles:
            lake = Lake(
                plugin,
                connections,
                pairs,
                edge.RawStore(storage_root(storage), kind=PRICES),
                latency=latency,
            )
        keeper = Keeper(plugin, raw, utc_now, lake.raw if lake is not None else None)
        configured, wake = asyncio.Event(), asyncio.Event()
        trader: Trader | None = None
        if "connectivity" in plugin.identity.roles:
            trader = Trader(
                plugin,
                lambda: connections.current,
                now=utc_now,
                pairs=pairs,
                raw=raw,
                secrets=lambda: connections.config.secrets(),
                sent=Kept(file_in(storage, "sent.json"), []),
                cursor=Kept(file_in(storage, "cursor.json"), {}),
                trades=Kept(file_in(storage, "trades.json"), {}),
                waiting=Kept(file_in(storage, "waiting.json"), []),
                rolling=Kept(file_in(storage, "rolling.json"), {}),
                scope=lambda: links.scope,
                latency=latency,
            )
        if lake is not None:
            # The data role's figures beside custody's on the Summary.
            syncer.more_figures = lake.figures
        page = serve(
            plugin, Held(syncer, connections, links, wake, trader, lake, latency), port
        )
        log.info("serving its pages on 127.0.0.1:%d", port)
        tasks = [
            following,
            asyncio.create_task(watch_settings(plugin, connections, keeper, configured, wake)),
            asyncio.create_task(poll(syncer, keeper, configured, wake)),
            asyncio.create_task(again(latency.run, "the latency recorder")),
        ]
        if trader is not None:
            if lake is not None:
                trader.waits_to_start = True
            tasks.append(asyncio.create_task(trade(trader, connections, hears=lake is None)))
        if lake is not None:
            data_lake = lake
            tasks.append(
                asyncio.create_task(
                    again(lambda: hear(plugin, data_lake, trader), "hearing the lake")
                )
            )
            tasks.append(asyncio.create_task(again(data_lake.keep_current, "keeping current")))
            tasks.append(asyncio.create_task(again(data_lake.keep_streaming, "the stream")))
        await stopped.wait()
        log.info("stopping")
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        page.shutdown()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
