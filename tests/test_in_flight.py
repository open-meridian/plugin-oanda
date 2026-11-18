"""The plugin within its share at its sidecar (item 96, 2026-11-18).

Core's sidecar admits at most `sidecar.limits.inFlight` (16) calls of a
plugin's at once and refuses the rest for load, with a retry-after
(crates/sidecar/src/share.rs). Here the sidecar is stood in for at that
level, `Share`, behind the SDK's own connection (`meridian.Plugin`), so the
SDK's own waiting-out of a refusal for load runs as it runs in a pod; what
an admitted call does is the suites' `Recorder`'s, with core's own
deduplication of a statement and an activity sent again (`Core`). The role
classes' `run()` is kept as `meridian.run` keeps it: started again each
time it raises (ruling 1012).

SIMULATED ONLY: the synthetic OANDA; nothing reaches OANDA or a network.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, cast

import grpc  # type: ignore[import-untyped]
import meridian
import meridian.client
import pytest
from meridian import edge
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import custody as custody_role
from meridian.suites import Recorder
from meridian.testing import PageClient
from meridian.v1 import sidecar_pb2

from oanda import synthetic
from oanda.__main__ import Oanda, together
from oanda.connection import Connections
from oanda.custody import Syncer, activities_of
from oanda.page import pages
from oanda.pairs import PairMap
from oanda.raw import RawStore
from oanda.settings import Config

from conftest import Clock
from rigs import ACCOUNT, BOOK_ACCOUNT, INSTANCE, computed, resolved

#: Core's sidecar's own default (crates/sidecar/src/share.rs, the chart's
#: sidecar.limits.inFlight).
IN_FLIGHT = 16

#: The account's read: one call at the sidecar at a time.
READ_HOLDS = 1

#: How long `meridian.run` waits before starting a failed run() again, cut
#: down (its own backoff starts at a second).
RESTART_PAUSE = 0.002


def refused_for_load(held: int) -> grpc.aio.AioRpcError:
    """The sidecar's refusal past its share, as core sends it: status,
    reason and retry-after (contract v30, W4.4)."""
    refusal = sidecar_pb2.Refusal(
        reason=sidecar_pb2.REFUSAL_REASON_RATE_LIMITED, retry_after_seconds=1
    )
    return grpc.aio.AioRpcError(
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.aio.Metadata(),
        grpc.aio.Metadata((meridian.client.REFUSAL_METADATA, refusal.SerializeToString())),
        f"rate limited: this plugin has {held} requests in flight, its sidecar.limits.inFlight",
    )


class Share:
    """The plugin's typed operations at its sidecar: at most `most` at once,
    past which a call is refused for load and reaches nothing; `refusing`
    refuses a row's nth calls (1 first) as if past it. A call on a row in
    `held` waits at the sidecar until its event is set, as a call does while
    core is slow to answer. Safe across threads: a tool asked through
    `PageClient` runs on a loop of its own."""

    def __init__(self, recorder: Recorder, most: int = IN_FLIGHT) -> None:
        self.recorder = recorder
        self.most = most
        self.lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.calls: Counter[str] = Counter()
        self.refused: Counter[str] = Counter()
        self.refusing: dict[str, set[int]] = {}
        self.held: dict[str, asyncio.Event] = {}

    def __getattr__(self, row: str) -> Callable[..., Awaitable[Any]]:
        if row.startswith("_"):
            raise AttributeError(row)

        async def call(params: Any, timeout: float | None = None) -> Any:
            return await self._call(row, params)

        return call

    async def _call(self, row: str, params: Any) -> Any:
        with self.lock:
            self.calls[row] += 1
            if self.now >= self.most or self.calls[row] in self.refusing.get(row, ()):
                self.refused[row] += 1
                raise refused_for_load(self.now)
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            gate = self.held.get(row)
            if gate is not None:
                await gate.wait()
            # Core's answer, a moment later.
            await asyncio.sleep(0.001)
            return await self.recorder._operate(row, params)
        finally:
            with self.lock:
                self.now -= 1

    def open(self) -> None:
        """Every call admitted and none held: the test is over."""
        self.most = 1 << 30
        self.refusing.clear()
        for gate in self.held.values():
            gate.set()


class Core:
    """What core records, as it answers a statement and an activity sent
    again (meridian-schema's data dictionary, `already_recorded`: "a
    redelivery ... nothing is recorded twice"): each statement by its
    external ID, its rows, and each activity by its custodian's ID."""

    def __init__(self, recorder: Recorder) -> None:
        self.statements: dict[str, str] = {}
        self.expected: dict[str, int] = {}
        self.rows: Counter[str] = Counter()
        self.activities: Counter[str] = Counter()
        self.sent_again: list[str] = []
        recorder.answer("ResolveIdentifier", resolved)
        recorder.answer("ComputeDate", lambda _: computed())
        recorder.answer("RecordHoldingsStatement", self._statement)
        recorder.answer("RecordHolding", self._row)
        recorder.answer("RecordActivity", self._activity)

    def _statement(
        self, params: ops.RecordHoldingsStatementParams
    ) -> ops.RecordHoldingsStatementResult:
        held = self.statements.get(params.external_statement_id)
        if held is not None:
            return ops.RecordHoldingsStatementResult(statement_id=held, already_recorded=True)
        made = self.statements[params.external_statement_id] = f"STM-{len(self.statements)}"
        self.expected[made] = params.expected_rows
        return ops.RecordHoldingsStatementResult(statement_id=made)

    def _row(self, params: ops.RecordHoldingParams) -> ops.RecordHoldingResult:
        self.rows[params.statement_id] += 1
        return ops.RecordHoldingResult(holding_id=f"HLD-{sum(self.rows.values())}")

    def _activity(self, params: ops.RecordActivityParams) -> ops.RecordActivityResult:
        said = params.activity.external_activity_id
        again = said in self.activities
        if again:
            self.sent_again.append(said)
        else:
            self.activities[said] += 1
        return ops.RecordActivityResult(activity_id=f"ACT-{said}", already_recorded=again)


class Lifeline:
    """The sidecar's own service, never counted in the share (core's
    LIFELINE: the heartbeat), and the account scope's stream: the link, then
    broken -- as a sidecar restarted beside the plugin breaks it -- after
    `after` seconds, on each opening (`breaks` None) or on the first
    `breaks`; open after that."""

    def __init__(self, breaks: int | None, after: float = 0.0) -> None:
        self.breaks = breaks
        self.after = after
        self.opened = 0

    async def Heartbeat(self, request: Any) -> None:  # noqa: N802
        return None

    def WatchAccountScope(self, request: Any) -> AsyncIterator[Any]:  # noqa: N802
        self.opened += 1
        return self._scope(self.breaks is None or self.opened <= self.breaks)

    async def _scope(self, breaking: bool) -> AsyncIterator[Any]:
        yield sidecar_pb2.AccountScopeDelivery(
            links=[
                sidecar_pb2.LinkedExternalAccount(
                    external_account_id=ACCOUNT, account_id=BOOK_ACCOUNT, account_name="OANDA"
                )
            ]
        )
        if not breaking:
            await asyncio.Event().wait()
        await asyncio.sleep(self.after)
        raise grpc.aio.AioRpcError(
            grpc.StatusCode.UNAVAILABLE, grpc.aio.Metadata(), grpc.aio.Metadata(), "restarted"
        )


def connected(share: Share, lifeline: Lifeline) -> meridian.Plugin:
    """The SDK's connection, its sidecar `share` and `lifeline`."""
    return meridian.Plugin(
        identity=meridian.Identity(instance_id=INSTANCE, roles=("custody",)),
        grants=meridian.Grants(),
        _channel=cast(Any, None),
        _stub=cast(Any, lifeline),
        _operations_stub=cast(Any, share),
    )


def many_accounts(count: int) -> list[dict[str, Any]]:
    """What a token reaching `count` accounts lists: the synthetic v20
    account first."""
    return [{"id": ACCOUNT, "tags": []}] + [
        {"id": f"101-001-0000000-{n:03}", "tags": []} for n in range(2, count + 1)
    ]


def custody_plugin(
    storage: Path, monkeypatch: pytest.MonkeyPatch, plugin: meridian.Plugin, accounts: int
) -> Oanda:
    """The plugin as `main` makes it, launched with custody alone, reading
    the synthetic OANDA as often as it can, its token reaching `accounts`
    accounts and the v20 one chosen."""
    monkeypatch.setenv(edge.STORAGE_DIR, str(storage))
    shared = Oanda()
    shared.custody._bind(plugin)
    shared.connections.configure(Config(synthetic=True, poll_seconds=0))
    listed = many_accounts(accounts)
    venue = shared.connections.synthetic_venue
    assert venue is not None

    async def reached() -> list[dict[str, Any]]:
        return [dict(each) for each in listed]

    monkeypatch.setattr(venue, "accounts", reached)
    shared.connections.take(listed)
    assert shared.connections.choose(ACCOUNT, "Rig Admin") == ""
    return shared


async def kept(work: Callable[[], Awaitable[None]], restarts: list[int]) -> None:
    """`work` as `meridian.run` keeps a role's run(): started again after
    each time it raises."""
    while True:
        try:
            await work()
            return
        except Exception:
            restarts.append(1)
            await asyncio.sleep(RESTART_PAUSE)


async def running(shared: Oanda, restarts: list[int]) -> asyncio.Task[None]:
    """The custody class started, and its run() kept."""
    await shared.started(shared.custody)
    return asyncio.create_task(kept(shared.custody.run, restarts))


async def stopped(*tasks: asyncio.Task[None]) -> None:
    """The tasks named stopped, as `meridian.run` stops a class's run(), and
    then any task left beside them."""
    for task in tasks:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    left = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in left:
        task.cancel()
    await asyncio.gather(*left, return_exceptions=True)


async def until(done: Callable[[], bool], within: float = 20.0) -> None:
    """Wait until `done`, a slow runner's time at most."""
    ends = asyncio.get_running_loop().time() + within
    while not done() and asyncio.get_running_loop().time() < ends:
        await asyncio.sleep(0.01)


@pytest.fixture()
def recorder() -> Iterator[Recorder]:
    yield Recorder(INSTANCE)


async def test_the_read_holds_one_call_at_a_time_however_often_it_is_started_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    """The account scope's stream broken again and again, a token reaching
    many accounts, and the sidecar refusing past 16: the account's read
    keeps one call at the sidecar at a time, never another read beside it
    for each time its class was started again."""
    Core(recorder)
    share = Share(recorder)
    shared = custody_plugin(
        tmp_path, monkeypatch, connected(share, Lifeline(breaks=None, after=0.02)), 60
    )
    restarts: list[int] = []
    task = await running(shared, restarts)
    try:
        await until(lambda: len(restarts) >= 20 and share.calls["ReportSyncStatus"] >= 10)
    finally:
        share.open()
        await stopped(task)
    assert len(restarts) >= 10, "the stream broke and the class was started again"
    assert share.calls["ReportSyncStatus"] >= 5, "the account was read"
    assert share.peak <= READ_HOLDS, f"{share.peak} calls at the sidecar at once"
    assert not share.refused, f"refused for load: {dict(share.refused)}"


async def test_a_tool_answers_while_the_read_waits_at_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    """Core slow to answer the read, and the account scope's stream broken
    again and again: a tool asking the sidecar something is still admitted
    and answers at once, never refused behind the read."""
    Core(recorder)
    share = Share(recorder)
    share.held["ReportExternalAccounts"] = asyncio.Event()
    plugin = connected(share, Lifeline(breaks=None, after=0.01))
    shared = custody_plugin(tmp_path, monkeypatch, plugin, 60)
    restarts: list[int] = []
    task = await running(shared, restarts)
    client = PageClient(
        pages,
        plugin,
        roles=["custody"],
        deployment_admin=True,
    )
    try:
        await until(lambda: len(restarts) >= 2 * IN_FLIGHT)
        answered = await asyncio.wait_for(
            asyncio.to_thread(client.call_tool, "read_account_links", level="admin"), 0.9
        )
    finally:
        share.open()
        await stopped(task)
    assert answered.data["external_accounts"], answered.data
    assert not share.refused["ReadAccountsForLinking"]


async def test_nothing_is_recorded_twice_when_the_class_is_started_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    """The account scope's stream broken once as the plugin starts, and the
    account read over and over: each activity is sent until core has it and
    never again, and the statement's rows once each."""
    core = Core(recorder)
    share = Share(recorder)
    shared = custody_plugin(tmp_path, monkeypatch, connected(share, Lifeline(breaks=1)), 3)
    restarts: list[int] = []
    task = await running(shared, restarts)
    try:
        await until(lambda: share.calls["ReportSyncStatus"] >= 20)
    finally:
        share.open()
        await stopped(task)
    assert restarts, "the stream broke and the class was started again"
    assert core.activities and core.statements, "the account was read"
    assert not core.sent_again, f"sent again after core had them: {sorted(core.sent_again)}"
    assert all(core.rows[made] == core.expected[made] for made in core.rows), core.rows


async def test_nothing_is_lost_when_the_sidecar_refuses_the_read_for_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    """A statement's second row and the second activity refused for load
    past the SDK's waiting (cut to nothing here): the read stops there, and
    the next carries on from it, so every row and every activity lands, and
    once."""
    monkeypatch.setattr(meridian.client, "LOAD_WAIT_MOST_SECONDS", 0.0)
    core = Core(recorder)
    share = Share(recorder)
    share.refusing = {"RecordHolding": {2}, "RecordActivity": {2}}
    role = custody_role.Role()
    role._bind(connected(share, Lifeline(breaks=None)))
    clock = Clock()
    connections = Connections(tmp_path, clock)
    connections.configure(Config(synthetic=True))
    connections.take(list(synthetic.ACCOUNTS))
    assert connections.choose(ACCOUNT, "Rig Admin") == ""
    syncer = Syncer(
        role,
        connections,
        now=clock,
        raw=RawStore(tmp_path, tmp_path),
        pairs=PairMap(None),
        linked=lambda _: True,
    )
    venue = connections.synthetic_venue
    assert venue is not None
    transactions, _ = await venue.transactions_since(ACCOUNT, "0")
    every = {a.external_activity_id for t in transactions for a in activities_of(t, "USD")}
    for _ in range(3):
        # A read stopped is said by the poll, which reads again on its next turn.
        with contextlib.suppress(meridian.MeridianError):
            await syncer.run_once()
    assert sum(share.refused.values()) == 2
    assert set(core.activities) == every and set(core.activities.values()) == {1}
    assert not core.sent_again, sorted(core.sent_again)
    (statement,) = core.rows
    assert core.rows[statement] == core.expected[statement] == 3


async def test_together_leaves_nothing_running_behind_a_failure_or_a_stop() -> None:
    """A class's loops, `together`: one failing cancels the rest and raises
    its failure; the class stopped stops each, never left running."""
    stopped_too: list[str] = []

    async def forever(name: str) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            stopped_too.append(name)

    async def fails() -> None:
        await asyncio.sleep(0)
        raise ConnectionError("the account scope's stream ended")

    with pytest.raises(ConnectionError):
        await together(forever("poll"), fails())
    assert stopped_too == ["poll"]
    task = asyncio.create_task(together(forever("a"), forever("b")))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(stopped_too) == ["a", "b", "poll"]
