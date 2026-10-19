"""The data role (lake.py, from 0.5.0): OANDA's prices into the lake, over the
synthetic OANDA (synthetic.py) and the SDK's `Recorder` standing in for the
sidecar. SIMULATED ONLY: nothing here reaches OANDA, practice or live.

**The data suite.** A plugin holding `data` is verified for it only by
passing every case of the suite the SDK carries (meridian.suites). Each case
is mapped to the plugin's own exchange with the synthetic OANDA, through its
real venue interface, conversion and bookkeeping (the SDK's
`meridian.roles.data.Wants`). A case about something OANDA never states is
named in `NOT_PRESENTED` with why:

- bars: the plugin publishes the lake's prices and quotes only, its closes
  as prices (the v26 plan's ruling for this role);
- a crypto asset's price, a security's price: OANDA's pairs here are
  currencies against currencies;
- trades, and the cases about a trade's fields and a restart's gap of
  trades: OANDA makes the prices it quotes, and states no trades;
- calendars and settlement dates: a price needs neither (the connectivity
  role reads them for a fill's value date, which its own suite holds);
- outcomes: OANDA lists no event contract.

`queries-the-lake-within-bounds` is mapped as the SDK's data template maps
it (`lake_query`): the plugin itself asks the lake nothing. The three cases
the SDK answers (a replacement, the template catalogue, an event group) are
its.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from meridian.operations import as_decimal, as_money
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles.data.testing import lake_query
from meridian.suites import Recorder, run, suite

from oanda import moments
from oanda.lake import read_price

from rigs import EUR, INSTANCE, JPY, LakeRig, cash_id, want

Producer = Callable[[Recorder], Awaitable[None]]

NEVER = {
    "bars": "this plugin publishes the lake's prices and quotes only: its closes are prices",
    "a crypto asset's price": "OANDA's pairs here are currencies against currencies: no token",
    "a security's price": "OANDA's pairs here are currencies against currencies: no security",
    "trades": "OANDA makes the prices it quotes and states no trades: quotes and prices only",
    "calendars": "a price needs no settlement calendar: recorded as OANDA states it",
    "settlement dates": "a price needs no settlement date: recorded as OANDA states it",
    "outcomes": "OANDA lists no event contract",
    "disputed outcomes": "OANDA lists no event contract",
}
NOT_PRESENTED: dict[str, str] = {
    case.name: NEVER[case.about] for case in suite("data").cases if case.about in NEVER
}

QUOTE = "meridian.v1.Quote"
#: Monday 12 October 2026, a closed day of OANDA's (ending 17:00 New York).
MONDAY = "2026-10-12"
#: Tuesday 13 October, the day forming at the rig's 15:00 UTC.
TUESDAY = "2026-10-13"


def run_want(rig: LakeRig, *messages: ops.ObservationsWantedEvent) -> None:
    rig.recorder.answer("ObservationsWanted", lambda _: list(messages))
    asyncio.run(rig.recorder.receive(observations_wanted=rig.lake.on_want))


def producers(root: Path) -> dict[str, Producer]:
    counter = iter(range(1_000_000))

    def fresh() -> Path:
        folder = root / f"case-{next(counter)}"
        folder.mkdir()
        return folder

    def wanted(*messages: ops.ObservationsWantedEvent, **rigged: object) -> Producer:
        async def produce(lake: Recorder) -> None:
            rig = LakeRig(lake, storage=fresh(), **rigged)  # type: ignore[arg-type]
            lake.answer("ObservationsWanted", lambda _: list(messages))
            await lake.receive(observations_wanted=rig.lake.on_want)

        return produce

    async def forming(lake: Recorder) -> None:
        # The day forming, read twice as it fills: restated under one key.
        rig = LakeRig(lake, storage=fresh())
        for second in (0, 7):
            rig.clock.at = datetime(2026, 10, 13, 15, 0, second, tzinfo=UTC)
            today = moments.trading_day(rig.lake.clock_ns())
            await rig.lake.record_days([EUR], today, today)

    async def venue_not_held(lake: Recorder) -> None:
        lake.answer(
            "ResolveVenue",
            lambda _: ops.ResolveVenueResult(
                found=False, miss_reason=ops.MISS_REASON_NOT_FOUND
            ),
        )
        rig = LakeRig(lake, storage=fresh())
        monday = date.fromisoformat(MONDAY)
        await rig.lake.record_days([EUR], monday, monday)

    def ambiguous(params: ops.ResolveIdentifierParams) -> ops.ResolveIdentifierResult:
        if any(i.value == "EUR" for i in params.identifiers):
            return ops.ResolveIdentifierResult(
                found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS
            )
        return ops.ResolveIdentifierResult(
            found=True, instrument_id=cash_id(params.identifiers[0].value)
        )

    async def one_sided(lake: Recorder) -> None:
        # OANDA's stream states a bid and no ask for a pair kept current.
        rig = LakeRig(lake, storage=fresh())
        lake.answer(
            "ObservationsWanted",
            lambda _: [want("WNT-9", "live", [EUR], data_type=QUOTE, standing=True)],
        )
        await lake.receive(observations_wanted=rig.lake.on_want)
        rig.lake.heard(
            {
                "type": "PRICE",
                "instrument": "EUR_USD",
                "time": "2026-10-13T15:00:01.250000000Z",
                "tradeable": True,
                "bids": [{"price": "1.08711", "liquidity": 1000000}],
                "asks": [],
            },
            rig.lake.clock_ns(),
        )
        await rig.lake.flush()

    async def withdrawn(lake: Recorder) -> None:
        rig = LakeRig(lake, storage=fresh())
        lake.answer(
            "WantWithdrawn",
            lambda _: [ops.WantWithdrawnEvent(want_id="WNT-1", dataset=f"{INSTANCE}:live")],
        )
        await lake.receive(want_withdrawn=rig.lake.on_withdrawn)

    async def licences(lake: Recorder) -> None:
        lake.answer(
            "ListDatasets",
            lambda _: ops.ListDatasetsResult(
                licences=[ops.DatasetLicence(dataset=f"{INSTANCE}:live")]
            ),
        )
        rig = LakeRig(lake, storage=fresh())
        assert await rig.lake.wants.read_licences() == {f"{INSTANCE}:live"}

    close = want("WNT-1", "daily", [EUR], day=MONDAY, kinds=(ops.PRICE_KIND_CLOSE,))
    quotes = want("WNT-2", "live", [EUR], data_type=QUOTE)
    yen = want("WNT-3", "daily", [JPY], day=MONDAY)
    return {
        "a-daily-close": wanted(close),
        "the-forming-day-restated": forming,
        "an-exact-price": wanted(close),
        "an-fx-rate": wanted(close),
        "an-asset-that-does-not-resolve": wanted(close, resolve=ambiguous),
        "a-venue-resolved": wanted(close),
        "a-venue-not-held": venue_not_held,
        "a-want-recorded-against": wanted(close),
        "a-subject-declined": wanted(yen),
        "a-standing-want-withdrawn": withdrawn,
        "a-quote-recorded": wanted(quotes),
        "a-one-sided-quote": one_sided,
        "reads-its-datasets-licences": licences,
        "queries-the-lake-within-bounds": lake_query(EUR, "quotes"),
    }


def test_every_case_of_the_data_suite_is_mapped_or_not_presented(tmp_path: Path) -> None:
    report = run("data", producers(tmp_path), not_presented=NOT_PRESENTED, instance_id=INSTANCE)
    assert report.passed, report.failures
    assert sorted(report.not_presented) == sorted(NOT_PRESENTED)
    assert not set(report.answered_by_sdk) - {
        "hears-and-reads-a-replacement",
        "reads-the-template-catalogue",
        "hears-and-reads-an-event-group",
    }


# ── The plugin's own conversion ─────────────────────────────────────────────


def test_a_quote_and_its_mid_are_exact_and_name_the_pair_as_oanda_quotes_it() -> None:
    recorder = Recorder(INSTANCE)
    rig = LakeRig(recorder)
    run_want(rig, want("WNT-1", "live", [EUR]))
    [recorded] = recorder.on("RecordQuotes")
    assert recorded.want_id == "WNT-1"
    by_key = {q.meta.row_key: q for q in recorded.quotes}
    at = moments.of(rig.clock())
    eur_usd = by_key[f"EUR_USD:q:{at}"]
    assert [s.entity_id for s in eur_usd.meta.subjects] == [EUR]
    assert eur_usd.meta.source.dataset == f"{INSTANCE}:live"
    assert eur_usd.meta.source.venue_id, "OANDA, as the venue master holds it"
    bid, ask = as_money(eur_usd.bid), as_money(eur_usd.ask)
    assert (bid.currency_code, ask.currency_code) == ("USD", "USD")
    assert ask.amount - bid.amount == Decimal("0.00020")
    assert as_decimal(eur_usd.bid_quantity) == Decimal(1_000_000)
    assert list(eur_usd.characteristics) == []
    assert eur_usd.meta.raw.key.startswith("pricing/2026-10-13/pricing/")
    # EUR/GBP too, the euro's price in pounds: every pair it is the base of.
    assert as_money(by_key[f"EUR_GBP:q:{at}"].bid).currency_code == "GBP"
    [mids] = recorder.on("RecordPrices")
    mid = next(p for p in mids.prices if p.meta.row_key == f"EUR_USD:mid:{at}")
    assert mid.kind == ops.PRICE_KIND_MID
    assert as_money(mid.price).amount == (bid.amount + ask.amount) / 2


def test_the_yen_is_declined_and_nothing_is_inverted() -> None:
    # No pair OANDA quotes has the yen as its base; USD_JPY is never turned round.
    recorder = Recorder(INSTANCE)
    run_want(LakeRig(recorder), want("WNT-3", "live", [JPY]))
    assert recorder.on("RecordQuotes") == [] and recorder.on("RecordPrices") == []
    [declined] = recorder.on("DeclineWant")
    assert declined.reason == ops.UNANSWERED_REASON_NOT_COVERED


def test_a_past_range_of_quotes_is_beyond_history() -> None:
    # OANDA keeps no history of its quotes to read.
    recorder = Recorder(INSTANCE)
    asked = want(
        "WNT-4", "live", [EUR], valid_from_ns=moments.of(datetime(2026, 10, 1, tzinfo=UTC))
    )
    run_want(LakeRig(recorder), asked)
    [declined] = recorder.on("DeclineWant")
    assert declined.reason == ops.UNANSWERED_REASON_BEYOND_HISTORY


def test_closes_for_the_latest_are_the_past_weeks_trading_days() -> None:
    recorder = Recorder(INSTANCE)
    run_want(LakeRig(recorder), want("WNT-5", "daily", [EUR]))
    [recorded] = recorder.on("RecordPrices")
    days = sorted(
        {p.meta.business_date for p in recorded.prices if "EUR_USD" in p.meta.row_key}
    )
    # Wednesday the 7th to Tuesday the 13th: no Saturday's or Sunday's.
    assert days == ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", TUESDAY]
    forming = next(p for p in recorded.prices if p.meta.row_key == f"EUR_USD:close:{TUESDAY}")
    assert list(forming.meta.source_times) == []  # still forming: no published time
    monday = next(p for p in recorded.prices if p.meta.row_key == f"EUR_USD:close:{MONDAY}")
    [published] = monday.meta.source_times
    assert published.at_ns == moments.of(datetime(2026, 10, 12, 21, 0, tzinfo=UTC))


def test_without_an_account_every_subject_is_source_silent() -> None:
    recorder = Recorder(INSTANCE)
    rig = LakeRig(recorder)
    rig.connections.configure(rig.connections.config.__class__())  # no settings
    run_want(rig, want("WNT-6", "live", [EUR]))
    [declined] = recorder.on("DeclineWant")
    assert declined.reason == ops.UNANSWERED_REASON_SOURCE_SILENT


def test_a_standing_want_is_streamed_at_most_once_a_second_a_pair() -> None:
    recorder = Recorder(INSTANCE)
    rig = LakeRig(recorder)
    run_want(rig, want("WNT-7", "live", [EUR], standing=True))
    assert sorted(rig.lake.streams) == ["EUR_GBP", "EUR_USD"]
    venue = rig.connections.synthetic_venue
    assert venue is not None
    first = recorder.sent[:]
    for second in (1, 1, 2):  # two messages in one second: the latest is sent
        at = moments.of(datetime(2026, 10, 13, 15, 0, second, tzinfo=UTC)) + 100
        rig.lake.heard(venue.price_at("EUR_USD", at), at)
    asyncio.run(rig.lake.flush())
    streamed = [s.params for s in recorder.sent[len(first) :] if s.row == "RecordQuotes"]
    assert [len(batch.quotes) for batch in streamed] == [1]
    assert streamed[0].quotes[0].meta.raw.key.endswith("EUR_USD.jsonl#0")
    # Withdrawn: off the stream.
    recorder.answer(
        "WantWithdrawn",
        lambda _: [ops.WantWithdrawnEvent(want_id="WNT-7", dataset=f"{INSTANCE}:live")],
    )
    asyncio.run(recorder.receive(want_withdrawn=rig.lake.on_withdrawn))
    assert rig.lake.streams == {}


def test_a_price_oanda_says_is_not_tradeable_is_halted() -> None:
    recorder = Recorder(INSTANCE)
    # Saturday: OANDA's last price, not tradeable.
    rig = LakeRig(recorder, at=datetime(2026, 10, 17, 15, 0, tzinfo=UTC))
    run_want(rig, want("WNT-8", "live", [EUR], data_type=QUOTE))
    [recorded] = recorder.on("RecordQuotes")
    assert all(
        list(q.characteristics) == [ops.QUOTE_CHARACTERISTIC_HALTED] for q in recorded.quotes
    )


def test_a_figure_that_does_not_read_is_kept_as_sent() -> None:
    priced = read_price(
        {
            "type": "PRICE",
            "instrument": "EUR_USD",
            "time": "2026-10-13T15:00:00.000000000Z",
            "bids": [{"price": "1.0871x", "liquidity": 1000000}],
            "asks": [{"price": "1.08730", "liquidity": 1000000}],
        }
    )
    assert priced is not None and priced.bid is None and priced.mid is None
    assert [(u.field, u.text) for u in priced.unread] == [("bids.price", "1.0871x")]


def test_what_is_not_carried_is_counted() -> None:
    recorder = Recorder(INSTANCE)
    run_want(LakeRig(recorder), want("WNT-1", "live", [EUR]))
    assert recorder.seen[("oanda:price", "closeoutBid")] >= 1


def test_the_datasets_page_record() -> None:
    recorder = Recorder(INSTANCE)
    rig = LakeRig(recorder)
    run_want(rig, want("WNT-1", "live", [EUR]))
    read = rig.lake.datasets_read()
    assert [s.dataset for s in read.served] == [f"{INSTANCE}:live", f"{INSTANCE}:daily"]
    assert read.served[0].recorded > 0
    # Ten currency pairs listed (no metal); the euro's two serve its want.
    assert read.products_listed == 10 and read.products_mapped == 2
    assert [getattr(w, "want_id", "") for w in read.wants] == ["WNT-1"]


def test_the_pricing_stream_keeps_a_standing_want_current() -> None:
    """The stream and the pass keeping current, run together over the
    synthetic OANDA's pricing stream for a moment: each pair's price recorded
    from the stream, its raw message kept as a line first."""
    recorder = Recorder(INSTANCE)
    rig = LakeRig(recorder)
    run_want(rig, want("WNT-7", "live", [EUR], standing=True))
    before = len(recorder.sent)

    async def a_moment() -> None:
        work = asyncio.gather(rig.lake.keep_streaming(), rig.lake.keep_current())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(work, timeout=2.5)

    asyncio.run(a_moment())
    assert rig.lake.stream.opened == 1 and rig.lake.stream.state == "open"
    assert rig.lake.stream.subscribed == ("EUR_GBP", "EUR_USD")
    streamed = [s.params for s in recorder.sent[before:] if s.row == "RecordQuotes"]
    assert streamed, "quotes recorded from the stream"
    keys = {q.meta.raw.key for batch in streamed for q in batch.quotes}
    assert all("/stream/" in key and ".jsonl#" in key for key in keys)
