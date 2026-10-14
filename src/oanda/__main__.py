"""The OANDA plugin: spot FX through OANDA's v20 API for an Open Meridian
deployment (meridian-design tasks/sdk-contract/an-fx-ccm-plugin-trades-spot,
ruled 2026-10-13).

It connects to the sidecar it is launched beside, declares its settings and
its pages, and serves them. As `custody` it reads the account the settings
name on every poll and every settings change (custody.py): its sync status,
its statement as two cash legs per open position, and its activity. As
`connectivity` it places the placements naming it at OANDA and reports what
OANDA did with them (trading.py), following the account's transaction
stream. What OANDA answered is kept as raw records in the storage the
deployment grants (raw.py), and moved past each kind's window (archive.py).

Everything goes through the sidecar. The access token arrives as the
plugin's secret setting, set by an admin of the plugin in the dashboard, and
is never logged, shown, kept or sent anywhere but OANDA. Sample data trades
with and reads the synthetic OANDA (synthetic.py), never OANDA.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import meridian
from meridian import edge

from .archive import Keeper
from .connection import Connections
from .custody import Syncer
from .declaration import DECLARATION
from .kept import Kept, file_in
from .linking import Links, failure
from .page import TITLE, Held, pages, serve
from .pairs import PairMap, pairs_file
from .raw import RawStore, storage_root
from .settings import DECLARED, config_from
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
        connections.configure(config_from(delivered.values))
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
            await syncer.run_once()
            await keeper.tend(syncer.connections.config.windows)
        except meridian.MeridianError as failed:
            log.warning("this read stopped: %s", failure(failed))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), timeout=syncer.connections.config.poll_seconds)


async def trade(trader: Trader, connections: Connections) -> None:
    try:
        await trader.run(
            lambda: SYNTHETIC_POLL_SECONDS if connections.config.synthetic else POLL_SECONDS
        )
    except meridian.MeridianError as failed:
        log.warning("trading stopped: %s", failure(failed))


async def run() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, stopped.set)

    port = int(os.environ.get("OANDA_PAGE_PORT", "8000"))
    async with await meridian.connect(
        interface=meridian.Interface(port=port, title=TITLE, pages=pages),
        settings=DECLARED,
        declaration=DECLARATION,
        reads_external_accounts=True,
    ) as plugin:
        log.info(
            "registered as %s, roles %s",
            plugin.identity.instance_id,
            ", ".join(plugin.identity.roles) or "none",
        )
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
        keeper = Keeper(plugin, raw, utc_now)
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
            )
        page = serve(plugin, Held(syncer, connections, links, wake, trader), port)
        log.info("serving its pages on 127.0.0.1:%d", port)
        tasks = [
            following,
            asyncio.create_task(watch_settings(plugin, connections, keeper, configured, wake)),
            asyncio.create_task(poll(syncer, keeper, configured, wake)),
        ]
        if trader is not None:
            tasks.append(asyncio.create_task(trade(trader, connections)))
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
