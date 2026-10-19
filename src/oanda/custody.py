"""OANDA as a custody plugin (W2): the account's sync status, its statement
and its activity, as the custodian's view, through the SDK's typed
operations.

**The account.** The v20 account the connection names (connection.py: the
`account_id` setting where given, else the one an admin chose on
Connections) is the external account it reports (W2.8), by OANDA's account
ID; until there is one, nothing is read. Its kind is
converted from what OANDA states, its margin rate: below 1 a margin
account; at 1, or unstated, not known, OANDA's margin rate beside it as
reported (OANDA states no account type).

**The statement: rolling spot as two cash legs** (ruled 2026-10-13, the
spot-FX plugin's Q3). At a US retail FX dealer a spot position never
settles: it rolls at each 17:00 New York, and OANDA describes its financing
as the cost of moving the settlement date forward a day. So each open trade
is two cash legs: its units of the base currency, and the units times its
price, negated, of the quote currency. Each leg is pending to the value date
the position now rolls to: the pair's spot date for the read's moment, from
the date read (`ComputeDate` with the pair's two cash instruments), never
worked out here, its provenance the rule it was derived by. Where the date
read answers none, the row states no split at all -- its settled quantity
unset and nothing pending -- since the street takes no pending quantity
without its date and none is guessed. The legs of every trade are summed
by currency and value date, so a currency has one row, signed (cash is one
long position, signed, as the book holds cash). The account's currency
carries the account's balance as OANDA states it, settled, beside its legs;
any other currency's settled quantity is 0, closed by the rolling rule with
its provenance. The legs net to the open profit or loss, so valuation never
counts a notional as holdings. A row's market value is left unset: OANDA
states none per currency.

The statement's figures are the account's own, as OANDA states them:
`NAV` as its net liquidation and `marginUsed` as its margin requirement, in
the account's currency. OANDA names no institution; it is the connection's,
OANDA, with its provenance. The statement's ID is the account and its last
transaction ID, so a read that found nothing new opens no second statement.

**Activity** (contract v14, W2.10): each of the account's transactions that
moved its cash, as OANDA states it, the backfill going back to the account's
opening on the first read in the process:

- ORDER_FILL: a purchase (units bought) or a sale of the base currency's
  cash instrument, its price in the quote currency, the cash paid or
  received in the quote currency; a commission or guaranteed-execution fee,
  its own fee; a realised profit in a quote currency other than the
  account's, which OANDA converts to the account's, its own journal (the
  ruling's "its own conversion"): the quote currency out, the account's in;
  a closed trade's financing, its own interest;
- DAILY_FINANCING: interest, signed as OANDA signs it, each roll's financing
  its own record (decisions/031);
- TRANSFER_FUNDS: by its `fundingReason`, CLIENT_FUNDING a contribution (in)
  or withdrawal (out), ACCOUNT_TRANSFER a transfer in or out; any other
  reason, as any other transaction moving cash, not known, as reported.

Its first date is the account's opening (`history_from`).

**Sync** (W2.1): current where OANDA answered; stale where it did not
answer and the last good read is older than `stale_after_hours`; needs
sign-in where OANDA refused the token (HTTP 401): a person makes a new one
in OANDA's hub.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import meridian
from meridian.bounds import PLUGIN_FIGURE_WHY_LENGTH
from meridian.edge import as_reported, derived
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import custody as custody_role

from . import moments
from .connection import Connection, Connections
from .declared import seen_in_account, seen_in_fill, seen_in_financing
from .pairs import PairMap
from .raw import ACCOUNT, INSTRUMENTS, TRANSACTIONS, RawStore, call, failed
from .venue import Json, VenueError, as_json

log = logging.getLogger("oanda")

SOURCE = "oanda"
INSTITUTION = "OANDA"
MARGIN_RATE_SCHEME = "oanda:margin-rate"
TRANSACTION_SCHEME = "oanda:transaction-type"
INSTRUMENT_TYPE_SCHEME = "oanda:instrument-type"
CURRENCY_SCHEME = "oanda:currency"

ROLLING = (
    "an open OANDA position rolls at each 17:00 New York and never settles: its legs are "
    "pending to the value date it rolls to"
)
VALUE_DATE = "the pair's spot date for the read's moment, from the date read (ComputeDate)"
NO_INSTITUTION = "OANDA's v20 account names no institution: the connection's, OANDA"


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def account_kind(stated: Json) -> tuple[str, ops.AsReported | None]:
    """The account's kind, from the margin rate OANDA states."""
    rate = _number(stated.get("marginRate"))
    if rate is not None and Decimal(0) < rate < Decimal(1):
        return "ACCOUNT_KIND_MARGIN", None
    code = str(stated.get("marginRate") or "")
    text = "margin rate 1: no leverage" if rate == 1 else f"margin rate {code or 'not stated'}"
    return "ACCOUNT_KIND_UNSPECIFIED", as_reported(MARGIN_RATE_SCHEME, code or "none", text)


@dataclass
class Leg:
    """A currency's quantity on one value date ("" where none was worked
    out), and how the date was."""

    currency: str
    value_date: str
    quantity: Decimal
    why: str = VALUE_DATE


@dataclass
class Row:
    """One row of a statement: a currency, or a metal, and its legs."""

    code: str
    kind: str
    settled: Decimal
    pending: list[Leg] = field(default_factory=list)
    stated_settled: bool = False

    @property
    def quantity(self) -> Decimal:
        return self.settled + sum((leg.quantity for leg in self.pending), Decimal(0))


@dataclass(frozen=True)
class Converted:
    """One activity, converted from OANDA's transaction."""

    external_activity_id: str
    kind: str
    trade_date: str
    transaction_id: str
    code: str = ""
    # OANDA's instrument the activity was in, where it was in one.
    instrument: str = ""
    units: Decimal | None = None
    price: meridian.Money | None = None
    amount: meridian.Money | None = None
    kind_as_reported: ops.AsReported | None = None
    description: str = ""


def activities_of(transaction: Json, home: str) -> list[Converted]:
    """The activities one transaction states: none for one that moved no
    cash."""
    kind = str(transaction.get("type") or "")
    tid = str(transaction.get("id") or "")
    day = moments.trading_day(moments.ns(transaction.get("time"))).isoformat()
    made: list[Converted] = []
    if kind == "ORDER_FILL":
        instrument = str(transaction.get("instrument") or "")
        base, _, quote = instrument.partition("_")
        units = _number(transaction.get("units")) or Decimal(0)
        price = _number(transaction.get("fullVWAP")) or _number(transaction.get("price"))
        if units and price is not None:
            verb = "Bought" if units > 0 else "Sold"
            made.append(
                Converted(
                    tid,
                    "ACTIVITY_KIND_PURCHASE" if units > 0 else "ACTIVITY_KIND_SALE",
                    day,
                    tid,
                    code=base,
                    instrument=instrument,
                    units=units,
                    price=meridian.Money(price, quote),
                    amount=meridian.Money(-units * price, quote),
                    description=f"{verb} {abs(units):f} {instrument}",
                )
            )
        for name, said in (
            ("commission", "Commission"),
            ("guaranteedExecutionFee", "Guaranteed execution fee"),
        ):
            fee = _number(transaction.get(name))
            if fee:
                made.append(
                    Converted(
                        f"{tid}:{name}",
                        "ACTIVITY_KIND_FEE",
                        day,
                        tid,
                        amount=meridian.Money(-fee, home),
                        description=f"{said} on {instrument}",
                    )
                )
        quote_pl = _number(transaction.get("quotePL"))
        pl = _number(transaction.get("pl"))
        if quote and quote != home and quote_pl and pl is not None:
            made.append(
                Converted(
                    f"{tid}:conversion",
                    "ACTIVITY_KIND_JOURNAL",
                    day,
                    tid,
                    code=quote,
                    units=-quote_pl,
                    amount=meridian.Money(pl, home),
                    description=f"Realised {quote} profit converted to {home}",
                )
            )
        financing = _number(transaction.get("financing"))
        if financing:
            made.append(
                Converted(
                    f"{tid}:financing",
                    "ACTIVITY_KIND_INTEREST",
                    day,
                    tid,
                    amount=meridian.Money(financing, home),
                    description=f"Financing settled on closing {instrument}",
                )
            )
        return made
    if kind == "DAILY_FINANCING":
        financing = _number(transaction.get("financing"))
        if financing:
            made.append(
                Converted(
                    tid,
                    "ACTIVITY_KIND_INTEREST",
                    day,
                    tid,
                    amount=meridian.Money(financing, home),
                    description="Daily financing at the 17:00 New York roll",
                )
            )
        return made
    if kind == "TRANSFER_FUNDS":
        amount = _number(transaction.get("amount"))
        reason = str(transaction.get("fundingReason") or "")
        if not amount:
            return made
        converted = {
            ("CLIENT_FUNDING", True): "ACTIVITY_KIND_CONTRIBUTION",
            ("CLIENT_FUNDING", False): "ACTIVITY_KIND_WITHDRAWAL",
            ("ACCOUNT_TRANSFER", True): "ACTIVITY_KIND_TRANSFER_IN",
            ("ACCOUNT_TRANSFER", False): "ACTIVITY_KIND_TRANSFER_OUT",
        }.get((reason, amount > 0))
        if converted is None:
            made.append(
                Converted(
                    tid,
                    "ACTIVITY_KIND_UNSPECIFIED",
                    day,
                    tid,
                    amount=meridian.Money(amount, home),
                    kind_as_reported=as_reported(
                        TRANSACTION_SCHEME,
                        f"TRANSFER_FUNDS/{reason}",
                        f"Funds transferred: {reason or 'no reason stated'}",
                    ),
                    description=f"Funds transferred ({reason})",
                )
            )
        else:
            made.append(
                Converted(
                    tid,
                    converted,
                    day,
                    tid,
                    amount=meridian.Money(amount, home),
                    description=f"Funds transferred ({reason})",
                )
            )
        return made
    # Any other transaction that moved the account's cash, as OANDA states it.
    moved = _number(transaction.get("amount")) or _number(transaction.get("financing"))
    if moved and kind not in ("DAILY_FINANCING",):
        made.append(
            Converted(
                tid,
                "ACTIVITY_KIND_UNSPECIFIED",
                day,
                tid,
                amount=meridian.Money(moved, home),
                kind_as_reported=as_reported(TRANSACTION_SCHEME, kind, kind),
                description=kind,
            )
        )
    return made


@dataclass
class Status:
    """What the last read found, for the pages and the Summary: counts,
    states and the statement's rows, never a credential."""

    read_at: datetime | None = None
    last_good: datetime | None = None
    error: str = ""
    state: str = ""
    account: Json = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)
    as_of: str = ""
    statement_id: str = ""
    activities_sent: int = 0
    linked: bool = False
    waiting_for: tuple[str, ...] = ()


class Syncer:
    """One read of the account at a time, and what it found."""

    def __init__(
        self,
        plugin: Any,
        connections: Connections,
        *,
        now: Callable[[], datetime],
        raw: RawStore | None,
        pairs: PairMap,
        linked: Callable[[str], bool],
    ) -> None:
        self._plugin = plugin
        self.connections = connections
        self.now = now
        self.raw = raw
        self._pairs = pairs
        self._linked = linked
        self.status = Status()
        # Another role's figures, after custody's on the Summary (the data
        # role's, from 0.5.0), where the plugin holds it.
        self.more_figures: Callable[[], list[meridian.Figure]] | None = None
        # The transactions taken for activity in this process, by account:
        # from 0 (the backfill) on the first read.
        self._since: dict[str, str] = {}
        self._cash: dict[tuple[str, bool], str] = {}
        self._said: set[str] = set()

    async def run_once(self) -> Status:
        connection = self.connections.current
        if connection is None:
            self.status = Status(waiting_for=self.connections.config.missing)
            await self._health(
                healthy=False, detail=self.connections.waiting(), figures=self.figures()
            )
            return self.status
        now = self.now()
        calls: list[Json] = []
        try:
            held = await connection.venue.account(connection.account_id)
            calls.append(call(ACCOUNT, held))
            instruments = await connection.venue.instruments(connection.account_id)
            calls.append(call(INSTRUMENTS, instruments))
            self._pairs.take(instruments)
            since = self._since.get(connection.account_id, "0")
            transactions, last = await connection.venue.transactions_since(
                connection.account_id, since
            )
            calls.append(
                call(
                    TRANSACTIONS,
                    {"transactions": transactions, "lastTransactionID": last},
                    f"since {since}",
                )
            )
        except VenueError as failure:
            calls.append(failed(ACCOUNT, str(failure), failure.said or None))
            self._keep_read(connection, now, calls)
            await self._failed(connection, failure, now)
            return self.status
        key = self._keep_read(connection, now, calls)
        account = as_json(held.get("account"))
        for scheme, name in seen_in_account(account):
            self._plugin.note_not_carried(scheme, name)
        home = str(account.get("currency") or "USD")
        self.status = Status(
            read_at=now,
            last_good=now,
            state="SYNC_STATE_CURRENT",
            account=account,
            linked=self._linked(connection.account_id),
        )
        await self._report_account(connection, account)
        await self._report_sync(connection, account, now, "SYNC_STATE_CURRENT", "")
        if not self.status.linked:
            await self._health(
                healthy=True, detail="read; the account is not linked", figures=self.figures()
            )
            return self.status
        rows = await self._rows(account, home, now)
        self.status.rows = rows
        self.status.as_of = moments.trading_day(moments.of(now)).isoformat()
        await self._record(connection, account, home, rows, now, key)
        sent = await self._activities(connection, transactions, home, now)
        self.status.activities_sent = sent
        self._since[connection.account_id] = str(last or since)
        await self._health(healthy=True, detail="read", figures=self.figures())
        return self.status

    async def _health(
        self, *, healthy: bool, detail: str, figures: list[meridian.Figure]
    ) -> None:
        """The plugin's health and figures on the heartbeat (`plugin.report`);
        a stand-in for the sidecar that keeps no heartbeat is told nothing."""
        report = getattr(self._plugin, "report", None)
        if report is not None:
            await report(healthy=healthy, detail=detail, figures=figures)

    # ── What was read ───────────────────────────────────────────────────

    def _keep_read(self, connection: Connection, now: datetime, calls: list[Json]) -> str:
        if self.raw is None:
            return "unkept"
        return self.raw.keep_read(
            connection.account_id,
            now,
            calls,
            connection.synthetic,
            self.connections.config.secrets(),
        )

    async def _failed(self, connection: Connection, failure: VenueError, now: datetime) -> None:
        last = self.status.last_good
        if failure.unauthorised:
            state = "SYNC_STATE_NEEDS_SIGN_IN"
            detail = (
                "OANDA refused the access token (HTTP 401): make a new one in OANDA's hub "
                "and enter it in Settings"
            )
        elif last is not None and now - last < self.connections.config.stale_after:
            state = "SYNC_STATE_CURRENT"
            detail = f"OANDA did not answer this read ({failure}); the last read is recent"
        else:
            state = "SYNC_STATE_STALE"
            detail = (
                f"OANDA did not answer ({failure}): what is held is the last good read, older "
                "than Stale after allows; it recovers when OANDA answers"
            )
        self.status = Status(
            read_at=now,
            last_good=last,
            error=str(failure),
            state=state,
            account=self.status.account,
            rows=self.status.rows,
            as_of=self.status.as_of,
            linked=self.status.linked,
        )
        await self._report_sync(
            connection, self.status.account, now, state, detail, synced=last
        )
        await self._health(healthy=False, detail=detail, figures=self.figures())

    async def _report_account(self, connection: Connection, account: Json) -> None:
        kind, said = account_kind(account)
        await self._plugin.report_external_accounts(
            accounts=[
                meridian.ExternalAccount(
                    external_account_id=connection.account_id,
                    name=str(account.get("alias") or connection.account_id),
                    account_kind=kind,
                    account_kind_as_reported=said,
                )
            ]
        )

    async def _report_sync(
        self,
        connection: Connection,
        account: Json,
        now: datetime,
        state: str,
        detail: str,
        synced: datetime | None = None,
    ) -> None:
        synced = synced if synced is not None else now
        opened = moments.ns(account.get("createdTime"))
        await self._plugin.report_sync_status(
            source=SOURCE,
            external_account_id=connection.account_id,
            state=state,
            connection_healthy=state == "SYNC_STATE_CURRENT",
            status_detail=detail,
            last_synced_at_ns=moments.of(synced),
            holdings_as_of_ns=moments.of(synced),
            history_as_of_ns=moments.of(synced),
            observed_at_ns=moments.of(now),
            # The first date OANDA's history of the account reaches: its
            # opening.
            history_from=moments.trading_day(opened).isoformat() if opened else "",
        )

    # ── The statement ───────────────────────────────────────────────────

    async def cash(self, code: str, cash: bool = True) -> str:
        """The deployment's record for a currency (or, `cash` false, a metal
        OANDA names by its ISO 4217 code): "" where it holds none, or more
        than one."""
        if (code, cash) not in self._cash:
            found = await self._plugin.resolve_identifier(
                identifiers=[meridian.Identifier(scheme="iso4217", value=code)],
                stated_asset_class="cash" if cash else None,
                stated_currency=code if cash else "",
            )
            if not found.instrument_id:
                return ""
            self._cash[(code, cash)] = str(found.instrument_id)
        return self._cash[(code, cash)]

    async def _value_date(self, base: str, quote: str, now: datetime) -> tuple[str, str]:
        """The pair's spot date for now, from the date read, and how."""
        base_id, quote_id = await self.cash(base), await self.cash(quote)
        if not base_id or not quote_id:
            return (
                "",
                f"no value date: the deployment holds no cash instrument for {base}/{quote}",
            )
        at_ns = moments.of(now)
        answer = await self._plugin.compute_date(
            spot=ops.SpotDateInputs(pair=[base_id, quote_id], at_ns=at_ns), as_of_ns=at_ns
        )
        if answer.outcome == ops.DATE_OUTCOME_COMPUTED and answer.date:
            rule = answer.basis.rule
            return str(answer.date), (
                f"{VALUE_DATE}: rule {rule.subject or base + '/' + quote} v{rule.version}"
            )
        return "", (
            f"no value date: the date read answered {ops.DateOutcome.Name(answer.outcome)} "
            f"for {base}/{quote}"
        )

    async def _rows(self, account: Json, home: str, now: datetime) -> list[Row]:
        balance = _number(account.get("balance")) or Decimal(0)
        rows: dict[str, Row] = {home: Row(home, "CURRENCY", balance, stated_settled=True)}
        dates: dict[str, tuple[str, str]] = {}
        for trade in account.get("trades") or []:
            if not isinstance(trade, dict):
                continue
            instrument = str(trade.get("instrument") or "")
            base, _, quote = instrument.partition("_")
            units = _number(trade.get("currentUnits"))
            price = _number(trade.get("price"))
            if not base or not quote or units is None or price is None:
                continue
            pair = self._pairs.get(instrument)
            kind = pair.kind if pair is not None else "CURRENCY"
            if instrument not in dates:
                dates[instrument] = (
                    await self._value_date(base, quote, now)
                    if kind == "CURRENCY"
                    else (
                        "",
                        f"no value date: OANDA lists {instrument} as {kind}, which no "
                        "spot-date rule covers",
                    )
                )
            value_date, why = dates[instrument]
            for code, quantity, its_kind in (
                (base, units, kind),
                (quote, -units * price, "CURRENCY"),
            ):
                row = rows.setdefault(code, Row(code, its_kind, Decimal(0)))
                leg = next((g for g in row.pending if g.value_date == value_date), None)
                if leg is None:
                    row.pending.append(Leg(code, value_date, quantity, why))
                else:
                    leg.quantity += quantity
        return [rows[code] for code in sorted(rows, key=lambda c: (c != home, c))]

    async def _resolve(self, row: Row, as_of_ns: int, now: datetime) -> dict[str, Any]:
        identifiers = [meridian.Identifier(scheme="iso4217", value=row.code)]
        cash = row.kind == "CURRENCY"
        answer = await self._plugin.resolve_identifier(
            identifiers=identifiers,
            as_of_ns=as_of_ns,
            currency=row.code if cash else "",
            stated_asset_class="cash" if cash else None,
            stated_currency=row.code if cash else "",
        )
        if answer.instrument_id:
            return {"instrument_id": answer.instrument_id}
        await self._plugin.report_missing_instrument(
            source=SOURCE,
            asset_class="cash" if cash else None,
            asset_class_as_reported=(
                None if cash else as_reported(INSTRUMENT_TYPE_SCHEME, row.kind, row.kind)
            ),
            identifiers=identifiers,
            as_of_ns=as_of_ns,
            reason=answer.miss_reason or ops.MISS_REASON_NOT_FOUND,
            observed_at_ns=moments.of(now),
        )
        return {"unresolved_identifiers": identifiers}

    async def _record(
        self,
        connection: Connection,
        account: Json,
        home: str,
        rows: list[Row],
        now: datetime,
        key: str,
    ) -> None:
        as_of_ns = moments.of(now)
        statement_id = f"{connection.account_id}:{account.get('lastTransactionID', '')}"
        try:
            instruments = [await self._resolve(row, as_of_ns, now) for row in rows]
            figures = []
            nav = _number(account.get("NAV"))
            used = _number(account.get("marginUsed"))
            if nav is not None or used is not None:
                figures.append(
                    meridian.StatementFigures(
                        segment="",
                        net_liquidation=None if nav is None else meridian.Money(nav, home),
                        margin_requirement=None if used is None else meridian.Money(used, home),
                    )
                )
            opened = await self._plugin.record_holdings_statement(
                source=SOURCE,
                external_statement_id=statement_id,
                external_account_id=connection.account_id,
                institution=INSTITUTION,
                as_of_date=self.status.as_of,
                read_at_ns=as_of_ns,
                expected_rows=len(rows),
                figures=figures,
                raw_record=self._plugin.raw_record(f"{connection.account_id}/{key}/account"),
                provenance=[derived("institution", NO_INSTITUTION)],
            )
            self.status.statement_id = opened.statement_id
            if opened.already_recorded:
                return
            for row, instrument in zip(rows, instruments, strict=True):
                await self._plugin.record_holding(
                    statement_id=opened.statement_id,
                    external_account_id=connection.account_id,
                    side="HOLDING_SIDE_LONG",
                    quantity=row.quantity,
                    raw_record=self._plugin.raw_record(
                        f"{connection.account_id}/{key}/account"
                    ),
                    **self._split(row),
                    **instrument,
                )
        except meridian.MeridianError as refused:
            self.status.error = str(refused)
            log.warning("the statement for %s stopped: %s", connection.account_id, refused)

    def _split(self, row: Row) -> dict[str, Any]:
        """A row's settled quantity and what is pending by value date, each
        closed by its rule with its provenance. Where a leg's value date was
        not worked out, the split is not stated at all -- the settled
        quantity unset, nothing pending -- since the street takes no pending
        quantity without its date and none is guessed; the reason is said
        once in the log."""
        undated = [leg for leg in row.pending if not leg.value_date]
        if undated:
            said = f"{row.code}: settled and pending not stated: {undated[0].why}"
            if said not in self._said:
                self._said.add(said)
                log.info("%s", said)
            return {}
        provenance = [] if row.stated_settled else [derived("settle_date_quantity", ROLLING)]
        provenance += [
            derived(f"pending[{i}].value_date", leg.why) for i, leg in enumerate(row.pending)
        ]
        return {
            "settle_date_quantity": row.settled,
            "pending": [
                meridian.ReportedPending(value_date=leg.value_date, quantity=leg.quantity)
                for leg in row.pending
            ],
            "provenance": provenance,
        }

    # ── Activity ────────────────────────────────────────────────────────

    async def _activities(
        self, connection: Connection, transactions: list[Json], home: str, now: datetime
    ) -> int:
        sent = 0
        for transaction in transactions:
            converted = activities_of(transaction, home)
            if not converted:
                continue
            kind = str(transaction.get("type") or "")
            if kind == "ORDER_FILL":
                for scheme, name in seen_in_fill(transaction, "custody"):
                    self._plugin.note_not_carried(scheme, name)
            elif kind == "DAILY_FINANCING":
                for scheme, name in seen_in_financing(transaction):
                    self._plugin.note_not_carried(scheme, name)
            key = (
                self.raw.keep_activity(
                    connection.account_id,
                    transaction,
                    now,
                    connection.synthetic,
                    self.connections.config.secrets(),
                )
                if self.raw is not None
                else f"activity/{connection.account_id}/{transaction.get('id')}"
            )
            for each in converted:
                instrument: dict[str, Any] = {}
                if each.code:
                    pair = self._pairs.get(each.instrument) if each.instrument else None
                    metal = pair is not None and not pair.tradeable and each.code == pair.base
                    held = await self.cash(each.code, cash=not metal)
                    instrument = (
                        {"instrument_id": held}
                        if held
                        else {
                            "instrument_as_reported": as_reported(
                                CURRENCY_SCHEME, each.code, f"{each.code}, as OANDA names it"
                            )
                        }
                    )
                try:
                    await self._plugin.record_activity(
                        external_account_id=connection.account_id,
                        source=SOURCE,
                        activity=meridian.CustodialActivity(
                            external_activity_id=each.external_activity_id,
                            kind=each.kind,
                            kind_as_reported=each.kind_as_reported,
                            trade_date=each.trade_date,
                            units=each.units,
                            price=each.price,
                            amount=each.amount,
                            description=each.description,
                            raw_record=self._plugin.raw_record(key),
                            **instrument,
                        ),
                    )
                except meridian.MeridianError as refused:
                    log.warning(
                        "activity %s was not recorded: %s", each.external_activity_id, refused
                    )
                    if isinstance(refused, meridian.NotLinked):
                        return sent
                    continue
                sent += 1
        if transactions:
            log.info(
                "%s: %d activities sent from %d transactions",
                connection.account_id,
                sent,
                len(transactions),
            )
        return sent

    # ── The Summary ─────────────────────────────────────────────────────

    def figures(self) -> list[meridian.Figure]:
        """The Summary's figures: the connection, the account reached, and
        when the last read was, an error with why when it failed. Counts and
        a moment, and no account's data."""
        status = self.status
        connection = self.connections.current
        last: meridian.Figure
        if status.error:
            why = status.error
            if len(why) > PLUGIN_FIGURE_WHY_LENGTH.most:
                why = why[: PLUGIN_FIGURE_WHY_LENGTH.most - 1] + "\N{HORIZONTAL ELLIPSIS}"
            last = meridian.Figure(
                "Last read", status.read_at or "Failed", state="error", why=why
            )
        else:
            last = meridian.Figure("Last read", status.read_at or "Not yet")
        return custody_role.figures(
            connections=1 if connection is not None else 0,
            accounts_reached=1 if status.account else 0,
            last_read=last,
        ) + (self.more_figures() if self.more_figures is not None else [])
