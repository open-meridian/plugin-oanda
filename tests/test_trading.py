"""The connectivity work (trading.py) against the synthetic OANDA: what is
sent to OANDA, and what is reported to the book. SIMULATED ONLY."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from oanda import synthetic
from oanda.trading import execution_id

from rigs import ACCOUNT, EUR, USD, TradeRig, asking, computed, money, not_held, placement


def run(work: object) -> None:
    asyncio.run(work)  # type: ignore[arg-type]


def events(recorder: Recorder) -> list[str]:
    return [ops.PlacementEvent.Name(p.event) for p in recorder.on("ReportPlacement")]


def test_a_buy_is_sent_as_oandas_units_and_its_fill_moves_both_legs() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement()
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()
        (order,) = rig.venue.orders()
        assert order["instrument"] == "EUR_USD" and order["units"] == "10000"
        assert order["type"] == "LIMIT" and order["price"] == "1.08000"
        assert order["timeInForce"] == "GTC"
        assert order["clientExtensions"]["id"] == held.placement_id

    run(work())
    assert events(recorder) == ["PLACEMENT_EVENT_ACKNOWLEDGED"]
    (fill,) = recorder.on("ReportExecution")
    assert fill.kind == ops.EXECUTION_KIND_VENUE_REPORTED
    assert as_decimal(fill.quantity) == 10000
    assert as_decimal(fill.price.amount) == Decimal("1.08000")
    assert fill.price.currency_code == "USD"
    assert fill.venue_execution_id.startswith(f"{ACCOUNT}:")
    # The value date is the date read's, asked with the pair's two cash
    # instruments and the fill's moment; OANDA states none beside it.
    (asked,) = recorder.on("ComputeDate")
    assert list(asked.spot.pair) == [EUR, USD] and asked.spot.at_ns == fill.trade_time_ns
    assert fill.computed_settlement_date == "2026-10-15" and fill.settlement_date == ""
    # OANDA's trading day: 11:00 in New York is the 13th's.
    assert fill.trade_date == "2026-10-13"
    assert fill.idempotency_key == f"fill:{fill.venue_execution_id}"


def test_a_sell_is_negative_units() -> None:
    async def work() -> None:
        rig = TradeRig(Recorder("oanda"))
        await rig.started()
        held = placement()
        rig.holds(held, side=ops.SIDE_SELL)
        await rig.hear(held)
        (order,) = rig.venue.orders()
        assert order["units"] == "-10000"

    run(work())


def test_a_fill_waits_for_its_value_date_never_guessed_even_across_a_restart() -> None:
    recorder = Recorder("oanda")
    answers = {"date": not_held()}

    async def work() -> None:
        rig = TradeRig(recorder, dates=lambda _: answers["date"])
        await rig.started()
        held = placement()
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()
        assert recorder.on("ReportExecution") == [], "never sent with a guessed date"
        assert len(rig.kept["waiting"].value) == 1
        # Started again: the waiting fill is kept in storage.
        rig.holds(
            placement(
                state=ops.PLACEMENT_STATE_WORKING, venue_order_id=rig.venue.orders()[0]["id"]
            )
        )
        rig.executions.clear()
        rig.trader = rig.make()
        await rig.trader.start()
        await rig.trader.poll()
        assert recorder.on("ReportExecution") == []
        answers["date"] = computed("2026-10-16")
        await rig.trader.poll()
        assert rig.kept["waiting"].value == []

    run(work())
    (fill,) = recorder.on("ReportExecution")
    assert fill.computed_settlement_date == "2026-10-16"


def test_a_read_only_connection_rejects_unsupported_and_sends_nothing() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder, trading=False)
        await rig.started()
        held = placement()
        rig.holds(held)
        await rig.hear(held)
        assert rig.venue.orders() == []

    run(work())
    (said,) = recorder.on("ReportPlacement")
    assert said.rejection_reason == ops.REJECTION_REASON_UNSUPPORTED
    (catalogue,) = recorder.on("ReportVenueCatalogue")
    assert list(catalogue.catalogue.order_types) == [], "a read-only connection offers none"


def reasons(recorder: Recorder) -> list[str]:
    return [
        ops.RejectionReason.Name(p.rejection_reason) for p in recorder.on("ReportPlacement")
    ]


def test_a_market_order_names_no_counter_currency_in_contract_v22() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(limit=None, tif=ops.TIME_IN_FORCE_FILL_OR_KILL)
        rig.holds(held, order_type=ops.ORDER_TYPE_MARKET)
        await rig.hear(held)
        assert rig.venue.orders() == []

    run(work())
    assert reasons(recorder) == ["REJECTION_REASON_UNSUPPORTED"]
    kept = recorder.on("ReportPlacement")[0].raw_record.key
    assert kept.startswith(f"trading/{ACCOUNT}/") and kept.endswith("/offered")


def test_a_pair_oanda_quotes_the_other_way_is_rejected_naming_it() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(limit="0.92", quote="EUR")
        rig.holds(held, instrument_id=USD)
        await rig.hear(held)
        assert rig.venue.orders() == []
        record = rig.raw.find(recorder.on("ReportPlacement")[0].raw_record.key)
        assert record is not None
        assert "EUR_USD" in json.dumps(record.document)

    run(work())
    assert reasons(recorder) == ["REJECTION_REASON_UNSUPPORTED"]


def test_a_refusal_is_venue_rejected_with_oandas_reason() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(quantity=f"{synthetic.REJECTED}")
        rig.holds(held)
        await rig.hear(held)

    run(work())
    (said,) = recorder.on("ReportPlacement")
    assert said.rejection_reason == ops.REJECTION_REASON_VENUE_REJECTED
    assert said.rejection_as_reported.code == "INSUFFICIENT_MARGIN"
    assert said.rejection_as_reported.scheme == "oanda:reject-reason"


def test_an_unanswered_send_is_asked_about_and_never_sent_again() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(quantity=f"{synthetic.LOST}")
        rig.holds(held)
        await rig.hear(held)
        assert events(recorder) == ["PLACEMENT_EVENT_OUTCOME_UNKNOWN"]
        # Started again before anything was learnt: the book still holds the
        # placement queued, and it is asked about, never sent again.
        rig.trader = rig.make()
        await rig.trader.start()
        await rig.trader.poll()
        assert rig.venue.client_ids().count(held.placement_id) == 1

    run(work())
    assert events(recorder)[-1] == "PLACEMENT_EVENT_ACKNOWLEDGED"


def test_a_cancel_and_a_replace_the_ems_asks_for() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(quantity=f"{synthetic.WORKS}")
        rig.holds(held)
        await rig.hear(held)
        first = rig.venue.orders()[0]["id"]
        working = placement(
            quantity=f"{synthetic.WORKS}",
            state=ops.PLACEMENT_STATE_WORKING,
            venue_order_id=first,
        )
        replace = asking(
            working, ops.PLACEMENT_CHANGE_KIND_REPLACE, limit_price=money("1.07500")
        )
        rig.holds(replace)
        await rig.hear(replace)
        orders = rig.venue.orders()
        assert [o["state"] for o in orders] == ["CANCELLED", "PENDING"]
        assert orders[1]["replacesOrderID"] == first
        assert orders[1]["price"] == "1.07500"
        assert orders[1]["clientExtensions"]["id"] == f"{held.placement_id}.r1"
        # A replace to at the open, which OANDA's order request has no time
        # in force for: refused unsupported, the placement working on.
        odd = asking(
            working,
            ops.PLACEMENT_CHANGE_KIND_REPLACE,
            "CRQ-2",
            time_in_force=ops.TIME_IN_FORCE_AT_THE_OPEN,
        )
        rig.holds(odd)
        await rig.hear(odd)
        cancel = asking(working, ops.PLACEMENT_CHANGE_KIND_CANCEL, "CRQ-3")
        rig.holds(cancel)
        await rig.hear(cancel)
        assert [o["state"] for o in rig.venue.orders()] == ["CANCELLED", "CANCELLED"]

    run(work())
    said = recorder.on("ReportPlacement")
    assert [ops.PlacementEvent.Name(p.event) for p in said] == [
        "PLACEMENT_EVENT_ACKNOWLEDGED",
        "PLACEMENT_EVENT_REPLACED",
        "PLACEMENT_EVENT_REJECTED",
        "PLACEMENT_EVENT_CANCELLED",
    ]
    assert said[1].venue_order_id != said[0].venue_order_id
    assert said[2].rejection_reason == ops.REJECTION_REASON_UNSUPPORTED


def test_a_pause_holds_a_send_and_a_stop_cancels_at_oanda() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
        held = placement(quantity=f"{synthetic.WORKS}")
        rig.holds(held)
        await rig.hear(held)
        assert rig.venue.orders() == []
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_ACTIVE)
        assert len(rig.venue.orders()) == 1
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_STOP)
        assert rig.venue.orders()[0]["state"] == "CANCELLED"

    run(work())
    (change,) = recorder.on("RequestPlacementChange")
    assert change.cause.entry_id == "ENT-stop-1"
    assert events(recorder)[-1] == "PLACEMENT_EVENT_CANCELLED"


def test_each_rolls_financing_is_a_charge_on_the_placement_that_opened_the_trade() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement()
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()
        rig.venue.roll()
        await rig.trader.poll()
        # Read again from a cursor before the roll: charged once.
        rig.trader = rig.make()
        await rig.trader.start()
        await rig.trader.poll()

    run(work())
    (charge,) = recorder.on("ReportCharge")
    assert charge.placement_id == "PLC-01JAE00000000000000000001"
    # OANDA's financing of the 10,000 EUR long, paid: a fee, positive.
    assert as_decimal(charge.fee.amount.amount) == Decimal("0.2174")
    assert charge.fee.amount.currency_code == "USD"
    assert charge.trade_date == charge.settlement_date == "2026-10-13"
    # The trade opened outside the platform is financed too, and charged to
    # no placement.


def test_a_fill_no_placement_names_is_left_alone() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        # An order taken in OANDA's own platform, after the plugin started.
        await rig.venue.place(
            ACCOUNT,
            {
                "order": {
                    "type": "MARKET",
                    "instrument": "EUR_USD",
                    "units": "500",
                    "timeInForce": "FOK",
                }
            },
        )
        await rig.trader.poll()

    run(work())
    assert recorder.on("ReportExecution") == []
    assert recorder.on("ReportPlacement") == []


def test_good_till_date_ends_at_oandas_day_end() -> None:
    async def work() -> None:
        rig = TradeRig(Recorder("oanda"))
        await rig.started()
        held = placement(tif=ops.TIME_IN_FORCE_GOOD_TILL_DATE)
        rig.holds(held, expire_date="2026-10-30")
        await rig.hear(held)
        (order,) = rig.venue.orders()
        # 17:00 New York on the 30th, daylight saving: 21:00 UTC.
        assert order["gtdTime"] == "2026-10-30T21:00:00.000000000Z"

    run(work())


def test_the_derived_execution_id() -> None:
    assert execution_id("101-001-1-001", "42") == "101-001-1-001:42"
