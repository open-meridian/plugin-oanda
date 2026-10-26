"""The data role (from 0.5.0): OANDA's prices into the lake, converted at the
edge, before the FX sample ships (plans/the-v26-build §8; the FX desk's
needs, spec/the-fx-and-multi-asset-sample-plugins §1.5: spot quotes per
pair, kept for TCA's look-back, and the rates it converts at).

**Two datasets** (declared.py), of the lake's existing kinds, prices and
quotes:

- `live`: each pair's price as OANDA's pricing states it -- its best bid and
  ask with their liquidity, as a quote, and their mid as a price of kind
  `mid` -- from the pricing stream for a standing want, at most once a second
  a pair, and from a price read now for a want of the latest. Kept, by its
  default terms, for 90 days, the deployment's to change.
- `daily`: each pair's close, the mid of OANDA's daily candle, on OANDA's
  day ending at 17:00 New York; the day still forming restated under the
  same key as it fills.

**A pair is two cash instruments** (pairs.py; ruled 2026-10-13, Q6). A row's
subject is the base currency's record, the one the lake asked for; its
price a `Money` in the quote currency, exactly as OANDA quotes the pair:
EUR_USD as the euro's price in dollars, USD_JPY as the dollar's in yen
(spec/the-lake, Q3). Nothing is inverted or crossed, so the yen's own
record, which no pair OANDA quotes has as its base, is declined as not
covered. Each currency is resolved through the master by its ISO 4217 code
(W3.1), a miss reported once (W3.2) and its pairs left out; a subject is
named as one of OANDA's currencies by its own record's `iso4217`
identifier (W3.6), never guessed. Only OANDA's currency pairs are served: a
metal or a CFD is not a pair of two cash instruments.

**The venue**: OANDA, which makes the prices it quotes, by its own name in
the venue master (W3.14), resolved once per reading; a venue not held is
reported once (W3.15) and each row names it as reported.

**Wants** (W10.7, the SDK's `meridian.roles.data.Wants`): the latest of the
live dataset, a price read now; a business date, a range or the latest (the
past week) of the daily one. Declined per subject: `not_covered` where its
record names none of OANDA's currencies, or no pair OANDA quotes has it as
the base; `beyond_history` for a past range of the live dataset, since
OANDA keeps no history of its quotes to read; `source_silent` where OANDA
said nothing, or nothing reaches it yet (no account chosen). A standing want
keeps its subjects current until withdrawn: the live dataset from the
pricing stream, the daily one's forming day every five minutes.

**A restart** (contract v20, W10.6). OANDA keeps no history of its quotes,
so a restart's gap in the live dataset cannot be read back: the stream
opens again from each pair's price now, a row keyed by the pair and OANDA's
own time, so a price recorded before is answered unchanged. The daily
dataset's past week is recorded again for a standing want heard again, each
day under its own key, so a close missed while stopped is recorded and one
held is unchanged.

**Liveness and latency** (contract v27; rulings 418 and 371, the
latency spec's convention for OANDA). OANDA's pricing stream sends a price
only when it changes, and a heartbeat every five seconds, each with OANDA's
`time`. The latest message or heartbeat heard is the live dataset's sign of
life: recorded once a pass, at OANDA's own time, for OANDA's venue
(`Wants.liveness`, RecordLiveness), it confirms every quote in force. The
stream's session (`pricing-stream`) keeps its last sign of life, its clock
skew from the heartbeat's `time`, its reconnects and OANDA's 429s, and its
round trip as the REST pricing calls' durations (the stream has no ping);
each row recorded for a standing want has its age at the edge recorded, the
feed delay -- recorded, not yet reported: core receives ReportLatency from
contract v28 (ruling 515). OANDA's own prices carry no declared delay.

**Raw records** (decisions/028): every answer OANDA's pricing gave a row is
kept first, in the kind `prices` (the SDK's `meridian.edge.RawStore`, a
unit a UTC day, moved past its window by archive.py's pass): a price read
now and a day's candles as one record each, and the stream's messages a row
was read from as lines of the day's file per pair. Each row names its
record. A request is kept by its method and path, never its headers, so
never the token.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

import meridian
from meridian import Money, ObservationMeta, Price, Quote, SourceTime, edge
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import data
from meridian.roles.data import pairs as role_pairs

from . import moments
from .connection import Connection, Connections
from .declared import CATALOGUE, DAILY, LIVE, PRICE_SCHEME, VENUE_IDENTIFIERS, VENUE_NAME
from .pairs import CURRENCY, Pair, PairMap
from .raw import dumps

log = logging.getLogger("oanda.lake")

SOURCE = "oanda"
PRICE, QUOTE = "meridian.v1.Price", "meridian.v1.Quote"
_LIVE_KINDS = {ops.PRICE_KIND_MID}
_DAILY_KINDS = {ops.PRICE_KIND_CLOSE}

#: The live dataset's pace: each pair at most once a second.
LIVE_EVERY_SECONDS = 1.0
#: A standing want of the daily dataset: its forming day, every five minutes.
DAILY_EVERY_SECONDS = 300.0
#: The licences read again, and the pair map, and a currency not resolved.
LICENCES_EVERY_SECONDS = 60.0
PAIRS_EVERY_SECONDS = 86_400.0
#: A want of the daily dataset's latest: the past week's days.
LATEST_DAYS = 7
#: The most days one want of the daily dataset is answered for.
MOST_DAYS = 3660
#: How long the stream waits before it opens again after a drop, at most.
MOST_WAIT_SECONDS = 60.0
#: The pricing stream's session, as its latency names it (contract v27).
STREAM_SESSION = "pricing-stream"

#: What OANDA allows, and what the plugin spends of it, in words.
ALLOWED = "OANDA's v20 API: at most 120 requests a second, and 2 new streams a second"
SPENT = "one pricing stream for every pair kept current, a price read per want of the latest"
ENTITLED = "Every currency pair the account may trade, from OANDA's pricing"

#: What the plugin asks OANDA's pricing, by its method and path.
PRICING_CALL = "GET /v3/accounts/{accountID}/pricing"
CANDLES_CALL = "GET /v3/accounts/{accountID}/instruments/{instrument}/candles"
STREAM_CALL = "GET /v3/accounts/{accountID}/pricing/stream"
ENDPOINTS = (
    data.Endpoint("Prices now", PRICING_CALL, "the latest of the live dataset"),
    data.Endpoint("Daily candles", CANDLES_CALL, "the daily dataset's closes"),
    data.Endpoint("Pricing stream", STREAM_CALL, "the live dataset, kept current"),
)

Plugin = Any  # meridian.Plugin, or the suite's Recorder: the SDK's typed operations


@dataclass(frozen=True)
class Priced:
    """One pair's price, as OANDA's pricing stated it: its best bid and ask
    with their liquidity, a side OANDA states none for left None, and
    whether it was tradeable."""

    instrument: str
    at_ns: int
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    tradeable: bool | None = None
    raw_key: str = ""
    unread: tuple[data.Unread, ...] = ()

    @property
    def mid(self) -> Decimal | None:
        """Halfway between the bid and the ask, exactly: a sum of two decimals
        halved always terminates. None where a side is missing."""
        if self.bid is None or self.ask is None:
            return None
        with localcontext() as exact:
            exact.prec = 60
            return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Candle:
    """One of OANDA's daily mid candles: its start, its day and its close."""

    instrument: str
    start_ns: int
    day: date
    close: Decimal | None
    complete: bool
    raw_key: str = ""
    unread: tuple[data.Unread, ...] = ()

    @property
    def end_ns(self) -> int:
        """When its day ends: 17:00 New York on its day."""
        return _day_end_ns(self.day)


def _day_end_ns(day: date) -> int:
    """17:00 New York on `day`, OANDA's day's end, in UTC nanoseconds."""
    local = datetime(day.year, day.month, day.day, moments.DAY_ENDS, tzinfo=moments.NEW_YORK)
    return moments.of(local)


def _side(stated: Any, name: str) -> tuple[Decimal | None, Decimal | None, list[data.Unread]]:
    """A side's best price and its liquidity: the first of OANDA's levels."""
    levels = stated.get(name)
    if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
        return None, None, []
    top = levels[0]
    unread: list[data.Unread] = []
    price = data.number(top.get("price"))
    if price is None:
        unread.append(data.Unread(f"{name}.price", str(top.get("price", ""))))
    size = data.number(top.get("liquidity")) if top.get("liquidity") is not None else None
    if top.get("liquidity") is not None and size is None:
        unread.append(data.Unread(f"{name}.liquidity", str(top.get("liquidity"))))
    return price, size, unread


def read_price(stated: Any, raw_key: str = "") -> Priced | None:
    """One of OANDA's PRICE objects, read exactly; None where it is no price
    of an instrument at a stated time."""
    if not isinstance(stated, dict) or stated.get("type", "PRICE") != "PRICE":
        return None
    instrument = str(stated.get("instrument") or "")
    at_ns = moments.ns(stated.get("time"))
    if not instrument or not at_ns:
        return None
    bid, bid_size, bid_unread = _side(stated, "bids")
    ask, ask_size, ask_unread = _side(stated, "asks")
    tradeable = stated.get("tradeable")
    return Priced(
        instrument=instrument,
        at_ns=at_ns,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        tradeable=tradeable if isinstance(tradeable, bool) else None,
        raw_key=raw_key,
        unread=tuple(bid_unread + ask_unread),
    )


def read_candle(instrument: str, stated: Any, raw_key: str = "") -> Candle | None:
    """One of OANDA's daily mid candles; None where it states no start."""
    if not isinstance(stated, dict):
        return None
    start = moments.ns(stated.get("time"))
    if not start:
        return None
    stated_mid = stated.get("mid")
    mid: dict[str, Any] = stated_mid if isinstance(stated_mid, dict) else {}
    close = data.number(mid.get("c"))
    unread = () if close is not None else (data.Unread("mid.c", str(mid.get("c", ""))),)
    return Candle(
        instrument=instrument,
        start_ns=start,
        day=moments.trading_day(start),
        close=close,
        complete=stated.get("complete") is True,
        raw_key=raw_key,
        unread=unread,
    )


@dataclass
class Stream:
    """How the pricing stream has gone: "off", "connecting", "open" or
    "waiting" to open again; the pairs on it; how often it opened and
    dropped; its messages."""

    state: str = "off"
    subscribed: tuple[str, ...] = ()
    opened: int = 0
    dropped: int = 0
    messages: int = 0
    last_message_ns: int = 0
    last_error: str = ""
    #: OANDA's time of the latest price or heartbeat heard, and the liveness
    #: last recorded from it (contract v27).
    alive_ns: int = 0
    liveness_ns: int = 0


@dataclass
class Lake:
    """The data role's work: resolving, recording, answering wants and keeping
    them current (see the module)."""

    plugin: Plugin
    connections: Connections
    pairs: PairMap
    raw: edge.RawStore
    clock_ns: Callable[[], int] = time.time_ns
    venue: data.Venue = field(default_factory=data.Venue)
    #: The deployment's cash instrument of each currency resolved, by its code;
    #: and each currency reported missing, and why.
    ids: dict[str, str] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    #: Which of OANDA's currencies each subject is, by its record's ID; and
    #: the subjects whose record names none, until the pairs are read again.
    subjects: dict[str, str] = field(default_factory=dict)
    unnamed: set[str] = field(default_factory=set)
    #: Whether the deployment answers this plugin's ResolveInstrument (W3.6).
    reads_instruments: bool = True
    #: When the pair map was last read here, and the session it was read in.
    pairs_read_ns: int = 0
    session: data.Session = field(
        default_factory=lambda: data.Session("live", ENDPOINTS, data.Limits(ALLOWED, SPENT))
    )
    stream: Stream = field(default_factory=Stream)
    #: The plugin's `meridian.roles.Latency`, where it keeps one (contract
    #: v27): the stream's session and each row's age at the edge.
    latency: Any = None
    #: The pairs on the stream, each with the subjects and data types it is
    #: kept current for; and the latest message heard for each, not sent yet.
    streams: dict[str, tuple[tuple[str, ...], frozenset[str]]] = field(default_factory=dict)
    pending: dict[str, tuple[dict[str, Any], int]] = field(default_factory=dict)
    wants: data.Wants = field(init=False)
    _changed: asyncio.Event = field(init=False)

    def __post_init__(self) -> None:
        self.wants = data.Wants(
            self.plugin,
            (DAILY, LIVE),
            clock_ns=self.clock_ns,
            latency=self.latency,
            session=STREAM_SESSION if self.latency is not None else "",
        )
        if self.latency is not None:
            self.latency.session(STREAM_SESSION, "rest")
        self._changed = asyncio.Event()

    # ── The connection ───────────────────────────────────────────────────

    def connection(self) -> Connection | None:
        """The connection the settings and the admin's choice name: pricing is
        asked of its account."""
        return self.connections.current

    def _moved(self, connection: Connection) -> bool:
        """Whether the connection in use is another than `connection`: another
        environment, account or venue (sample data switched), not merely its
        trading enabled."""
        now = self.connection()
        return now is None or (now.connection_id, now.venue) != (
            connection.connection_id,
            connection.venue,
        )

    def _session_mode(self) -> None:
        connection = self.connection()
        synthetic = connection is not None and connection.synthetic
        self.session.mode = "sample" if synthetic else "live"

    async def _ask(self, call: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        """One question of OANDA, counted on the session's health; None where it
        refused or did not answer, which is said and never raised."""
        health = self.session.health
        health.calls += 1
        started = self.clock_ns()
        try:
            answer = await call(*args)
        except Exception as failed:  # noqa: BLE001 - OANDA's refusal, said, not raised
            health.failures += 1
            health.last_failed_ns = self.clock_ns()
            health.last_error = str(failed)[:300]
            if getattr(failed, "status", None) == 429:
                health.rate_limited += 1
                if self.latency is not None:
                    self.latency.count(STREAM_SESSION, rate_limited=1)
            log.warning("OANDA did not answer: %s", failed)
            return None
        health.last_ok_ns = self.clock_ns()
        if self.latency is not None:
            # The stream has no ping: its round trip is the REST pricing
            # calls' (the latency spec, OANDA's convention).
            self.latency.rtt(STREAM_SESSION, health.last_ok_ns - started)
        return answer

    # ── Resolving ────────────────────────────────────────────────────────

    async def read_pairs(self, again: bool = False) -> None:
        """The account's instruments into the pair map, once in the process and
        then once a day; currencies not resolved asked again with them."""
        connection = self.connection()
        if connection is None:
            return
        now = self.clock_ns()
        if self.pairs_read_ns and not again and len(self.pairs):
            return
        listed = await self._ask(connection.venue.instruments, connection.account_id)
        if listed is not None:
            self.pairs.take(listed)
        self.pairs_read_ns = now
        self.missing.clear()
        self.unnamed.clear()
        self.venue.asked = False

    def currencies(self) -> list[str]:
        """Every currency of a pair OANDA quotes the account, once."""
        return sorted({c for p in self.pairs.pairs() if p.kind == CURRENCY
                       for c in (p.base, p.quote)})  # fmt: skip

    async def cash(self, code: str) -> str:
        """The deployment's cash instrument for a currency, by its ISO 4217 code
        (W3.1); "" where it holds none or more than one, reported once."""
        if code in self.ids:
            return self.ids[code]
        if code in self.missing:
            return ""
        now = self.clock_ns()
        named = [meridian.Identifier(scheme="iso4217", value=code)]
        found = await self.plugin.resolve_identifier(
            identifiers=named, as_of_ns=now, stated_asset_class="cash", stated_currency=code
        )
        if found.found and found.instrument_id:
            self.ids[code] = str(found.instrument_id)
            return self.ids[code]
        miss = found.miss_reason or ops.MISS_REASON_NOT_FOUND
        self.missing[code] = ops.MissReason.Name(miss)
        await self.plugin.report_missing_instrument(
            source=SOURCE,
            asset_class="cash",
            identifiers=named,
            as_of_ns=now,
            reason=miss,
            observed_at_ns=now,
        )
        return ""

    async def code_of(self, subject: str) -> str:
        """Which of OANDA's currencies a subject is: one resolved here, or read
        from its record's `iso4217` identifier (W3.6); empty where its record
        names none of them, or cannot be read."""
        known = self.subjects.get(subject) or next(
            (code for code, held in self.ids.items() if held == subject), ""
        )
        if known or subject in self.unnamed or not self.reads_instruments:
            return known
        try:
            found = await self.plugin.resolve_instrument(
                instrument_id=subject, as_of_ns=self.clock_ns()
            )
        except meridian.NotGranted:
            log.warning("this data plugin may not read an instrument's record")
            self.reads_instruments = False
            return ""
        offered = set(self.currencies())
        code = ""
        if found.found and found.instrument.asset_class in (
            ops.ASSET_CLASS_UNSPECIFIED,
            ops.ASSET_CLASS_CASH,
        ):
            code = next(
                (i.value.upper() for i in found.instrument.identifiers
                 if i.scheme == "iso4217" and i.value.upper() in offered),
                "",
            )  # fmt: skip
        if not code:
            self.unnamed.add(subject)
            return ""
        self.subjects[subject] = code
        return code

    async def serving(self, subjects: Iterable[str]) -> dict[str, list[Pair]]:
        """Each subject's pairs, those OANDA quotes with it as the base and both
        currencies resolved: none where it is not covered."""
        await self.read_pairs()
        served: dict[str, list[Pair]] = {}
        for subject in subjects:
            code = await self.code_of(subject)
            found: list[Pair] = []
            if code and await self.cash(code):
                for pair in self.pairs.pairs():
                    if pair.kind != CURRENCY or pair.base != code:
                        continue
                    if await self.cash(pair.quote):
                        found.append(pair)
            served[subject] = found
        return served

    async def venue_id(self) -> str:
        """The venue master's ID for OANDA, asked once per reading of its
        pairs; a venue not held reported once and left empty."""
        return await self.venue.resolve(self.plugin, VENUE_IDENTIFIERS, SOURCE, self.clock_ns())

    # ── Rows ─────────────────────────────────────────────────────────────

    def _meta(
        self,
        row_key: str,
        subject: str,
        key: str,
        valid_from_ns: int,
        valid_until_ns: int,
        times: Sequence[SourceTime],
        raw_key: str,
        unread: Iterable[data.Unread] = (),
        business_date: date | None = None,
    ) -> ObservationMeta:
        return ObservationMeta(
            row_key=row_key,
            subjects=[meridian.SubjectRef(entity_id=subject)],
            source=ops.Source(dataset=self.wants.dataset(key), venue_id=self.venue.venue_id),
            valid_from_ns=valid_from_ns,
            valid_until_ns=valid_until_ns,
            business_date=business_date or "",
            source_times=list(times),
            raw=self.plugin.raw_record(raw_key),
            unconverted=data.unconverted(SOURCE, unread, self.venue, VENUE_NAME),
        )

    def quote(self, subject: str, pair: Pair, priced: Priced) -> Quote | None:
        """OANDA's best bid and ask with their liquidity, in force from its
        time; a side it states none for left unset, and a price it says is not
        tradeable `halted`. None where neither side has a price."""
        if priced.bid is None and priced.ask is None:
            return None
        return Quote(
            meta=self._meta(
                f"{pair.name}:q:{priced.at_ns}",
                subject,
                LIVE,
                priced.at_ns,
                0,
                [SourceTime(kind="event", at_ns=priced.at_ns)],
                priced.raw_key,
                priced.unread,
            ),
            bid=None if priced.bid is None else Money(priced.bid, pair.quote),
            ask=None if priced.ask is None else Money(priced.ask, pair.quote),
            bid_quantity=priced.bid_size if priced.bid is not None else None,
            ask_quantity=priced.ask_size if priced.ask is not None else None,
            characteristics=["halted"] if priced.tradeable is False else [],
        )

    def mid(self, subject: str, pair: Pair, priced: Priced) -> Price | None:
        """The mid of OANDA's best bid and ask, at its time: the rate a desk
        converts at. None where a side is missing."""
        mid = priced.mid
        if mid is None:
            return None
        return Price(
            meta=self._meta(
                f"{pair.name}:mid:{priced.at_ns}",
                subject,
                LIVE,
                priced.at_ns,
                0,
                [SourceTime(kind="event", at_ns=priced.at_ns)],
                priced.raw_key,
            ),
            kind="mid",
            price=Money(mid, pair.quote),
            basis="per_unit",
        )

    def close(self, subject: str, pair: Pair, candle: Candle) -> Price | None:
        """The day's close, the mid candle's, for its business date: OANDA's
        day ending at 17:00 New York; a closed day with its published time,
        the day's end."""
        if candle.close is None:
            return None
        end = candle.end_ns
        times = [SourceTime(kind="published", at_ns=end)] if candle.complete else []
        return Price(
            meta=self._meta(
                f"{pair.name}:close:{candle.day.isoformat()}",
                subject,
                DAILY,
                candle.start_ns,
                end,
                times,
                candle.raw_key,
                business_date=candle.day,
            ),
            kind="close",
            price=Money(candle.close, pair.quote),
            basis="per_unit",
        )

    def _rows(
        self, subject: str, pair: Pair, priced: Priced, data_types: frozenset[str]
    ) -> tuple[list[Quote], list[Price]]:
        quotes: list[Quote] = []
        prices: list[Price] = []
        if QUOTE in data_types and (row := self.quote(subject, pair, priced)):
            quotes.append(row)
        if PRICE in data_types and (price := self.mid(subject, pair, priced)):
            prices.append(price)
        self.wants.counts.unread += len(priced.unread)
        return quotes, prices

    # ── Recording ────────────────────────────────────────────────────────

    def _keep(self, call: str, name: str, request: str, body: Any) -> str:
        return self.raw.keep(
            call, name, {"received_at_ns": self.clock_ns(), "request": request,
                         "body": dumps(body)},
        )  # fmt: skip

    async def record_live(
        self,
        subjects: Iterable[str],
        data_types: frozenset[str] = frozenset({PRICE, QUOTE}),
        want_id: str = "",
    ) -> dict[str, str]:
        """Each subject's price now, from every pair it is the base of: its
        quote and its mid, as asked; what was declined, by subject and
        reason."""
        connection = self.connection()
        subjects = list(subjects)
        if connection is None:
            return {s: "source_silent" for s in subjects}
        served = await self.serving(subjects)
        await self.venue_id()
        instruments = sorted({p.name for found in served.values() for p in found})
        declined = {s: "not_covered" for s, found in served.items() if not found}
        if not instruments:
            return declined
        answer = await self._ask(connection.venue.pricing, connection.account_id, instruments)
        priced: dict[str, Priced] = {}
        if answer is not None:
            key = self._keep(
                "pricing",
                connection.account_id,
                f"{PRICING_CALL}?instruments={','.join(instruments)}",
                answer,
            )
            for stated in answer.get("prices") or []:
                read = read_price(stated, f"{key}#{stated.get('instrument', '')}")
                if read is not None:
                    priced[read.instrument] = read
                    self._note(stated)
        quotes: list[Quote] = []
        prices: list[Price] = []
        for subject, found in served.items():
            before = len(quotes) + len(prices)
            for pair in found:
                if pair.name in priced:
                    q, p = self._rows(subject, pair, priced[pair.name], data_types)
                    quotes += q
                    prices += p
            if found and len(quotes) + len(prices) == before:
                declined[subject] = "source_silent"
        await self.wants.record("quotes", quotes, LIVE, want_id)
        await self.wants.record("prices", prices, LIVE, want_id)
        return declined

    async def record_days(
        self, subjects: Iterable[str], first: date, last: date, want_id: str = ""
    ) -> dict[str, str]:
        """Each subject's closes from `first` to `last`, OANDA's days, from every
        pair it is the base of; what was declined, by subject and reason."""
        connection = self.connection()
        subjects = list(subjects)
        if connection is None:
            return {s: "source_silent" for s in subjects}
        served = await self.serving(subjects)
        await self.venue_id()
        today = moments.trading_day(self.clock_ns())
        first = max(first, last - timedelta(days=MOST_DAYS - 1))
        declined = {s: "not_covered" for s, found in served.items() if not found}
        if first > today:
            return declined | {s: "source_silent" for s, found in served.items() if found}
        from_time = moments.rfc3339(_day_end_ns(first - timedelta(days=1)))
        to_time = moments.rfc3339(min(_day_end_ns(min(last, today)), self.clock_ns()))
        candles: dict[str, list[Candle]] = {}
        prices: list[Price] = []
        for subject, found in served.items():
            before = len(prices)
            for pair in found:
                if pair.name not in candles:
                    candles[pair.name] = await self._candles(
                        connection, pair.name, from_time, to_time
                    )
                for candle in candles[pair.name]:
                    row = self.close(subject, pair, candle)
                    if first <= candle.day <= last and row is not None:
                        prices.append(row)
            if found and len(prices) == before:
                declined[subject] = "source_silent"
        await self.wants.record("prices", prices, DAILY, want_id)
        return declined

    async def _candles(
        self, connection: Connection, instrument: str, from_time: str, to_time: str
    ) -> list[Candle]:
        answer = await self._ask(
            connection.venue.candles, connection.account_id, instrument, from_time, to_time
        )
        if answer is None:
            return []
        key = self._keep(
            "candles",
            instrument,
            f"{CANDLES_CALL}?price=M&granularity=D&from={from_time}&to={to_time}",
            {"instrument": instrument, "candles": answer},
        )
        read = [read_candle(instrument, c, f"{key}#{c.get('time', '')}") for c in answer]
        found = [c for c in read if c is not None]
        self.wants.counts.unread += sum(1 for c in found if c.unread)
        return found

    def _note(self, stated: Any) -> None:
        """Count what OANDA states of a price that is not carried."""
        note = getattr(self.plugin, "note_not_carried", None)
        if note is None or not isinstance(stated, dict):
            return
        for name in ("closeoutBid", "closeoutAsk"):
            if stated.get(name) not in (None, ""):
                note(PRICE_SCHEME, name)

    # ── Wants ────────────────────────────────────────────────────────────

    async def on_want(self, heard: Any) -> None:
        """Record what the lake wants, or decline it per subject."""
        await self.wants.hear(heard, self._answer)

    async def on_withdrawn(self, heard: Any) -> None:
        """A standing want withdrawn: its subjects no longer kept current, nor
        streamed unless another stands for them."""
        withdrawn = self.wants.withdraw(heard.message.want_id)
        if withdrawn is not None and self.wants.key_of(withdrawn.dataset) == LIVE:
            await self.restream()

    @staticmethod
    def _types(key: str, data_type: str, kinds: Sequence[int]) -> frozenset[str]:
        """What a want of `data_type` and `kinds` asks of `key` that it holds:
        none where it asks for nothing it holds."""
        if key == LIVE:
            types: set[str] = set()
            if data_type in ("", QUOTE):
                types.add(QUOTE)
            if data_type in ("", PRICE) and (not kinds or set(kinds) & _LIVE_KINDS):
                types.add(PRICE)
            if data_type == "" and kinds and not set(kinds) & _LIVE_KINDS:
                types.discard(PRICE)
            return frozenset(types)
        daily = not kinds or bool(set(kinds) & _DAILY_KINDS)
        if key == DAILY and data_type in ("", PRICE) and daily:
            return frozenset({PRICE})
        return frozenset()

    async def _answer(
        self,
        want: ops.ObservationsWantedEvent,
        key: str,
        subjects: list[str],
        kinds: tuple[int, ...],
    ) -> dict[str, str]:
        types = self._types(key, want.data_type, kinds)
        if not types:
            return {s: "not_covered" for s in subjects}
        if key == LIVE:
            if want.business_date or (want.valid_from_ns and not want.standing):
                # OANDA keeps no history of its quotes to read.
                return {s: "beyond_history" for s in subjects}
            if want.standing:
                self.wants.stand(want, subjects, kinds)
                await self.restream()
            return await self.record_live(subjects, types, want.want_id)
        if want.standing:
            self.wants.stand(want, subjects, kinds)
        today = moments.trading_day(self.clock_ns())
        if want.business_date:
            day = date.fromisoformat(want.business_date)
            return await self.record_days(subjects, day, day, want.want_id)
        if want.valid_from_ns:
            until = want.valid_until_ns or self.clock_ns()
            first = moments.trading_day(want.valid_from_ns)
            last = moments.trading_day(max(until - 1, want.valid_from_ns))
            return await self.record_days(subjects, first, last, want.want_id)
        # The latest: the past week's days, the forming one among them, so a
        # subject OANDA still prices is never declined for a day barely begun.
        return await self.record_days(
            subjects, today - timedelta(days=LATEST_DAYS - 1), today, want.want_id
        )

    # ── The stream ───────────────────────────────────────────────────────

    async def restream(self) -> None:
        """Put on the stream the pairs of every subject under a standing want of
        the live dataset, each with what it is kept current for; the stream
        opened again where that changed, and closed where none stands."""
        wanted: dict[str, tuple[set[str], set[str]]] = {}
        standing = self.wants.standing_of(LIVE)
        served = await self.serving(sorted({s for w in standing for s in w.subjects}))
        for want in standing:
            types = self._types(LIVE, want.data_type, want.kinds)
            for subject in want.subjects:
                for pair in served.get(subject, []):
                    held = wanted.setdefault(pair.name, (set(), set()))
                    held[0].add(subject)
                    held[1].update(types)
        streams = {
            name: (tuple(sorted(subjects)), frozenset(types))
            for name, (subjects, types) in wanted.items()
        }
        if streams != self.streams:
            self.streams = streams
            self._changed.set()

    async def flush(self) -> None:
        """Send the latest price heard for each pair on the stream, its raw
        message kept first: at most once a pass, so a pair at most once a
        second."""
        await self.alive()
        pending, self.pending = self.pending, {}
        connection = self.connection()
        if not pending or connection is None:
            return
        quotes: list[Quote] = []
        prices: list[Price] = []
        for instrument, (stated, received) in sorted(pending.items()):
            held = self.streams.get(instrument)
            pair = self.pairs.get(instrument)
            if held is None or pair is None:
                continue
            key = self.raw.keep_line(
                "stream",
                instrument,
                {
                    "received_at_ns": received,
                    "request": STREAM_CALL,
                    "body": dumps(stated),
                },
            )
            priced = read_price(stated, key)
            if priced is None:
                self.wants.counts.unread += 1
                continue
            self._note(stated)
            subjects, types = held
            for subject in subjects:
                q, p = self._rows(subject, pair, priced, types)
                quotes += q
                prices += p
        try:
            await self.wants.record("quotes", quotes, LIVE)
            await self.wants.record("prices", prices, LIVE)
        except (meridian.MeridianError, ValueError) as refused:
            log.warning("%d quotes and %d prices not recorded: %s",
                        len(quotes), len(prices), refused)  # fmt: skip

    async def alive(self) -> bool:
        """The stream's sign of life since the last (contract v27; ruling
        418): the live dataset's liveness at OANDA's time of the latest price
        or heartbeat, recorded for OANDA's venue once a pass, which confirms
        every quote in force -- OANDA sends a price only on change. Whether
        it was recorded; a refusal is said, never raised."""
        stream = self.stream
        if stream.state != "open" or stream.alive_ns <= stream.liveness_ns:
            return False
        stream.liveness_ns = stream.alive_ns
        try:
            await self.wants.liveness(LIVE, stream.alive_ns, self.venue.venue_id)
        except (meridian.MeridianError, ValueError) as refused:
            log.warning("the stream's liveness not recorded: %s", refused)
            return False
        return True

    def heard(self, stated: dict[str, Any], received_ns: int) -> None:
        """One message of the pricing stream: a pair's price kept as its latest,
        sent by the next pass; a heartbeat counted. Either is the stream's
        sign of life at OANDA's `time` (contract v27)."""
        self.stream.messages += 1
        self.stream.last_message_ns = received_ns
        at_ns = moments.ns(stated.get("time"))
        if at_ns:
            self.stream.alive_ns = max(self.stream.alive_ns, at_ns)
        if self.latency is not None:
            self.latency.alive(STREAM_SESSION, received_ns)
            if at_ns and stated.get("type") == "HEARTBEAT":
                self.latency.clock_skew(STREAM_SESSION, at_ns)
        if stated.get("type") == "PRICE":
            instrument = str(stated.get("instrument") or "")
            if instrument in self.streams:
                self.pending[instrument] = (stated, received_ns)

    async def keep_streaming(
        self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        """Keep the pricing stream open while anything stands on it, until
        cancelled: opened again when what stands on it, or the connection,
        changes, and after a drop, waiting longer each time, a minute at most."""
        wait = LIVE_EVERY_SECONDS
        while True:
            connection = self.connection()
            instruments = sorted(self.streams)
            self.stream.subscribed = tuple(instruments)
            if connection is None or not instruments:
                self.stream.state = "off"
                self._changed.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), timeout=5.0)
                continue
            self._changed.clear()
            self.stream.state = "connecting"
            try:
                lines = connection.venue.pricing_stream(connection.account_id, instruments)
                try:
                    async for stated in lines:
                        if self.stream.state != "open":
                            self.stream.state = "open"
                            self.stream.opened += 1
                            wait = LIVE_EVERY_SECONDS
                        self.heard(stated, self.clock_ns())
                        if self._changed.is_set() or self._moved(connection):
                            break
                    else:
                        self.stream.dropped += 1
                        self.stream.last_error = "OANDA ended the stream"
                        self._reconnects()
                finally:
                    close = getattr(lines, "aclose", None)
                    if close is not None:
                        await close()
            except Exception as failed:  # noqa: BLE001 - said, and opened again
                self.stream.dropped += 1
                self.stream.last_error = str(failed)[:300]
                self._reconnects()
                log.warning("the pricing stream stopped, opening it again: %s", failed)
            if self._changed.is_set() or self._moved(connection):
                continue
            self.stream.state = "waiting"
            await sleep(wait)
            wait = min(wait * 2, MOST_WAIT_SECONDS)

    def _reconnects(self) -> None:
        if self.latency is not None:
            self.latency.count(STREAM_SESSION, reconnects=1)

    # ── Keeping current ──────────────────────────────────────────────────

    async def current(self, daily: bool = False) -> None:
        """One pass keeping each standing want's subjects current: what the
        stream heard sent, and with `daily` the daily dataset's forming day."""
        self._session_mode()
        await self.flush()
        if daily:
            today = moments.trading_day(self.clock_ns())
            for want in self.wants.standing_of(DAILY):
                await self.record_days(want.subjects, today - timedelta(days=1), today)

    async def keep_current(
        self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        """Keep each standing want's subjects current, until cancelled: the
        stream's prices at most once a second, the daily dataset's forming
        day every five minutes, the licences each minute, and the pair map
        once a day, its currencies not resolved asked again."""
        last_daily = last_licences = 0.0
        last_pairs = time.monotonic()
        while True:
            now = time.monotonic()
            try:
                if now - last_pairs >= PAIRS_EVERY_SECONDS:
                    await self.read_pairs(again=True)
                    await self.restream()
                    last_pairs = now
                if now - last_licences >= LICENCES_EVERY_SECONDS:
                    await self.wants.read_licences()
                    last_licences = now
                due = now - last_daily >= DAILY_EVERY_SECONDS
                await self.current(daily=due)
                if due:
                    last_daily = now
                standing = self.wants.standing_of(LIVE)
                if self.connection() is not None and not self.streams and standing:
                    # A connection made since the want stood: put it on the stream.
                    await self.restream()
            except Exception as failed:  # noqa: BLE001 - said, and tried again next time
                log.warning("keeping current stopped this time: %s", failed)
            await sleep(LIVE_EVERY_SECONDS)

    # ── What the pages and the Summary read ──────────────────────────────

    def datasets_read(self) -> role_pairs.DatasetsRead:
        """Datasets' record (`read_datasets`), the SDK's for a venue listing
        pairs: each dataset, each want, and OANDA's pairs."""
        named = set(self.subjects.values())
        served = [
            p for p in self.pairs.pairs()
            if p.kind == CURRENCY and p.base in named and p.base in self.ids
            and p.quote in self.ids
        ]  # fmt: skip
        return role_pairs.DatasetsRead(
            served=data.served(
                CATALOGUE, self.plugin.identity.instance_id, self.wants, ENTITLED
            ),
            wants=list(data.want_rows(self.wants)),
            reads_instruments=self.reads_instruments,
            standing_wants=len(self.wants.standing),
            products_listed=sum(1 for p in self.pairs.pairs() if p.kind == CURRENCY),
            products_mapped=len(served),
            subjects_named=len(self.subjects),
            assets_not_resolved=sorted(self.missing),
            map_read_at=data.iso_time(self.pairs_read_ns),
            restated=self.wants.counts.restated,
            unchanged=self.wants.counts.unchanged,
            not_read=self.wants.counts.unread,
        )

    def figures(self) -> list[meridian.Figure]:
        """The data role's figures on the Summary: its stream and when it last
        recorded; Connection is the custody role's, which reads the same
        account with the same token."""
        stream: str | meridian.Figure = "Off"
        if self.stream.state != "off":
            pairs = len(self.stream.subscribed)
            said = f"{pairs} {'pair' if pairs == 1 else 'pairs'}"
            open_ = self.stream.state == "open"
            stream = meridian.Figure(
                "",
                said,
                state=None if open_ else "warn",
                why=None if open_ else f"The pricing stream is {self.stream.state}",
            )
        last = max((self.wants.sent_to(key).last_ns for key in (DAILY, LIVE)), default=0)
        return data.figures(
            connection=None,
            stream=stream,
            last_recorded=datetime.fromtimestamp(last / 1e9, tz=UTC) if last else None,
        )


def connection_read(
    lake: Lake | None,
    credentials: Sequence[data.Credential],
    mode: str,
) -> Any:
    """Connection's record before the data role is held (None) or from its
    work."""
    from .records import LakeConnectionRead

    if lake is None:
        return LakeConnectionRead(
            mode=mode,
            credentials=list(credentials),
            sessions=0,
            venue=VENUE_NAME,
            entitled_at_vendor=ENTITLED,
        )
    health = lake.session.health
    stream = lake.stream
    return LakeConnectionRead(
        mode=mode,
        credentials=list(credentials),
        entitled_at_vendor=ENTITLED,
        limit=f"{ALLOWED}. This plugin: {SPENT}",
        calls=health.calls,
        rate_limited=health.rate_limited,
        last_call_at=data.iso_time(max(health.last_ok_ns, health.last_failed_ns)),
        last_error=health.last_error,
        sessions=1 if stream.state == "open" else 0,
        venue=VENUE_NAME,
        venue_id=lake.venue.venue_id,
        venue_held=lake.venue.asked and not lake.venue.missing,
        healthy=health.healthy,
        endpoints=list(ENDPOINTS),
        allowed=ALLOWED,
        spent=SPENT,
        failures=health.failures,
        stream_state=stream.state,
        streaming=len(stream.subscribed),
        stream_opened=stream.opened,
        stream_dropped=stream.dropped,
        stream_messages=stream.messages,
        last_message_at=data.iso_time(stream.last_message_ns),
        stream_error=stream.last_error,
    )
