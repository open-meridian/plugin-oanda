"""OANDA as a connectivity plugin (contract v23; W11.8 to W11.13, W11.20,
W11.23 to W11.25; meridian-design tasks/sdk-contract/an-fx-ccm-plugin-trades-spot),
built as SnapTrade's is.

**What it does.** It hears each placement naming its own instance, and no
other's. For each queued one it reads the placement's order from the book
(ListOrders) and the order's instrument (ResolveInstrument): a spot FX order
is an order in the base currency's cash instrument, its limit or stop price
in the quote currency (plans/an-order-reaches-a-venue-through-the-book,
"Spot FX needs nothing more"), and from contract v23 the order names the
quote currency's cash instrument (`Order.quote_currency`, W11.1), so a market
order, which states no price, names its pair too (W11.8). The pair is found
in the pair map (pairs.py) as OANDA quotes it, and the order sent to OANDA's
`POST /v3/accounts/{accountID}/orders` in the account the placement's venue
account code names -- OANDA's account ID, the external account custody
reports -- with the placement's ID as the order's client extension ID
(`clientExtensions.id`). Units are OANDA's signed units of the base
currency: a buy positive, a sell negative.

**Facts from the transaction stream.** OANDA states what became of an order
as transactions: the order created, filled (ORDER_FILL), cancelled
(ORDER_CANCEL), and each day's financing at the roll (DAILY_FINANCING). The
plugin follows the account's transaction stream, and on every poll reads the
transactions since the last it took (`sinceid`), so one the stream missed is
taken late, never lost; the last taken is kept in storage, so a restart
takes up where it left off.

- **Acknowledged** from OANDA's answer to the send: its create transaction,
  the order's ID as the venue order ID.
- **Rejected**: what OANDA does not offer for this account, or this plugin
  does not send (below), is rejected `unsupported` with nothing sent; what
  OANDA refused (its orderRejectTransaction) is `venue rejected`, its
  `rejectReason` as reported.
- **Fills** (ReportExecution, kind venue reported): each ORDER_FILL of one of
  its orders, quantity its units, price its `fullVWAP` in the quote
  currency, trade time OANDA's to the nanosecond, trade date OANDA's trading
  day (moments.py), its execution ID derived from the account and the fill's
  transaction ID, the same however often it is read (requirement 33), and
  its commission a fee of kind commission (`FEE_KIND_COMMISSION`, W11.11).
  **The value date** comes from the date read: `ComputeDate` with the pair's
  two cash instruments and the fill's moment (W3.20's spot inputs: the
  spot-date rule of the pair, its trade date taken through the pair's 17:00
  New York cut), sent as `computed_settlement_date` with its basis. OANDA's
  fill states no value date, so there is none beside it. Where the date read
  answers none (a rule or a calendar not held, a date outside coverage), the
  fill waits, kept in storage, and is asked again each poll: never sent with
  a guessed date.
- **Busts**: OANDA's v20 transactions have none, so none is reported.
- **Charges** (ReportCharge): each open trade's financing at a roll
  (DAILY_FINANCING's `openTradeFinancings`), and a trade's financing settled
  when it closes, of kind financing (`FEE_KIND_FINANCING`, W11.12), on the
  placement whose fill opened the trade, each its own record (ruled
  2026-10-13; decisions/031), its amount OANDA's in the account's currency,
  signed as a fee (paid positive, received negative).
- **Unknown outcomes**: a send OANDA never answered is outcome unknown,
  `request unanswered`, and is resolved by asking OANDA for the order by its
  client ID (`GET .../orders/@<placement>`), never by sending it again (the
  order-path plan's Q17). Before every send the placement is noted in
  storage (`Sent`), so a placement the book still holds queued after a
  restart is asked about, never sent twice. An order state the mapping does
  not know is outcome unknown, `venue status not mapped`, as reported.

**The value-date roll** (contract v23, W11.25; the spot-FX plugin's Q3,
ruled 2026-10-13). A fill moves the book's two cash positions pending to the
value date the date read gives. OANDA never settles a spot position: at each
17:00 New York it rolls every open trade to the next spot date and charges
or credits its financing (its DAILY_FINANCING transaction). The plugin
reports each trade's financing first, a charge on the placement that opened
it, then, for each pair OANDA rolled, a value-date roll
(ReportValueDateRoll): the venue account code, the pair's two cash
instruments, the value date the pending lines leave, the date read's answer
for the roll's moment with its basis (never a date worked out here), the
roll's moment, the `CHG-` IDs of the pair's financing charges, and the
transaction as its raw record. The pending dates its fills and rolls left
in the book, by pair, are kept in storage (`rolling`), so it knows what each
roll moves; where the date read answers none, the roll waits, kept, and is
asked again each poll. **A catch-up roll** (approved 2026-10-13): a fill
reported after OANDA rolled its pair -- one that waited for its value date,
or a venue-originated fill its `ems` accepted later -- pends to the value
date it was recorded with, and the plugin reports a roll for it from that
date to the date read's answer for OANDA's latest roll's moment, its
financing the charges it reported at the rolls since. Whether OANDA rolled a
fill is read from the transactions' own order: a roll whose transaction
follows the fill's rolled it.

**Venue-originated fills** (contract v23, W11.11, W11.23; the order-path
plan's rulings 1 and 8 to 19, ruled 2026-10-13). OANDA's transactions show
every order on the account, those taken in OANDA's own platform or app, and
those OANDA makes itself (a stop loss, a take profit, a margin closeout),
too. A fill of an order no placement names, in a currency pair, is reported
on no placement: the venue account code, the base currency's cash
instrument, the side, OANDA's order ID as the venue order ID and its client
order ID where one was sent, with its value date as any fill's, so the book
holds it unmatched until its `ems` accepts it or answers it DK. Its trade's
financing is reported on no placement, kept unplaced. Once accepted, the
book creates a placement at filled naming OANDA's order; the plugin hears it
(PlacementUpdated), reports the trade's financing charges again on it, and
a catch-up roll where OANDA rolled the trade before the accept; the trade's
later financing is charged to it. OANDA takes no execution acknowledgement,
and its catalogue says so. Only fills after the plugin first watched the
account are reported: what came before is the account's history, which
custody's statement and activity hold. A fill in an instrument that is not
a currency pair (OANDA's metals and CFDs) is not this plugin's, and is said
once and left.

**A change the `ems` asks for** (W11.9; approved 2026-10-13). The placement
carries its change requests (`Placement.change_requests`), and the plugin
tells a cancel from a replace by the request's kind. A cancel is sent to
OANDA's cancel endpoint, its ORDER_CANCEL reporting it cancelled. A replace
is sent to OANDA's replace endpoint (the order cancelled and a new one made
in its place), reported replaced with the new order's ID, its client
extension ID derived from the placement's (`<placement>.r<n>`, since a
client ID is not reused). A change OANDA refused is the placement rejected
while it works, the request ending refused (transitions row 14).

**The firm's controls.** It reads the trading instructions in force on start
and hears each change. Under a pause or a stop on All accounts, on this
instance or on a placement's account, nothing new is sent: a queued
placement is held, and sent once lifted if the book still holds it queued. A
stop cancels at OANDA at once every order it covers, reporting each cancel
as a fact naming the stop's entry (W11.9).

**Its catalogue.** On start and whenever trading is enabled, it publishes
what OANDA's order endpoint takes for its account (ReportVenueCatalogue):
market (FOK, IOC), limit and stop (GTC, GTD, day as OANDA's GFD, FOK, IOC),
none while the connection is read-only; never a strategy (OANDA has no
algorithms); and no execution acknowledgement
(`EXECUTION_ACKNOWLEDGEMENT_NONE`): OANDA's v20 API takes none.

**Tests and `make e2e` trade only with the synthetic OANDA**
(synthetic.py). Never OANDA's practice or live hosts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

import meridian
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops

from . import moments
from .connection import Connection
from .declared import seen_in_fill
from .kept import Kept
from .pairs import PairMap
from .raw import CANCEL, OFFERED, ORDER, PLACE, REPLACE, STREAMED, RawStore, call, failed
from .venue import Json, VenueError, as_json

log = logging.getLogger("oanda.trading")

POLL_SECONDS = 5
SYNTHETIC_POLL_SECONDS = 1

#: The source an identifier OANDA names is stated under.
SOURCE = "oanda"

#: The schemes OANDA's words are kept in as reported.
REJECT_SCHEME = "oanda:reject-reason"
STATE_SCHEME = "oanda:order-state"
ERROR_SCHEME = "oanda:error"

#: OANDA's order types this plugin sends, with the times in force each takes
#: (OANDA's order request definitions): a market order fills or is cancelled
#: at once.
ORDER_TYPES: dict[int, str] = {
    ops.ORDER_TYPE_MARKET: "MARKET",
    ops.ORDER_TYPE_LIMIT: "LIMIT",
    ops.ORDER_TYPE_STOP: "STOP",
}
RESTING = (
    ops.TIME_IN_FORCE_GOOD_TILL_CANCELLED,
    ops.TIME_IN_FORCE_GOOD_TILL_DATE,
    ops.TIME_IN_FORCE_DAY,
    ops.TIME_IN_FORCE_FILL_OR_KILL,
    ops.TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
)
OFFERED_TIMES: dict[int, tuple[int, ...]] = {
    ops.ORDER_TYPE_MARKET: (
        ops.TIME_IN_FORCE_FILL_OR_KILL,
        ops.TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
    ),
    ops.ORDER_TYPE_LIMIT: RESTING,
    ops.ORDER_TYPE_STOP: RESTING,
}
TIMES_IN_FORCE: dict[int, str] = {
    ops.TIME_IN_FORCE_GOOD_TILL_CANCELLED: "GTC",
    ops.TIME_IN_FORCE_GOOD_TILL_DATE: "GTD",
    # A day order is OANDA's good for day (GFD).
    ops.TIME_IN_FORCE_DAY: "GFD",
    ops.TIME_IN_FORCE_FILL_OR_KILL: "FOK",
    ops.TIME_IN_FORCE_IMMEDIATE_OR_CANCEL: "IOC",
}

#: OANDA's order states, as this plugin converts them.
WORKING = frozenset({"PENDING", "TRIGGERED"})
FILLED = "FILLED"
CANCELLED = "CANCELLED"

#: An ORDER_CANCEL's reasons that end an order expired rather than cancelled.
EXPIRED_REASONS = frozenset({"TIME_IN_FORCE_EXPIRED"})
REPLACED_REASON = "CLIENT_REQUEST_REPLACED"

HOLDING = (ops.TRADING_INSTRUCTION_VALUE_PAUSE, ops.TRADING_INSTRUCTION_VALUE_STOP)

#: The fees an ORDER_FILL states, each its own Fee, by its kind: OANDA's
#: commission is the broker's (`FEE_KIND_COMMISSION`, contract v23, as FIX
#: keeps Commission (12) apart). A guaranteed stop loss's premium is no kind
#: of the contract's closed list (exchange, cancel, network, financing,
#: commission), so it is sent with the kind unspecified, as OANDA states it.
FILL_FEES: dict[str, ops.FeeKind] = {
    "commission": ops.FEE_KIND_COMMISSION,
    "guaranteedExecutionFee": ops.FEE_KIND_UNSPECIFIED,
}

#: OANDA's units' sign, as the contract's side.
BUY, SELL = ops.SIDE_BUY, ops.SIDE_SELL


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def execution_id(account_id: str, transaction_id: str) -> str:
    """A fill's derived execution ID: the account and the fill's transaction
    ID, the same however often it is read (requirement 33)."""
    return f"{account_id}:{transaction_id}"


def replacement_client_id(placement_id: str, n: int) -> str:
    """The client extension ID of the n-th order replacing a placement's."""
    return f"{placement_id}.r{n}"


def placement_of_client(client_id: str) -> str:
    return client_id.split(".r", 1)[0]


def _opened(fill: Json) -> str:
    """The trade a fill opened, by OANDA's ID; "" for none."""
    opened = fill.get("tradeOpened")
    return str(opened.get("tradeID") or "") if isinstance(opened, dict) else ""


def _pair_key(code: str, pair: str) -> str:
    """A pair on a venue account code, as the roll's state keeps it."""
    return f"{code}|{pair}"


def _txn(transaction_id: str) -> int:
    """OANDA's transaction ID as a number: its transactions' own order."""
    try:
        return int(transaction_id)
    except ValueError:
        return 0


def _charge_id(done: Any) -> str:
    """The `CHG-` ID of a charge the book placed; "" for one kept unplaced."""
    charge = getattr(done, "charge", None)
    return str(getattr(charge, "charge_id", "") or "")


def basis_of(basis: ops.DateBasis) -> meridian.DateBasis:
    """The date read's basis, as the SDK's DateBasis carries it on a fill."""
    rule = basis.rule
    return meridian.DateBasis(
        rule=meridian.DateRuleRef(kind=rule.kind, subject=rule.subject, version=rule.version),
        calendars=list(basis.calendars),
    )


class Sent:
    """The placements sent to OANDA, noted in storage before each send, so a
    restart never sends one twice (Q17)."""

    def __init__(self, kept: Kept) -> None:
        self._kept = kept

    def __contains__(self, placement_id: object) -> bool:
        return placement_id in self._kept.value

    def note(self, placement_id: str) -> None:
        if placement_id not in self._kept.value:
            self._kept.value.append(placement_id)
            self._kept.save()


@dataclass
class Worked:
    """A placement OANDA holds an order for, as this plugin works it."""

    placement_id: str
    order_id: str
    code: str
    pair: str
    base_instrument: str
    quote_instrument: str
    quote: str
    quantity: Decimal
    order_ids: list[str] = field(default_factory=list)
    filled: Decimal = Decimal(0)
    executions: dict[str, str] = field(default_factory=dict)
    acknowledged: bool = False
    unknown: str = ""
    unanswered: bool = False
    ended: str = ""
    cancel_sent: bool = False
    changes: set[str] = field(default_factory=set)
    replaced: int = 0
    replacing: tuple[str, Decimal, ops.Money | None, str] | None = None

    @property
    def oanda_order_id(self) -> str:
        return self.order_ids[-1] if self.order_ids else ""

    def client_ids(self) -> set[str]:
        return {self.placement_id} | {
            replacement_client_id(self.placement_id, n) for n in range(1, self.replaced + 1)
        }


@dataclass
class Shown:
    """What the Connections page shows of trading: counts and states only."""

    working: int = 0
    held: int = 0
    unknown: int = 0
    waiting: int = 0
    last_poll: datetime | None = None
    error: str = ""


class Trader:
    """OANDA's connectivity work, beside its custody reads."""

    def __init__(
        self,
        plugin: Any,
        connection: Callable[[], Connection | None],
        *,
        now: Callable[[], datetime],
        pairs: PairMap,
        raw: RawStore | None = None,
        secrets: Callable[[], Sequence[str]] = lambda: (),
        sent: Kept | None = None,
        cursor: Kept | None = None,
        trades: Kept | None = None,
        waiting: Kept | None = None,
        rolling: Kept | None = None,
        scope: Callable[[], meridian.AccountScope] = meridian.AccountScope,
    ) -> None:
        self._plugin = plugin
        self._connection = connection
        self._now = now
        self._pairs = pairs
        self._raw = raw
        self._secrets = secrets
        self._scope = scope
        self.me: str = plugin.identity.instance_id
        self._sent = Sent(sent if sent is not None else Kept(None, []))
        # The last transaction taken, by account.
        self._cursor = cursor if cursor is not None else Kept(None, {})
        # Each trade a placement's fill opened, and each charge reported.
        self._trades = trades if trades is not None else Kept(None, {})
        # Fills waiting for their value date, as OANDA stated them.
        self._waiting = waiting if waiting is not None else Kept(None, [])
        # The value-date roll's state (contract v23): by pair, the pending
        # dates its fills and rolls left in the book and OANDA's latest roll;
        # the venue-originated fills and their trades' charges; the charges
        # of a trade whose opening fill waits; the rolls waiting for a date.
        self._rolling = rolling if rolling is not None else Kept(None, {})
        self.instructions: dict[tuple[Any, ...], ops.TradingInstruction] = {}
        self.working: dict[str, Worked] = {}
        self.held: dict[str, ops.Placement] = {}
        self.decided: set[str] = set()
        self.outside: set[str] = set()
        self.watermark: ops.Watermark | None = None
        self.published: dict[str, tuple[tuple[int, tuple[int, ...]], ...]] = {}
        self.shown = Shown()
        self._lock = asyncio.Lock()
        self._said: set[str] = set()
        # The date read's last answer that gave no date, for the log.
        self._last_outcome = ""
        # The currencies reported missing in this process.
        self._missing: set[str] = set()
        # The account's currency, in which OANDA states fees, financing and
        # profit: read from the account on start.
        self.home = "USD"

    # ── Reading the book ────────────────────────────────────────────────

    async def start(self) -> None:
        """On start: the instructions in force, the placements it works, the
        pair map, and its catalogue."""
        listed = await self._plugin.list_trading_instructions()
        for instruction in listed.instructions:
            self._hold(instruction)
        await self._read_pairs()
        await self._read_home()
        await self.catch_up(since=None)
        await self.publish()

    async def _read_home(self) -> None:
        connection = self._connection()
        if connection is None:
            return
        try:
            held = await connection.venue.account(connection.account_id)
        except VenueError as failure:
            log.warning("the account's currency was not read: %s", failure)
            return
        account = as_json(held.get("account"))
        self.home = str(account.get("currency") or self.home)
        if not self._cursor.value.get(connection.account_id):
            # First start on this account: what came before is no
            # placement's; transactions are taken from here on.
            self._cursor.value[connection.account_id] = str(
                held.get("lastTransactionID") or "0"
            )
            self._cursor.save()

    async def catch_up(self, since: ops.Watermark | None) -> None:
        """The placements naming it, as the book holds them now or changed
        since `since`."""
        cursor = ""
        while True:
            listed = await self._plugin.list_orders(since=since, cursor=cursor)
            orders = {order.order_id: order for order in listed.orders}
            for order in listed.orders:
                for placement in order.placements:
                    await self._resume(placement, order, listed.executions)
            for placement in listed.placements:
                await self._resume(placement, orders.get(placement.order_id), listed.executions)
            if listed.HasField("as_of"):
                self.watermark = listed.as_of
            if not listed.next_cursor:
                return
            cursor = listed.next_cursor

    async def _resume(
        self,
        placement: ops.Placement,
        order: ops.Order | None,
        executions: Sequence[ops.Execution],
    ) -> None:
        if placement.connectivity_instance_id != self.me:
            return
        state = placement.status.state
        worked = self.working.get(placement.placement_id)
        if worked is None and await self._adopt(placement):
            return
        if worked is None and state in (ops.PLACEMENT_STATE_SENT, ops.PLACEMENT_STATE_WORKING):
            order = order if order is not None else await self._order_of(placement)
            if order is None:
                return
            worked = await self._worked(placement, order)
            if worked is None:
                return
            ids = list(placement.venue_order_ids)
            if placement.venue_order_id and placement.venue_order_id not in ids:
                ids.append(placement.venue_order_id)
            worked.order_ids = ids
            worked.acknowledged = bool(ids)
            worked.unanswered = not ids
            for execution in executions:
                if (
                    execution.placement_id == placement.placement_id
                    and not execution.supersedes
                ):
                    worked.executions[execution.venue_execution_id] = execution.execution_id
                    worked.filled += as_decimal(execution.quantity)
            self.working[placement.placement_id] = worked
            self.decided.add(placement.placement_id)
            log.info("working %s again: %s filled", placement.placement_id, worked.filled)
        if (
            state == ops.PLACEMENT_STATE_QUEUED
            and placement.placement_id not in self.decided
            and placement.placement_id not in self.working
        ):
            await self.on_queued(placement, order)
        if worked is not None:
            await self._asked_to_change(placement, order, worked)

    async def _order_of(self, placement: ops.Placement) -> ops.Order | None:
        listed = await self._plugin.list_orders(account_id=placement.account_id)
        for order in listed.orders:
            if order.order_id == placement.order_id:
                found: ops.Order = order
                return found
        return None

    # ── Hearing ─────────────────────────────────────────────────────────

    async def on_placement(self, heard: Any) -> None:
        placement: ops.Placement = heard.message.placement
        if placement.connectivity_instance_id != self.me:
            return
        async with self._lock:
            await self._resume(placement, None, ())
            await self._take_transactions()

    async def on_instruction(self, heard: Any) -> None:
        instruction: ops.TradingInstruction = heard.message.instruction
        async with self._lock:
            self._hold(instruction)
            if instruction.value == ops.TRADING_INSTRUCTION_VALUE_STOP:
                await self._stop(instruction)
            elif instruction.value == ops.TRADING_INSTRUCTION_VALUE_ACTIVE:
                await self._release_held()

    @staticmethod
    def _key(instruction: ops.TradingInstruction) -> tuple[Any, ...]:
        target = instruction.target
        return (
            target.all_accounts,
            target.account_id,
            target.instance_id,
            target.subject,
            target.service_account_id,
            instruction.setter_level,
        )

    def _hold(self, instruction: ops.TradingInstruction) -> None:
        key = self._key(instruction)
        if instruction.value in HOLDING:
            self.instructions[key] = instruction
        else:
            self.instructions.pop(key, None)

    def _covers(self, instruction: ops.TradingInstruction, account_id: str) -> bool:
        """Whether an instruction binds this plugin's placements in an
        account: on All accounts, this instance, or the account. One aimed at
        a person or a service account binds the plugins acting for them."""
        target = instruction.target
        if target.subject or target.service_account_id:
            return False
        if target.instance_id and target.instance_id != self.me:
            return False
        if target.account_id:
            return bool(target.account_id == account_id)
        return bool(target.all_accounts or target.instance_id == self.me)

    def in_force(self, account_id: str) -> ops.TradingInstruction | None:
        binding = [i for i in self.instructions.values() if self._covers(i, account_id)]
        binding.sort(key=lambda i: i.value != ops.TRADING_INSTRUCTION_VALUE_STOP)
        return binding[0] if binding else None

    async def _stop(self, instruction: ops.TradingInstruction) -> None:
        """A stop's cancels at OANDA at once, each reported as a fact naming
        the stop's entry (W11.9), never waiting for the book."""
        for worked in list(self.working.values()):
            if worked.ended or worked.cancel_sent or not worked.oanda_order_id:
                continue
            account = await self._account_of(worked)
            if account and not self._covers(instruction, account):
                continue
            await self._cancel(worked, cause=instruction.entry, reason="a stop is in force")
        await self._take_transactions()

    async def _account_of(self, worked: Worked) -> str:
        listed = await self._plugin.list_orders()
        for order in listed.orders:
            if order.order_id == worked.order_id:
                return str(order.account_id)
        return ""

    async def _release_held(self) -> None:
        for placement_id, placement in list(self.held.items()):
            if self.in_force(placement.account_id) is not None:
                continue
            self.held.pop(placement_id, None)
            order = await self._order_of(placement)
            current = next(
                (
                    p
                    for p in (order.placements if order else ())
                    if p.placement_id == placement_id
                ),
                placement,
            )
            if current.status.state == ops.PLACEMENT_STATE_QUEUED:
                await self.on_queued(current, order)
        await self._take_transactions()

    # ── The pair map ────────────────────────────────────────────────────

    async def _read_pairs(self) -> None:
        connection = self._connection()
        if connection is None:
            return
        try:
            self._pairs.take(await connection.venue.instruments(connection.account_id))
        except VenueError as failure:
            log.warning("the pair map was not read again: %s", failure)

    async def _cash(self, code: str) -> str:
        """The deployment's cash instrument for a currency, by its ISO 4217
        code (W3.1); "" where it holds none, which is reported missing, once
        in the process for a fill asked about again each poll."""
        identifiers = [meridian.Identifier(scheme="iso4217", value=code)]
        found = await self._plugin.resolve_identifier(
            identifiers=identifiers,
            stated_asset_class="cash",
            stated_currency=code,
        )
        if found.instrument_id:
            return str(found.instrument_id)
        if code in self._missing:
            return ""
        self._missing.add(code)
        now_ns = moments.of(self._now())
        await self._plugin.report_missing_instrument(
            source=SOURCE,
            asset_class="cash",
            identifiers=identifiers,
            as_of_ns=now_ns,
            reason=found.miss_reason or ops.MISS_REASON_NOT_FOUND,
            observed_at_ns=now_ns,
        )
        return ""

    async def _currency_of(self, instrument_id: str) -> str:
        """The ISO 4217 code of a cash instrument; "" for one that is not
        cash or names none."""
        found = await self._plugin.resolve_instrument(instrument_id=instrument_id)
        if not found.found:
            return ""
        record = found.instrument
        if record.asset_class not in (ops.ASSET_CLASS_CASH,):
            return ""
        for held in record.identifiers:
            if held.scheme == "iso4217" and held.value:
                return str(held.value)
        return str(record.currency or "")

    async def _quote_of(
        self, placement: ops.Placement, order: ops.Order
    ) -> tuple[str, str, str]:
        """The order's quote currency: its code, the cash instrument the
        order names for it ("" where it names none), and why it has none.

        From contract v23 the order names it (`Order.quote_currency`, W11.1),
        so a market order, which states no price, names its pair (W11.8);
        an order naming none is read from its limit or stop price, in the
        quote currency, as in v22."""
        priced = (
            placement.limit_price
            if placement.HasField("limit_price")
            else order.limit_price
            if order.HasField("limit_price")
            else order.stop_price
            if order.HasField("stop_price")
            else None
        )
        stated = ""
        if priced is not None:
            stated = priced.currency_code or (
                await self._currency_of(priced.instrument_id) if priced.instrument_id else ""
            )
        if order.quote_currency:
            quote = await self._currency_of(order.quote_currency)
            if not quote:
                return "", "", "the order's quote currency is no currency's cash instrument"
            if stated and stated != quote:
                # The book refuses this on the proposal (W11.1); held here too.
                return (
                    "",
                    "",
                    f"the order's price is in {stated}, not its quote currency {quote}",
                )
            return quote, str(order.quote_currency), ""
        if not stated:
            return (
                "",
                "",
                "the order names no quote currency (Order.quote_currency) and no limit or stop "
                "price in it",
            )
        return stated, "", ""

    # ── Sending ─────────────────────────────────────────────────────────

    async def on_queued(self, placement: ops.Placement, order: ops.Order | None) -> None:
        pid = placement.placement_id
        if self.in_force(placement.account_id) is not None:
            self.held[pid] = placement
            log.info("holding %s back: trading is paused or stopped", pid)
            return
        connection = self._connection()
        if connection is None:
            return
        order = order if order is not None else await self._order_of(placement)
        if order is None:
            return
        if pid in self._sent:
            # Sent before this process: asked about, never sent again.
            worked = await self._worked(placement, order)
            if worked is not None:
                worked.unanswered = True
                self.working[pid] = worked
                self.decided.add(pid)
            return
        self.decided.add(pid)
        why, worked = await self._decide(connection, placement, order)
        if why or worked is None:
            await self._reject_unsupported(connection, placement, why)
            return
        body = self._body(placement, order, worked, worked.placement_id)
        self._sent.note(pid)
        self.working[pid] = worked
        try:
            answer = await connection.venue.place(connection.account_id, body)
        except VenueError as failure:
            raw = self._keep(
                connection,
                [failed(PLACE, str(failure), failure.said or None, _note(body))],
                "place",
            )
            await self._send_failed(worked, failure, raw)
            return
        raw = self._keep(connection, [call(PLACE, answer, _note(body))], "place")
        await self._answered(connection, worked, answer, raw)

    async def _decide(
        self, connection: Connection, placement: ops.Placement, order: ops.Order
    ) -> tuple[str, Worked | None]:
        """Why OANDA or this plugin does not take the placement, or what it
        is sent as."""
        if placement.venue_account_code != connection.code:
            return (
                "OANDA holds no account under this venue account code for this instance",
                None,
            )
        if not connection.trading:
            return "trading is not enabled on this connection: it is read-only", None
        if placement.HasField("strategy") and placement.strategy.kind:
            return "OANDA works no strategy: it has no broker algorithms", None
        if order.side not in (ops.SIDE_BUY, ops.SIDE_SELL):
            return "OANDA takes buys and sells of a currency: no short sale", None
        if order.order_type not in ORDER_TYPES:
            return "OANDA's catalogue lists no such order type", None
        tif = placement.time_in_force or order.time_in_force
        if tif not in OFFERED_TIMES[order.order_type]:
            return "OANDA's catalogue lists no such time in force for the order type", None
        if tif == ops.TIME_IN_FORCE_GOOD_TILL_DATE and not (
            order.expire_time_ns or order.expire_date
        ):
            return "a good-till-date order states no date", None
        if order.HasField("cash_quantity"):
            return "OANDA takes units of the base currency, never an amount of cash", None
        base = await self._currency_of(order.instrument_id)
        if not base:
            return "the order's instrument is no currency's cash instrument", None
        if order.dealt_currency and order.dealt_currency != order.instrument_id:
            return (
                "OANDA takes the quantity in units of the base currency; this order deals in "
                "the other currency",
                None,
            )
        quote, named, why = await self._quote_of(placement, order)
        if why:
            return why, None
        pair = self._pairs.find(base, quote)
        if pair is None:
            await self._read_pairs()
            pair = self._pairs.find(base, quote)
        if pair is None:
            other = self._pairs.quoted_the_other_way(base, quote)
            if other is not None:
                return (
                    f"OANDA quotes the pair as {other.name}: the order is placed in "
                    f"{other.base}'s cash instrument, priced in {other.quote}",
                    None,
                )
            return f"OANDA lists no {base}_{quote}", None
        if not pair.tradeable:
            return f"OANDA lists {pair.name} as {pair.kind}, not a currency pair", None
        quote_instrument = named or pair.quote_instrument or await self._cash(quote)
        if not quote_instrument:
            return f"the deployment holds no cash instrument for {quote}", None
        pair = self._pairs.resolved(pair.name, order.instrument_id, quote_instrument) or pair
        return "", Worked(
            placement_id=placement.placement_id,
            order_id=order.order_id,
            code=connection.code,
            pair=pair.name,
            base_instrument=order.instrument_id,
            quote_instrument=quote_instrument,
            quote=quote,
            quantity=as_decimal(placement.quantity),
        )

    def _body(
        self,
        placement: ops.Placement,
        order: ops.Order,
        worked: Worked,
        client_id: str,
        *,
        quantity: Decimal | None = None,
        limit: ops.Money | None = None,
        tif: int = 0,
    ) -> Json:
        """The order as OANDA's order request takes it."""
        units = quantity if quantity is not None else as_decimal(placement.quantity)
        signed = units if order.side == ops.SIDE_BUY else -units
        tif = tif or placement.time_in_force or order.time_in_force
        made: Json = {
            "type": ORDER_TYPES[order.order_type],
            "instrument": worked.pair,
            "units": f"{signed:f}",
            "timeInForce": TIMES_IN_FORCE[tif],
            "positionFill": "DEFAULT",
            "clientExtensions": {"id": client_id, "tag": "meridian", "comment": order.order_id},
        }
        if order.order_type == ops.ORDER_TYPE_LIMIT:
            price = limit or (
                placement.limit_price
                if placement.HasField("limit_price")
                else order.limit_price
            )
            made["price"] = f"{as_decimal(price.amount):f}"
        elif order.order_type == ops.ORDER_TYPE_STOP:
            made["price"] = f"{as_decimal(order.stop_price.amount):f}"
        if tif == ops.TIME_IN_FORCE_GOOD_TILL_DATE:
            made["gtdTime"] = moments.rfc3339(self._expiry_ns(order))
        return {"order": made}

    @staticmethod
    def _expiry_ns(order: ops.Order) -> int:
        """A good-till-date order's end: its time, or its date's end as
        OANDA's trading day ends, 17:00 New York."""
        if order.expire_time_ns:
            return int(order.expire_time_ns)
        day = date.fromisoformat(order.expire_date)
        ends = datetime.combine(day, time(moments.DAY_ENDS), tzinfo=moments.NEW_YORK)
        return moments.of(ends)

    async def _answered(
        self, connection: Connection, worked: Worked, answer: Json, raw: ops.RawRecordRef
    ) -> None:
        """OANDA's answer to a send: acknowledged by its create transaction;
        with none, the order asked for by its client ID."""
        created = answer.get("orderCreateTransaction")
        if isinstance(created, dict) and created.get("id"):
            await self._acknowledged(worked, str(created["id"]), created.get("time"), raw)
            await self._take_transactions()
            return
        # Answered with no create transaction: what OANDA holds is asked.
        await self._ask(connection, worked)

    async def _acknowledged(
        self, worked: Worked, oanda_order_id: str, at: Any, raw: ops.RawRecordRef
    ) -> None:
        if oanda_order_id not in worked.order_ids:
            worked.order_ids.append(oanda_order_id)
        if worked.acknowledged and not worked.unknown:
            return
        worked.acknowledged = True
        worked.unknown = ""
        worked.unanswered = False
        await self._plugin.report_placement(
            placement_id=worked.placement_id,
            event=ops.PLACEMENT_EVENT_ACKNOWLEDGED,
            venue_order_id=oanda_order_id,
            routed_at_ns=moments.ns(at) or moments.of(self._now()),
            routed_precision=ops.TIME_PRECISION_NANOSECOND,
            raw_record=raw,
        )

    async def _send_failed(
        self, worked: Worked, failure: VenueError, raw: ops.RawRecordRef
    ) -> None:
        if failure.answered:
            worked.ended = "rejected"
            rejected = failure.said.get("orderRejectTransaction")
            if isinstance(rejected, dict) and rejected.get("rejectReason"):
                said = ops.AsReported(
                    scheme=REJECT_SCHEME,
                    code=str(rejected["rejectReason"]),
                    text=str(failure.said.get("errorMessage") or rejected["rejectReason"]),
                )
            else:
                code = str(failure.said.get("errorCode") or failure.status or "")
                said = ops.AsReported(
                    scheme=ERROR_SCHEME,
                    code=code,
                    text=str(failure.said.get("errorMessage") or "OANDA refused the order"),
                )
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=ops.PLACEMENT_EVENT_REJECTED,
                rejection_reason=ops.REJECTION_REASON_VENUE_REJECTED,
                rejection_as_reported=said,
                raw_record=raw,
            )
            return
        # Never answered: asked about each poll, never sent again.
        worked.unanswered = True
        await self._plugin.report_placement(
            placement_id=worked.placement_id,
            event=ops.PLACEMENT_EVENT_OUTCOME_UNKNOWN,
            unknown_reason=ops.UNKNOWN_REASON_REQUEST_UNANSWERED,
            raw_record=raw,
        )

    async def _reject_unsupported(
        self, connection: Connection, placement: ops.Placement, why: str
    ) -> None:
        stated: Json = {
            "why": why,
            "venue_account_code": placement.venue_account_code,
            "connection": connection.connection_id,
            "trading": connection.trading,
            "catalogue": [
                {
                    "order_type": ops.OrderType.Name(t),
                    "times_in_force": [ops.TimeInForce.Name(f) for f in tifs],
                }
                for t, tifs in self.catalogue(connection)
            ],
        }
        raw = self._keep(connection, [call(OFFERED, stated, why)], "offered")
        log.info("rejected %s unsupported: %s", placement.placement_id, why)
        await self._plugin.report_placement(
            placement_id=placement.placement_id,
            event=ops.PLACEMENT_EVENT_REJECTED,
            rejection_reason=ops.REJECTION_REASON_UNSUPPORTED,
            raw_record=raw,
        )

    async def _worked(self, placement: ops.Placement, order: ops.Order) -> Worked | None:
        connection = self._connection()
        if connection is None:
            return None
        base = await self._currency_of(order.instrument_id)
        quote, named, _ = await self._quote_of(placement, order)
        pair = self._pairs.find(base, quote) if base and quote else None
        if pair is None:
            await self._read_pairs()
            pair = self._pairs.find(base, quote) if base and quote else None
        if pair is None:
            log.warning("%s: its pair is not in OANDA's list", placement.placement_id)
            return None
        quote_instrument = named or pair.quote_instrument or await self._cash(quote)
        return Worked(
            placement_id=placement.placement_id,
            order_id=order.order_id,
            code=connection.code,
            pair=pair.name,
            base_instrument=order.instrument_id,
            quote_instrument=quote_instrument,
            quote=quote,
            quantity=as_decimal(placement.quantity),
        )

    # ── Asking about an order ───────────────────────────────────────────

    async def _ask(self, connection: Connection, worked: Worked) -> None:
        """What OANDA holds for a placement whose send went unanswered, or
        was answered with no create transaction: its order, by client ID."""
        specifier = f"@{worked.placement_id}"
        try:
            answer = await connection.venue.order(connection.account_id, specifier)
        except VenueError as failure:
            if failure.status == 404:
                self._once(
                    f"ask:{worked.placement_id}",
                    f"{worked.placement_id}: OANDA holds no order for it yet; asked "
                    "again each poll, never sent again",
                )
                return
            log.warning("%s: %s", worked.placement_id, failure)
            return
        raw = self._keep(connection, [call(ORDER, answer, specifier)], "order")
        held = as_json(answer.get("order"))
        state = str(held.get("state") or "")
        oanda_order_id = str(held.get("id") or "")
        if state not in WORKING | {FILLED, CANCELLED}:
            if worked.unknown != state:
                worked.unknown = state
                worked.unanswered = False
                await self._plugin.report_placement(
                    placement_id=worked.placement_id,
                    event=ops.PLACEMENT_EVENT_OUTCOME_UNKNOWN,
                    unknown_reason=ops.UNKNOWN_REASON_VENUE_STATUS_NOT_MAPPED,
                    status_as_reported=ops.AsReported(
                        scheme=STATE_SCHEME, code=state, text=state
                    ),
                    raw_record=raw,
                )
            return
        await self._acknowledged(worked, oanda_order_id, held.get("createTime"), raw)
        # Its fill or cancel follows from the transactions, read next.

    # ── Changing: a cancel or a replace ─────────────────────────────────

    async def _asked_to_change(
        self, placement: ops.Placement, order: ops.Order | None, worked: Worked
    ) -> None:
        pending = [
            request
            for request in placement.change_requests
            if request.outcome == ops.CHANGE_REQUEST_OUTCOME_PENDING
            and request.HasField("placement_change")
            and request.change_request_id not in worked.changes
        ]
        if not pending or worked.ended:
            return
        if not worked.acknowledged or not worked.oanda_order_id:
            # Answered once OANDA has acknowledged the order: the book
            # delivers the placement again with its acknowledgement.
            return
        worked.changes.update(request.change_request_id for request in pending)
        kinds = [request.placement_change.kind for request in pending]
        if ops.PLACEMENT_CHANGE_KIND_CANCEL in kinds:
            if not worked.cancel_sent:
                await self._cancel(worked, cause=None, reason="")
            return
        replaces = [
            request
            for request in pending
            if request.placement_change.kind == ops.PLACEMENT_CHANGE_KIND_REPLACE
        ]
        if replaces:
            await self._replace(placement, order, worked, replaces[-1])

    async def _cancel(
        self, worked: Worked, *, cause: ops.EntryMeta | None, reason: str
    ) -> None:
        connection = self._connection()
        if connection is None or not worked.oanda_order_id:
            return
        worked.cancel_sent = True
        try:
            answer = await connection.venue.cancel(connection.account_id, worked.oanda_order_id)
        except VenueError as failure:
            raw = self._keep(
                connection,
                [failed(CANCEL, str(failure), failure.said or None, worked.oanda_order_id)],
                "cancel",
            )
            log.info("%s: the cancel was not taken: %s", worked.placement_id, failure)
            worked.cancel_sent = False
            if failure.answered and cause is None:
                await self._change_refused(worked, failure, raw)
            return
        self._keep(connection, [call(CANCEL, answer, worked.oanda_order_id)], "cancel")
        if cause is not None:
            await self._plugin.request_placement_change(
                placement_id=worked.placement_id,
                change=meridian.PlacementChange(kind=ops.PLACEMENT_CHANGE_KIND_CANCEL),
                reason=reason,
                cause=ops.EntryMeta(entry_id=cause.entry_id),
                idempotency_key=f"stop:{cause.entry_id}:{worked.placement_id}",
            )

    async def _replace(
        self,
        placement: ops.Placement,
        order: ops.Order | None,
        worked: Worked,
        request: ops.ChangeRequest,
    ) -> None:
        connection = self._connection()
        order = order if order is not None else await self._order_of(placement)
        if connection is None or order is None or not worked.oanda_order_id:
            return
        change = request.placement_change
        quantity = (
            as_decimal(change.quantity) if change.HasField("quantity") else worked.quantity
        )
        limit = (
            change.limit_price
            if change.HasField("limit_price")
            else (placement.limit_price if placement.HasField("limit_price") else None)
        )
        tif = change.time_in_force or placement.time_in_force or order.time_in_force
        why = ""
        if order.order_type not in ORDER_TYPES or order.order_type == ops.ORDER_TYPE_MARKET:
            why = "OANDA replaces a limit or stop order that works, never a market order"
        elif tif not in OFFERED_TIMES[order.order_type]:
            why = "OANDA's catalogue lists no such time in force for the order type"
        elif tif == ops.TIME_IN_FORCE_GOOD_TILL_DATE and not (
            order.expire_time_ns or order.expire_date
        ):
            why = "a good-till-date order states no date"
        elif quantity <= worked.filled:
            why = "the new quantity is no more than what is filled"
        if why:
            raw = self._keep(connection, [call(OFFERED, {"why": why}, why)], "offered")
            log.info("%s: the replace was rejected unsupported: %s", worked.placement_id, why)
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=ops.PLACEMENT_EVENT_REJECTED,
                rejection_reason=ops.REJECTION_REASON_UNSUPPORTED,
                raw_record=raw,
            )
            return
        client_id = replacement_client_id(worked.placement_id, worked.replaced + 1)
        body = self._body(
            placement,
            order,
            worked,
            client_id,
            quantity=quantity - worked.filled,
            limit=limit,
            tif=tif,
        )
        try:
            answer = await connection.venue.replace(
                connection.account_id, worked.oanda_order_id, body
            )
        except VenueError as failure:
            raw = self._keep(
                connection,
                [failed(REPLACE, str(failure), failure.said or None, _note(body))],
                "replace",
            )
            if failure.answered:
                await self._change_refused(worked, failure, raw)
                return
            worked.replaced += 1
            worked.replacing = (request.change_request_id, quantity, limit, client_id)
            log.info(
                "%s: the replace was not answered; asked about each poll", worked.placement_id
            )
            return
        worked.replaced += 1
        raw = self._keep(connection, [call(REPLACE, answer, _note(body))], "replace")
        created = answer.get("orderCreateTransaction")
        new_id = str(created.get("id") or "") if isinstance(created, dict) else ""
        await self._replaced(worked, new_id, quantity, limit, raw)
        await self._take_transactions()

    async def _replaced(
        self,
        worked: Worked,
        new_id: str,
        quantity: Decimal,
        limit: ops.Money | None,
        raw: ops.RawRecordRef,
    ) -> None:
        worked.replacing = None
        if new_id and new_id not in worked.order_ids:
            worked.order_ids.append(new_id)
        worked.quantity = quantity
        await self._plugin.report_placement(
            placement_id=worked.placement_id,
            event=ops.PLACEMENT_EVENT_REPLACED,
            venue_order_id=worked.oanda_order_id,
            quantity=quantity,
            limit_price=(
                None
                if limit is None
                else meridian.Money(as_decimal(limit.amount), limit.currency_code)
            ),
            raw_record=raw,
        )

    async def _change_refused(
        self, worked: Worked, failure: VenueError, raw: ops.RawRecordRef
    ) -> None:
        code = str(failure.said.get("errorCode") or failure.status or "")
        text = str(failure.said.get("errorMessage") or "OANDA refused the change")
        await self._plugin.report_placement(
            placement_id=worked.placement_id,
            event=ops.PLACEMENT_EVENT_REJECTED,
            rejection_reason=ops.REJECTION_REASON_VENUE_REJECTED,
            rejection_as_reported=ops.AsReported(scheme=ERROR_SCHEME, code=code, text=text),
            raw_record=raw,
        )

    async def _replace_unanswered(self, connection: Connection, worked: Worked) -> None:
        """A replace OANDA never answered: its new order asked for by the
        client ID it was sent with, reported replaced once OANDA holds it."""
        if worked.replacing is None:
            return
        _, quantity, limit, client_id = worked.replacing
        try:
            answer = await connection.venue.order(connection.account_id, f"@{client_id}")
        except VenueError:
            return
        held = as_json(answer.get("order"))
        if not held.get("id"):
            return
        raw = self._keep(connection, [call(ORDER, answer, f"@{client_id}")], "order")
        await self._replaced(worked, str(held["id"]), quantity, limit, raw)

    # ── The transactions ────────────────────────────────────────────────

    async def poll(self) -> None:
        """One poll: the book's placements changed since the last, the
        unanswered asked about, then the account's transactions since the
        last taken, and fills waiting for their value date asked again."""
        async with self._lock:
            connection = self._connection()
            if connection is None:
                return
            if self.watermark is not None:
                await self.catch_up(since=self.watermark)
            for worked in list(self.working.values()):
                if worked.ended:
                    continue
                if worked.unanswered or worked.unknown:
                    await self._ask(connection, worked)
                if worked.replacing is not None:
                    await self._replace_unanswered(connection, worked)
            await self._take_transactions()
            await self._retry_waiting(connection)
            await self._retry_rolls()
            self.shown = Shown(
                working=sum(1 for w in self.working.values() if not w.ended),
                held=len(self.held),
                unknown=sum(
                    1
                    for w in self.working.values()
                    if not w.ended and (w.unknown or w.unanswered)
                ),
                waiting=len(self._waiting.value)
                + len(self._rolling.value.get("waiting_rolls") or []),
                last_poll=self._now(),
            )

    async def _take_transactions(self) -> None:
        """The account's transactions since the last taken, each in turn."""
        connection = self._connection()
        if connection is None:
            return
        account = connection.account_id
        since = str(self._cursor.value.get(account) or "")
        try:
            if not since:
                # First start on this account: what came before is no
                # placement's; taken from here on.
                held = await connection.venue.account(account)
                self._cursor.value[account] = str(held.get("lastTransactionID") or "0")
                self._cursor.save()
                return
            transactions, _ = await connection.venue.transactions_since(account, since)
        except VenueError as failure:
            self.shown.error = str(failure)
            log.warning("transactions not read: %s", failure)
            return
        for transaction in transactions:
            await self.take(connection, transaction)
            self._cursor.value[account] = str(transaction.get("id") or since)
            self._cursor.save()

    async def take(self, connection: Connection, transaction: Json) -> None:
        """One transaction, as the stream or a read gave it."""
        kind = str(transaction.get("type") or "")
        if kind == "ORDER_FILL":
            await self._fill(connection, transaction)
        elif kind == "ORDER_CANCEL":
            await self._cancelled(connection, transaction)
        elif kind == "DAILY_FINANCING":
            await self._financing(connection, transaction)
        elif kind.endswith("_ORDER") and transaction.get("replacesOrderID"):
            worked = self._by_order(str(transaction["replacesOrderID"]), "")
            if worked is not None and str(transaction.get("id")) not in worked.order_ids:
                worked.order_ids.append(str(transaction.get("id")))

    def _by_order(self, oanda_order_id: str, client_id: str) -> Worked | None:
        for worked in self.working.values():
            if oanda_order_id and oanda_order_id in worked.order_ids:
                return worked
            if client_id and client_id in worked.client_ids():
                return worked
        return None

    async def _fill(self, connection: Connection, fill: Json) -> None:
        worked = self._by_order(
            str(fill.get("orderID") or ""), str(fill.get("clientOrderID") or "")
        )
        if worked is None:
            client = str(fill.get("clientOrderID") or "")
            if client and placement_of_client(client) in self._sent:
                # One of its own, not worked in this process yet: the book is
                # read again, and the fill taken on the next poll.
                self._wait(fill)
                return
            await self._originated(connection, fill)
            return
        await self._report_fill(connection, worked, fill)

    def _wait(self, fill: Json) -> None:
        self._waiting.value.append(fill)
        self._waiting.save()

    async def _value_date(
        self, base_instrument: str, quote_instrument: str, at_ns: int
    ) -> ops.ComputeDateResult | None:
        """The date read's spot date for the pair at a moment (W3.20), or
        None where it answers none: never a date worked out here."""
        settles = await self._plugin.compute_date(
            spot=ops.SpotDateInputs(pair=[base_instrument, quote_instrument], at_ns=at_ns),
            as_of_ns=at_ns,
        )
        if settles.outcome == ops.DATE_OUTCOME_COMPUTED and settles.date:
            found: ops.ComputeDateResult = settles
            return found
        self._last_outcome = ops.DateOutcome.Name(settles.outcome)
        return None

    def _fees(self, fill: Json) -> list[meridian.Fee]:
        """A fill's fees, each with its kind, in the account's currency."""
        fees = []
        for name, kind in FILL_FEES.items():
            amount = _number(fill.get(name))
            if amount:
                fees.append(
                    meridian.Fee(kind=kind or None, amount=meridian.Money(amount, self.home))
                )
        for scheme, name in seen_in_fill(fill, "connectivity"):
            self._plugin.note_not_carried(scheme, name)
        return fees

    async def _report_fill(
        self, connection: Connection, worked: Worked, fill: Json, retried: bool = False
    ) -> bool:
        """A fill reported, or kept waiting for its value date; True where it
        was reported."""
        transaction_id = str(fill.get("id") or "")
        venue_id = execution_id(connection.account_id, transaction_id)
        if venue_id in worked.executions:
            return True
        units, price, at_ns = self._terms(fill)
        if units == 0 or price is None:
            log.warning(
                "%s: a fill states no units or price: %s", worked.placement_id, transaction_id
            )
            return True
        settles = await self._value_date(worked.base_instrument, worked.quote_instrument, at_ns)
        if settles is None:
            if not retried:
                self._wait(fill)
            self._once(
                f"wait:{transaction_id}",
                f"{worked.placement_id}: no value date yet for fill {venue_id} "
                f"({self._last_outcome}); asked again each poll, never guessed",
            )
            return False
        raw = self._keep(connection, [call(STREAMED, fill, transaction_id)], "fill")
        done = await self._plugin.report_execution(
            placement_id=worked.placement_id,
            venue_account_code=worked.code,
            venue_execution_id=venue_id,
            kind=ops.EXECUTION_KIND_VENUE_REPORTED,
            source=ops.EXECUTION_SOURCE_PRIMARY,
            quantity=abs(units),
            price=meridian.Money(price, worked.quote),
            trade_time_ns=at_ns,
            trade_date=moments.trading_day(at_ns).isoformat(),
            finality=meridian.Finality(level=ops.FINALITY_LEVEL_FINAL, at_ns=at_ns),
            computed_settlement_date=settles.date,
            settlement_date_basis=basis_of(settles.basis),
            fees=self._fees(fill),
            raw_record=raw,
            idempotency_key=f"fill:{venue_id}",
        )
        execution = getattr(done, "execution", None)
        worked.executions[venue_id] = str(getattr(execution, "execution_id", "") or "")
        worked.filled += abs(units)
        if worked.filled >= worked.quantity:
            worked.ended = "filled"
        pair_key = self._booked_pair(
            worked.code, worked.pair, worked.base_instrument, worked.quote_instrument
        )
        charge_ids: list[str] = []
        opened = _opened(fill)
        if opened:
            self._trades.value.setdefault("trades", {})[opened] = worked.placement_id
            self._trades.save()
            charge_ids = await self._charge_held(connection, opened)
        await self._closing_charges(connection, fill, transaction_id, at_ns, raw)
        # A fill booked after OANDA rolled its pair: caught up (W11.25).
        await self._catch_up(
            pair_key,
            settles.date,
            transaction_id,
            charge_ids,
            f"catch-up:{worked.code}:{transaction_id}",
        )
        return True

    def _terms(self, fill: Json) -> tuple[Decimal, Decimal | None, int]:
        """A fill's units (signed as OANDA signs them), price and moment."""
        units = _number(fill.get("units")) or Decimal(0)
        price = _number(fill.get("fullVWAP")) or _number(fill.get("price"))
        at_ns = moments.ns(fill.get("time")) or moments.of(self._now())
        return units, price, at_ns

    async def _closing_charges(
        self,
        connection: Connection,
        fill: Json,
        transaction_id: str,
        at_ns: int,
        raw: ops.RawRecordRef,
    ) -> None:
        """A trade a fill closed or reduced settles its financing: a charge
        on the placement that opened it."""
        closing = list(fill.get("tradesClosed") or [])
        if isinstance(fill.get("tradeReduced"), dict):
            closing.append(fill["tradeReduced"])
        for each in closing:
            amount = _number(each.get("financing")) if isinstance(each, dict) else None
            if amount:
                await self._financed(
                    connection,
                    transaction_id,
                    str(each.get("tradeID") or ""),
                    amount,
                    at_ns,
                    raw,
                )

    # ── Venue-originated fills (contract v23) ───────────────────────────

    @property
    def _originals(self) -> dict[str, Json]:
        held: dict[str, Json] = self._rolling.value.setdefault("originated", {})
        return held

    async def _originated(
        self, connection: Connection, fill: Json, retried: bool = False
    ) -> bool:
        """A fill of an order no placement names -- taken in OANDA's own
        platform, or made by OANDA itself -- reported on no placement for
        its `ems` to accept or answer DK (W11.11); True where it is done
        with, False where it waits."""
        transaction_id = str(fill.get("id") or "")
        order_id = str(fill.get("orderID") or "")
        venue_id = execution_id(connection.account_id, transaction_id)
        held = self._originals.get(order_id)
        if held is not None and held.get("execution") == venue_id:
            return True
        name = str(fill.get("instrument") or "")
        pair = self._pairs.get(name)
        if pair is None:
            await self._read_pairs()
            pair = self._pairs.get(name)
        if pair is None or not pair.tradeable:
            self._once(
                f"outside:{transaction_id}",
                f"{connection.code}: OANDA filled an order no placement names ({order_id}) in "
                f"{name}, which is not a currency pair: not this plugin's, kept as received",
            )
            return True
        units, price, at_ns = self._terms(fill)
        if units == 0 or price is None:
            log.warning("%s: a fill states no units or price: %s", order_id, transaction_id)
            return True
        base_instrument = pair.base_instrument or await self._cash(pair.base)
        quote_instrument = pair.quote_instrument or await self._cash(pair.quote)
        if not (base_instrument and quote_instrument):
            if not retried:
                self._wait(fill)
            self._once(
                f"wait:{transaction_id}",
                f"{connection.code}: the deployment holds no cash instrument for {name}'s "
                f"currencies; its fill {venue_id} on no placement asked again each poll",
            )
            return False
        self._pairs.resolved(name, base_instrument, quote_instrument)
        settles = await self._value_date(base_instrument, quote_instrument, at_ns)
        if settles is None:
            if not retried:
                self._wait(fill)
            self._once(
                f"wait:{transaction_id}",
                f"{connection.code}: no value date yet for fill {venue_id} on no placement "
                f"({self._last_outcome}); asked again each poll, never guessed",
            )
            return False
        raw = self._keep(connection, [call(STREAMED, fill, transaction_id)], "fill")
        client = str(fill.get("clientOrderID") or "")
        await self._plugin.report_execution(
            venue_account_code=connection.code,
            instrument_id=base_instrument,
            side=BUY if units > 0 else SELL,
            venue_order_id=order_id,
            venue_client_order_id=client,
            venue_execution_id=venue_id,
            kind=ops.EXECUTION_KIND_VENUE_REPORTED,
            source=ops.EXECUTION_SOURCE_PRIMARY,
            quantity=abs(units),
            price=meridian.Money(price, pair.quote),
            trade_time_ns=at_ns,
            trade_date=moments.trading_day(at_ns).isoformat(),
            finality=meridian.Finality(level=ops.FINALITY_LEVEL_FINAL, at_ns=at_ns),
            computed_settlement_date=settles.date,
            settlement_date_basis=basis_of(settles.basis),
            fees=self._fees(fill),
            raw_record=raw,
            idempotency_key=f"fill:{venue_id}",
        )
        opened = _opened(fill)
        self._originals[order_id] = {
            "code": connection.code,
            "pair": name,
            "base": base_instrument,
            "quote": quote_instrument,
            "value_date": settles.date,
            "txn": transaction_id,
            "execution": venue_id,
            "trade": opened,
            "charges": [],
            "adopted": "",
        }
        self._rolling.save()
        log.info(
            "%s: OANDA filled an order no placement names (%s), taken outside the platform: "
            "reported on no placement, for its ems to accept or answer DK",
            connection.code,
            order_id,
        )
        if opened:
            await self._charge_held(connection, opened)
        await self._closing_charges(connection, fill, transaction_id, at_ns, raw)
        return True

    def _originated_trade(self, trade_id: str) -> Json | None:
        for held in self._originals.values():
            if trade_id and held.get("trade") == trade_id and not held.get("adopted"):
                return held
        return None

    async def _adopt(self, placement: ops.Placement) -> bool:
        """A placement an accept created (W11.23), naming a venue order whose
        fill was reported on no placement: its trade's financing charged
        again on it, and a catch-up roll where OANDA rolled the trade before
        the accept (W11.25). True where the placement is one."""
        ids = list(
            dict.fromkeys(
                i for i in (placement.venue_order_id, *placement.venue_order_ids) if i
            )
        )
        named = [i for i in ids if i in self._originals]
        if not named:
            return False
        connection = self._connection()
        if connection is None:
            return True
        pid = placement.placement_id
        ids_by_key: dict[str, str] = self._rolling.value.setdefault("charge_ids", {})
        groups: dict[tuple[str, str], tuple[str, list[str]]] = {}
        for order_id in named:
            held = self._originals[order_id]
            if held.get("adopted"):
                continue
            held["adopted"] = pid
            if held.get("trade"):
                self._trades.value.setdefault("trades", {})[held["trade"]] = pid
            again: list[str] = []
            for charge in held.get("charges") or []:
                done = await self._plugin.report_charge(
                    placement_id=pid,
                    fee=meridian.Fee(
                        kind=ops.FEE_KIND_FINANCING,
                        amount=meridian.Money(Decimal(charge["amount"]), charge["currency"]),
                    ),
                    trade_date=charge["day"],
                    settlement_date=charge["day"],
                    raw_record=self._plugin.raw_record(charge["raw"]),
                )
                charge_id = _charge_id(done)
                if charge_id:
                    again.append(charge_id)
                    ids_by_key[charge["key"]] = charge_id
            pair_key = self._booked_pair(
                held["code"], held["pair"], held["base"], held["quote"]
            )
            group = (pair_key, str(held["value_date"]))
            first, collected = groups.get(group, (str(held["txn"]), []))
            if _txn(str(held["txn"])) < _txn(first):
                first = str(held["txn"])
            groups[group] = (first, collected + again)
            log.info("%s: OANDA's order %s accepted to %s", connection.code, order_id, pid)
        self._trades.save()
        self._rolling.save()
        for (pair_key, value_date), (first, charge_ids) in groups.items():
            await self._catch_up(
                pair_key, value_date, first, charge_ids, f"catch-up:{pid}:{value_date}"
            )
        return True

    # ── Financing, and the value-date roll (contract v23) ───────────────

    async def _financing(self, connection: Connection, financing: Json) -> None:
        """OANDA's roll: each open trade's financing charged, then a
        value-date roll for each pair it rolled (W11.25)."""
        at_ns = moments.ns(financing.get("time")) or moments.of(self._now())
        transaction_id = str(financing.get("id") or "")
        raw = self._keep(connection, [call(STREAMED, financing, transaction_id)], "financing")
        rolls: dict[str, Json] = self._rolling.value.setdefault("rolls", {})
        rolled: list[tuple[str, list[str]]] = []
        for position in financing.get("positionFinancings") or []:
            if not isinstance(position, dict) or not position.get("instrument"):
                continue
            charge_ids: list[str] = []
            for each in position.get("openTradeFinancings") or []:
                if not isinstance(each, dict):
                    continue
                amount = _number(each.get("financing"))
                if amount:
                    charge_id = await self._financed(
                        connection,
                        transaction_id,
                        str(each.get("tradeID") or ""),
                        amount,
                        at_ns,
                        raw,
                    )
                    if charge_id:
                        charge_ids.append(charge_id)
            pair_key = _pair_key(connection.code, str(position["instrument"]))
            rolls[pair_key] = {"txn": transaction_id, "at_ns": at_ns, "raw": raw.key}
            rolled.append((pair_key, charge_ids))
        self._rolling.save()
        for pair_key, charge_ids in rolled:
            await self._roll(
                {
                    "pair_key": pair_key,
                    "txn": transaction_id,
                    "at_ns": at_ns,
                    "charge_ids": charge_ids,
                    "raw": raw.key,
                }
            )

    async def _financed(
        self,
        connection: Connection,
        transaction_id: str,
        trade_id: str,
        financing: Decimal,
        at_ns: int,
        raw: ops.RawRecordRef,
    ) -> str:
        """A trade's financing, once: a charge of kind financing on the
        placement whose fill opened it, its `CHG-` ID returned; on no
        placement for a trade a venue-originated fill opened, kept
        unplaced until an accept (W11.12); held while the trade's opening
        fill waits for its value date; none for a trade no fill it reported
        opened (the account's history). OANDA's amount signed as a fee is:
        paid positive."""
        key = f"{transaction_id}:{trade_id}"
        charged: list[str] = self._trades.value.setdefault("charged", [])
        ids_by_key: dict[str, str] = self._rolling.value.setdefault("charge_ids", {})
        if key in charged:
            return str(ids_by_key.get(key, ""))
        day = moments.trading_day(at_ns).isoformat()
        fee = meridian.Fee(
            kind=ops.FEE_KIND_FINANCING, amount=meridian.Money(-financing, self.home)
        )
        placement_id = (self._trades.value.get("trades") or {}).get(trade_id)
        if placement_id:
            done = await self._plugin.report_charge(
                placement_id=placement_id,
                fee=fee,
                trade_date=day,
                settlement_date=day,
                raw_record=raw,
            )
            charge_id = _charge_id(done)
            charged.append(key)
            if charge_id:
                ids_by_key[key] = charge_id
            self._trades.save()
            self._rolling.save()
            return charge_id
        held = self._originated_trade(trade_id)
        if held is not None:
            await self._plugin.report_charge(
                fee=fee, trade_date=day, settlement_date=day, raw_record=raw
            )
            held.setdefault("charges", []).append(
                {
                    "key": key,
                    "amount": f"{-financing:f}",
                    "currency": self.home,
                    "day": day,
                    "raw": raw.key,
                }
            )
            charged.append(key)
            self._trades.save()
            self._rolling.save()
            return ""
        if trade_id and any(_opened(fill) == trade_id for fill in self._waiting.value):
            waits: list[Json] = self._rolling.value.setdefault("held_charges", {}).setdefault(
                trade_id, []
            )
            if all(each.get("key") != key for each in waits):
                waits.append(
                    {
                        "key": key,
                        "txn": transaction_id,
                        "amount": f"{financing:f}",
                        "at_ns": at_ns,
                        "raw": raw.key,
                    }
                )
                self._rolling.save()
        return ""

    async def _charge_held(self, connection: Connection, trade_id: str) -> list[str]:
        """The financing held for a trade while its opening fill waited,
        charged now it is reported; the `CHG-` IDs."""
        waits = self._rolling.value.get("held_charges", {}).pop(trade_id, [])
        if not waits:
            return []
        self._rolling.save()
        made = []
        for each in waits:
            charge_id = await self._financed(
                connection,
                str(each["txn"]),
                trade_id,
                Decimal(each["amount"]),
                int(each["at_ns"]),
                self._plugin.raw_record(each["raw"]),
            )
            if charge_id:
                made.append(charge_id)
        return made

    def _booked_pair(self, code: str, pair: str, base: str, quote: str) -> str:
        """The pair's entry in the book's pending dates, made where new."""
        key = _pair_key(code, pair)
        pairs: dict[str, Json] = self._rolling.value.setdefault("pairs", {})
        held = pairs.setdefault(key, {"dates": []})
        held.update(code=code, pair=pair, base=base, quote=quote)
        self._rolling.save()
        return key

    async def _catch_up(
        self,
        pair_key: str,
        from_date: str,
        fill_transaction: str,
        charge_ids: list[str],
        key: str,
    ) -> None:
        """Lines a fill booked at `from_date`: where OANDA's latest roll of
        the pair followed the fill, a catch-up roll from that date to the
        date read's answer for the roll's moment (W11.25, approved
        2026-10-13); otherwise the date is held as the pair's."""
        roll = (self._rolling.value.get("rolls") or {}).get(pair_key)
        if roll is None or _txn(str(roll["txn"])) <= _txn(fill_transaction):
            held = self._rolling.value["pairs"][pair_key]
            held["dates"] = sorted(set(held["dates"]) | {from_date})
            self._rolling.save()
            return
        await self._roll(
            {
                "pair_key": pair_key,
                "txn": roll["txn"],
                "at_ns": roll["at_ns"],
                "charge_ids": charge_ids,
                "raw": roll["raw"],
                "from": from_date,
                "key": key,
            }
        )

    async def _roll(self, entry: Json, retried: bool = False) -> bool:
        """One value-date roll (W11.25): the pending lines this instance's
        fills and rolls left on each earlier date in the pair, moved to the
        date read's answer for the roll's moment, never a date worked out
        here. A catch-up names its own from date. Kept waiting, in order,
        while the date read answers none; True where done."""
        waiting: list[Json] = self._rolling.value.setdefault("waiting_rolls", [])
        held = (self._rolling.value.get("pairs") or {}).get(entry["pair_key"])
        catching_up = bool(entry.get("from"))
        if held is None or not (catching_up or held.get("dates")):
            return True
        if waiting and not retried:
            # An earlier roll waits for its date: this one follows it.
            waiting.append(entry)
            self._rolling.save()
            return False
        at_ns = int(entry["at_ns"])
        answer = await self._value_date(str(held["base"]), str(held["quote"]), at_ns)
        if answer is None:
            if not retried:
                waiting.append(entry)
                self._rolling.save()
            self._once(
                f"roll:{entry['txn']}:{entry['pair_key']}:{entry.get('from', '')}",
                f"{held['code']}: no value date yet for OANDA's roll of {held['pair']} at "
                f"{moments.rfc3339(at_ns)} ({self._last_outcome}); asked again each poll, "
                "never guessed",
            )
            return False
        to = answer.date
        dates = set(held["dates"])
        if catching_up:
            froms = [str(entry["from"])] if str(entry["from"]) < to else []
            dates.add(to if froms else str(entry["from"]))
        else:
            # Lines on a date the roll's trading day has reached are not
            # pending any more: OANDA held no trade there to roll.
            day = moments.trading_day(at_ns).isoformat()
            froms = sorted(d for d in dates if day < d < to)
            dates = {d for d in dates if d >= to} | ({to} if froms else set())
        for n, from_date in enumerate(froms):
            await self._plugin.report_value_date_roll(
                venue_account_code=str(held["code"]),
                pair=[str(held["base"]), str(held["quote"])],
                from_value_date=from_date,
                to_value_date=to,
                value_date_basis=basis_of(answer.basis),
                rolled_at_ns=at_ns,
                charge_ids=list(entry["charge_ids"]) if n == len(froms) - 1 else [],
                raw_record=self._plugin.raw_record(str(entry["raw"])),
                idempotency_key=str(
                    entry.get("key") or f"roll:{held['code']}:{entry['txn']}:{held['pair']}"
                )
                + f":{from_date}",
            )
        held["dates"] = sorted(dates)
        self._rolling.save()
        return True

    async def _retry_rolls(self) -> None:
        waiting = list(self._rolling.value.get("waiting_rolls") or [])
        if not waiting:
            return
        kept: list[Json] = []
        for entry in waiting:
            if kept or not await self._roll(entry, retried=True):
                kept.append(entry)
        self._rolling.value["waiting_rolls"] = kept
        self._rolling.save()

    async def _retry_waiting(self, connection: Connection) -> None:
        held = list(self._waiting.value)
        if not held:
            return
        kept: list[Json] = []
        for fill in held:
            worked = self._by_order(
                str(fill.get("orderID") or ""), str(fill.get("clientOrderID") or "")
            )
            if worked is None:
                client = str(fill.get("clientOrderID") or "")
                mine = bool(client) and placement_of_client(client) in self._sent
                if mine or not await self._originated(connection, fill, retried=True):
                    kept.append(fill)
                continue
            if not await self._report_fill(connection, worked, fill, retried=True):
                kept.append(fill)
        self._waiting.value = kept
        self._waiting.save()

    async def _cancelled(self, connection: Connection, cancel: Json) -> None:
        worked = self._by_order(str(cancel.get("orderID") or ""), "")
        if worked is None or worked.ended:
            return
        reason = str(cancel.get("reason") or "")
        if reason == REPLACED_REASON:
            return
        if str(cancel.get("orderID")) != worked.oanda_order_id:
            return
        raw = self._keep(
            connection, [call(STREAMED, cancel, str(cancel.get("id") or ""))], "cancel"
        )
        expired = reason in EXPIRED_REASONS
        worked.ended = "expired" if expired else "cancelled"
        await self._plugin.report_placement(
            placement_id=worked.placement_id,
            event=ops.PLACEMENT_EVENT_EXPIRED if expired else ops.PLACEMENT_EVENT_CANCELLED,
            raw_record=raw,
        )

    # ── The catalogue ───────────────────────────────────────────────────

    def catalogue(self, connection: Connection) -> tuple[tuple[int, tuple[int, ...]], ...]:
        """What OANDA's order endpoint takes on the account: none while the
        connection is read-only."""
        if not connection.trading:
            return ()
        return tuple((kind, OFFERED_TIMES[kind]) for kind in ORDER_TYPES)

    async def publish(self) -> None:
        """The account's catalogue, where it changed since last published."""
        connection = self._connection()
        if connection is None:
            return
        offered = self.catalogue(connection)
        if self.published.get(connection.code) == offered:
            return
        stated_at = moments.of(self._now())
        await self._plugin.report_venue_catalogue(
            catalogue=meridian.VenueCatalogue(
                venue_account_code=connection.code,
                order_types=[
                    meridian.VenueOrderType(
                        order_type=ops.OrderType.Name(kind),
                        times_in_force=[ops.TimeInForce.Name(t) for t in tifs],
                        stated_at_ns=stated_at,
                    )
                    for kind, tifs in offered
                ],
                execution_acknowledgement=ops.EXECUTION_ACKNOWLEDGEMENT_NONE,
            )
        )
        self.published[connection.code] = offered

    # ── Raw records ─────────────────────────────────────────────────────

    def _keep(self, connection: Connection, calls: list[Json], what: str) -> ops.RawRecordRef:
        key = ""
        if self._raw is not None:
            key = self._raw.keep_trading(
                connection.account_id,
                self._now(),
                calls,
                connection.synthetic,
                self._secrets(),
            )
        reference: ops.RawRecordRef = self._plugin.raw_record(
            f"trading/{connection.account_id}/{key or 'unkept'}/{what}"
        )
        return reference

    def _once(self, key: str, said: str) -> None:
        if key not in self._said:
            self._said.add(key)
            log.info("%s", said)

    # ── Running ─────────────────────────────────────────────────────────

    async def follow(self) -> None:
        """The account's transaction stream: each transaction a cue to read
        the transactions since the last taken, so stream and poll take the
        same ones in order; reconnected after a pause when it ends."""
        while True:
            connection = self._connection()
            if connection is None:
                await asyncio.sleep(1)
                continue
            try:
                async for heard in connection.venue.stream(connection.account_id):
                    if heard.get("type") == "HEARTBEAT":
                        continue
                    async with self._lock:
                        await self._take_transactions()
                    if self._connection() != connection:
                        break
            except VenueError as failure:
                self.shown.error = str(failure)
                log.warning("the transaction stream ended: %s", failure)
            except meridian.MeridianError as failure:
                log.warning("a streamed transaction was not taken: %s", failure)
            await asyncio.sleep(2)

    async def run(self, poll_seconds: Callable[[], int]) -> None:
        """Start, hear placements and instructions, follow the stream and
        poll until cancelled."""
        while self._connection() is None:
            await asyncio.sleep(1)
        await self.start()
        hearing = asyncio.create_task(
            self._plugin.receive(
                placement_updated=self.on_placement,
                trading_instruction_changed=self.on_instruction,
                seed=False,
            )
        )
        following = asyncio.create_task(self.follow())
        try:
            while True:
                try:
                    await self.poll()
                    await self.publish()
                except meridian.MeridianError as failure:
                    self.shown.error = str(failure)
                    log.warning("this poll stopped: %s", failure)
                await asyncio.sleep(poll_seconds())
        finally:
            for task in (hearing, following):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


def _note(body: Json) -> str:
    from .raw import dumps

    return dumps(body)
