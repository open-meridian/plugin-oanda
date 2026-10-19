"""OANDA's work run against the suites' recorder: the plugin's own Trader
(trading.py) and Syncer (custody.py) over the synthetic OANDA
(synthetic.py), only the sidecar stood in for by the recorder.

SIMULATED ONLY: nothing here reaches OANDA, practice or live.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import meridian
from meridian import edge
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from oanda import moments, synthetic
from oanda.connection import Connection, Connections
from oanda.custody import Syncer
from oanda.kept import Kept
from oanda.lake import Lake
from oanda.pairs import PairMap
from oanda.raw import RawStore
from oanda.settings import Config
from oanda.synthetic import SyntheticOanda
from oanda.trading import Trader

from conftest import NOW, Clock

Producer = Callable[[Recorder], Awaitable[None]]

ACCOUNT = synthetic.ACCOUNT_ID
BOOK_ACCOUNT = "ACC-01JAE000000000000000000001"
INSTANCE = "oanda"


def cash_id(code: str) -> str:
    """A currency's cash instrument, as the harness's platform names it."""
    return f"INS-01J{code}{'0' * 19}1"


EUR = cash_id("EUR")
USD = cash_id("USD")
JPY = cash_id("JPY")


def decimal(text: str) -> ops.Decimal:
    exact = Decimal(text)
    scale = max(0, -int(exact.as_tuple().exponent))
    units = int(exact.scaleb(scale))
    return ops.Decimal(high=units >> 64, low=units & ((1 << 64) - 1), scale=scale)


def money(text: str, code: str = "USD") -> ops.Money:
    return ops.Money(amount=decimal(text), currency_code=code)


def placement(
    pid: str = "PLC-01JAE00000000000000000001",
    *,
    quantity: str = "10000",
    instance: str = INSTANCE,
    code: str = ACCOUNT,
    tif: ops.TimeInForce = ops.TIME_IN_FORCE_GOOD_TILL_CANCELLED,
    state: ops.PlacementState = ops.PLACEMENT_STATE_QUEUED,
    strategy: ops.StrategyInstruction | None = None,
    venue_order_id: str = "",
    limit: str | None = "1.08000",
    quote: str = "USD",
    filled: bool = False,
) -> ops.Placement:
    held = ops.Placement(
        placement_id=pid,
        order_id="ORD-" + pid[4:],
        account_id=BOOK_ACCOUNT,
        ems_instance_id="desk",
        connectivity_instance_id=instance,
        venue_account_code=code,
        quantity=decimal(quantity),
        time_in_force=tif,
        status=ops.PlacementStatus(state=ops.PLACEMENT_STATE_FILLED if filled else state),
        venue_order_id=venue_order_id,
    )
    if limit is not None:
        held.limit_price.CopyFrom(money(limit, quote))
    if venue_order_id:
        held.venue_order_ids.append(venue_order_id)
    if strategy is not None:
        held.strategy.CopyFrom(strategy)
    return held


def asking(
    held: ops.Placement,
    kind: ops.PlacementChangeKind,
    crq: str = "CRQ-01JAE00000000000000000001",
    **change: Any,
) -> ops.Placement:
    """The placement as the book delivers it with a change requested."""
    asked = ops.Placement()
    asked.CopyFrom(held)
    asked.change_requests.append(
        ops.ChangeRequest(
            change_request_id=crq,
            order_id=held.order_id,
            placement_id=held.placement_id,
            placement_change=ops.PlacementChange(kind=kind, **change),
            outcome=ops.CHANGE_REQUEST_OUTCOME_PENDING,
        )
    )
    return asked


def order_of(held: ops.Placement, **changes: Any) -> ops.Order:
    order = ops.Order(
        order_id=held.order_id,
        account_id=BOOK_ACCOUNT,
        instrument_id=changes.pop("instrument_id", EUR),
        side=changes.pop("side", ops.SIDE_BUY),
        quantity=held.quantity,
        order_type=changes.pop("order_type", ops.ORDER_TYPE_LIMIT),
        time_in_force=changes.pop("time_in_force", held.time_in_force),
        dealt_currency=changes.pop("dealt_currency", ""),
        quote_currency=changes.pop("quote_currency", ""),
        expire_date=changes.pop("expire_date", ""),
    )
    if held.HasField("limit_price"):
        order.limit_price.CopyFrom(held.limit_price)
    if "stop_price" in changes:
        order.stop_price.CopyFrom(changes.pop("stop_price"))
    order.placements.append(held)
    return order


def cash_record(code: str) -> ops.InstrumentRecord:
    return ops.InstrumentRecord(
        instrument_id=cash_id(code),
        asset_class=ops.ASSET_CLASS_CASH,
        currency=code,
        identifiers=[ops.Identifier(scheme="iso4217", value=code)],
    )


def computed(day: str = "2026-10-15") -> ops.ComputeDateResult:
    return ops.ComputeDateResult(
        date=day,
        outcome=ops.DATE_OUTCOME_COMPUTED,
        basis=ops.DateBasis(
            rule=ops.DateRuleRef(kind=ops.DATE_RULE_KIND_SPOT, subject="EUR/USD", version=1),
            calendars=[
                ops.CalendarRef(calendar_id="CAL-01JAE000000000000000EUR1", version=1),
                ops.CalendarRef(calendar_id="CAL-01JAE000000000000000USD1", version=1),
            ],
        ),
    )


def spot_of(params: ops.ComputeDateParams) -> ops.ComputeDateResult:
    """The date read as core answers EUR/USD's spot inputs, stood in for:
    the trade date through the 17:00 New York cut, two weekdays on (this
    rig holds no holiday). The 13th, a Tuesday, before 17:00 spots on the
    15th; after it, on the 16th."""
    day = (
        moments.trading_day(params.spot.at_ns)
        if params.spot.at_ns
        else date.fromisoformat(params.spot.trade_date)
    )
    counted = 0
    while counted < 2:
        day += timedelta(days=1)
        counted += day.weekday() < 5
    return computed(day.isoformat())


def not_held() -> ops.ComputeDateResult:
    return ops.ComputeDateResult(outcome=ops.DATE_OUTCOME_RULE_NOT_HELD)


def resolved(params: ops.ResolveIdentifierParams) -> ops.ResolveIdentifierResult:
    """A currency by its ISO 4217 code, as the deployment pulls it."""
    for held in params.identifiers:
        if held.scheme == "iso4217" and held.value != "XAU":
            return ops.ResolveIdentifierResult(found=True, instrument_id=cash_id(held.value))
    return ops.ResolveIdentifierResult(found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS)


class TradeRig:
    """A Trader over the recorder and the synthetic OANDA, trading enabled
    unless asked; the book's answers as the recorder gives them."""

    def __init__(
        self,
        recorder: Recorder,
        *,
        trading: bool = True,
        dates: Callable[[Any], ops.ComputeDateResult] = spot_of,
        storage: Path | None = None,
    ) -> None:
        self.recorder = recorder
        self.clock = Clock(NOW)
        self.storage = storage or Path(tempfile.mkdtemp())
        self.venue = SyntheticOanda(self.clock, synthetic.state_file(self.storage))
        self.trading = trading
        self.pairs = PairMap(None)
        self.raw = RawStore(self.storage, self.storage)
        self.orders: list[ops.Order] = []
        self.executions: list[ops.Execution] = []
        self.minted = 0
        self.charged = 0
        self.kept: dict[str, Kept] = {}
        recorder.answer("ListOrders", self._listed)
        recorder.answer("ResolveInstrument", self._instrument)
        recorder.answer("ResolveIdentifier", resolved)
        recorder.answer("ComputeDate", dates)
        recorder.answer("ReportExecution", self._executed)
        recorder.answer("ReportCharge", self._charged)
        self.trader = self.make()

    def connection(self) -> Connection:
        return Connection(
            connection_id=f"synthetic:{ACCOUNT}",
            account_id=ACCOUNT,
            environment="synthetic",
            venue=self.venue,
            synthetic=True,
            trading=self.trading,
        )

    def make(self) -> Trader:
        """A trader as a process started now makes one: what it keeps read
        again from storage."""
        empties: dict[str, Any] = {
            "sent": [],
            "cursor": {},
            "trades": {},
            "waiting": [],
            "rolling": {},
        }
        self.kept = {
            name: Kept(self.storage / "state" / f"{name}.json", empty)
            for name, empty in empties.items()
        }
        return Trader(
            self.recorder,
            self.connection,
            now=self.clock,
            pairs=self.pairs,
            raw=self.raw,
            sent=self.kept["sent"],
            cursor=self.kept["cursor"],
            trades=self.kept["trades"],
            waiting=self.kept["waiting"],
            rolling=self.kept["rolling"],
        )

    def _instrument(self, params: ops.ResolveInstrumentParams) -> ops.ResolveInstrumentResult:
        for code in ("EUR", "USD", "JPY", "GBP", "CAD"):
            if params.instrument_id == cash_id(code):
                return ops.ResolveInstrumentResult(found=True, instrument=cash_record(code))
        return ops.ResolveInstrumentResult(
            found=True,
            instrument=ops.InstrumentRecord(
                instrument_id=params.instrument_id, asset_class=ops.ASSET_CLASS_EQUITY
            ),
        )

    def _listed(self, _: Any) -> ops.ListOrdersResult:
        return ops.ListOrdersResult(
            orders=self.orders,
            executions=self.executions,
            as_of=ops.Watermark(
                partitions=[ops.PartitionSequence(partition="book", sequence=7)]
            ),
        )

    def _executed(self, params: ops.ReportExecutionParams) -> ops.ReportExecutionResult:
        """The book's answer: the execution recorded, matched on a placement
        and unmatched on none (contract v23)."""
        self.minted += 1
        held = ops.Execution(
            execution_id=f"EXE-{self.minted}",
            placement_id=params.placement_id,
            venue_execution_id=params.venue_execution_id,
            venue_order_id=params.venue_order_id,
            quantity=params.quantity,
            state=(
                ops.EXECUTION_STATE_MATCHED
                if params.placement_id
                else ops.EXECUTION_STATE_UNMATCHED
            ),
        )
        self.executions.append(held)
        return ops.ReportExecutionResult(execution=held)

    def _charged(self, params: ops.ReportChargeParams) -> ops.ReportChargeResult:
        """The book's answer: a charge on a placement minted its `CHG-` ID;
        one on none kept unplaced (W11.12)."""
        if not params.placement_id:
            return ops.ReportChargeResult(
                unplaced=ops.UnplacedFact(
                    unplaced_id="UNP-1",
                    reason=ops.UNPLACED_REASON_UNKNOWN_PLACEMENT,
                    fact_kind="charge",
                )
            )
        self.charged += 1
        return ops.ReportChargeResult(
            charge=ops.Charge(charge_id=f"CHG-{self.charged}", placement_id=params.placement_id)
        )

    def holds(self, *placements: ops.Placement, **changes: Any) -> None:
        self.orders = [order_of(each, **changes) for each in placements]

    async def hear(self, *placements: ops.Placement) -> None:
        self.recorder.answer(
            "PlacementUpdated",
            lambda _: [ops.PlacementUpdatedEvent(placement=each) for each in placements],
        )
        await self.recorder.receive(placement_updated=self.trader.on_placement)

    async def instruct(self, value: ops.TradingInstructionValue) -> None:
        instruction = ops.TradingInstruction(
            target=ops.TradingInstructionTarget(instance_id=INSTANCE),
            value=value,
            entry=ops.EntryMeta(entry_id="ENT-stop-1"),
        )
        self.recorder.answer(
            "TradingInstructionChanged",
            lambda _: ops.TradingInstructionChangedEvent(instruction=instruction),
        )
        await self.recorder.receive(trading_instruction_changed=self.trader.on_instruction)

    async def started(self) -> None:
        """The trader started, the cursor at the account's last transaction."""
        await self.trader.start()
        await self.trader.poll()


def sent_ids(recorder: Recorder, row: str) -> list[Any]:
    return recorder.on(row)


class CustodyRig:
    """A Syncer over the recorder and the synthetic OANDA, the account
    linked unless asked."""

    def __init__(
        self,
        recorder: Recorder,
        *,
        linked: bool = True,
        dates: Callable[[Any], ops.ComputeDateResult] = lambda _: computed(),
        resolve: Callable[[Any], ops.ResolveIdentifierResult] = resolved,
    ) -> None:
        self.recorder = recorder
        self.clock = Clock(NOW)
        self.storage = Path(tempfile.mkdtemp())
        self.connections = Connections(self.storage, self.clock)
        self.connections.configure(Config(synthetic=True))
        # The synthetic OANDA's accounts listed, and its v20 account chosen,
        # as an admin chooses it on Connections.
        self.connections.take(list(synthetic.ACCOUNTS))
        assert self.connections.choose(ACCOUNT, "Rig Admin") == ""
        assert self.connections.current is not None, "the synthetic OANDA, made"
        self.raw = RawStore(self.storage, self.storage)
        recorder.answer("ResolveIdentifier", resolve)
        recorder.answer("ComputeDate", dates)
        self.syncer = Syncer(
            recorder,
            self.connections,
            now=self.clock,
            raw=self.raw,
            pairs=PairMap(None),
            linked=lambda _: linked,
        )

    @property
    def venue(self) -> SyntheticOanda:
        held = self.connections.synthetic_venue
        assert held is not None
        return held


def instrument_of(params: ops.ResolveInstrumentParams) -> ops.ResolveInstrumentResult:
    """A cash instrument's record, by the ID `cash_id` names it by, as the
    deployment reads it (W3.6)."""
    held = params.instrument_id
    if held.startswith("INS-01J") and held.endswith("1") and len(held) == 30:
        return ops.ResolveInstrumentResult(found=True, instrument=cash_record(held[7:10]))
    return ops.ResolveInstrumentResult(found=False)


class LakeRig:
    """The data role's Lake over the recorder and the synthetic OANDA, its
    v20 account chosen; currencies resolved and named as the deployment's
    records answer them."""

    def __init__(
        self,
        recorder: Recorder,
        *,
        storage: Path | None = None,
        at: datetime = NOW,
        resolve: Callable[[Any], ops.ResolveIdentifierResult] = resolved,
    ) -> None:
        self.recorder = recorder
        self.clock = Clock(at)
        self.storage = storage or Path(tempfile.mkdtemp())
        self.connections = Connections(self.storage, self.clock)
        self.connections.configure(Config(synthetic=True))
        self.connections.take(list(synthetic.ACCOUNTS))
        assert self.connections.choose(ACCOUNT, "Rig Admin") == ""
        recorder.answer("ResolveIdentifier", resolve)
        recorder.answer("ResolveInstrument", instrument_of)
        self.lake = Lake(
            recorder,
            self.connections,
            PairMap(None),
            edge.RawStore(self.storage, kind="pricing"),
            clock_ns=lambda: moments.of(self.clock()),
        )


def want(
    want_id: str,
    key: str,
    subjects: list[str],
    *,
    day: str = "",
    data_type: str = "",
    kinds: tuple[int, ...] = (),
    standing: bool = False,
    valid_from_ns: int = 0,
    instance: str = INSTANCE,
) -> ops.ObservationsWantedEvent:
    """A want as the lake delivers it."""
    return ops.ObservationsWantedEvent(
        want_id=want_id,
        dataset=f"{instance}:{key}",
        data_type=data_type,
        subjects=[ops.SubjectRef(entity_id=s) for s in subjects],
        kinds=[ops.PriceKind.Name(k) for k in kinds],
        business_date=day,
        standing=standing,
        valid_from_ns=valid_from_ns,
    )


def report_of(recorder: Recorder) -> list[Any]:
    return recorder.on("ReportPlacement")


__all__ = ["meridian"]
