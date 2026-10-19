"""The connectivity work (trading.py) against the synthetic OANDA: what is
sent to OANDA, and what is reported to the book; from contract v23 the
value-date roll, a market order's quote currency, and fills of orders taken
in OANDA's own app. SIMULATED ONLY."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from decimal import Decimal

from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from oanda import moments, synthetic
from oanda.trading import execution_id

from rigs import (
    ACCOUNT,
    EUR,
    USD,
    TradeRig,
    asking,
    computed,
    money,
    not_held,
    placement,
    spot_of,
)

#: Tuesday the 13th's 17:00 New York, daylight saving: 21:00 UTC.
CUT_13 = "2026-10-13T21:00:00.000000000Z"


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


def test_a_market_order_names_its_quote_currency_and_is_sent() -> None:
    """Contract v23: the order names the pair's quote currency
    (Order.quote_currency), so a market order, with no price, names EUR_USD."""
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(limit=None, tif=ops.TIME_IN_FORCE_FILL_OR_KILL)
        rig.holds(held, order_type=ops.ORDER_TYPE_MARKET, quote_currency=USD)
        await rig.hear(held)
        (order,) = rig.venue.orders()
        assert order["type"] == "MARKET" and order["instrument"] == "EUR_USD"
        assert order["timeInForce"] == "FOK" and "price" not in order
        assert order["state"] == "FILLED"

    run(work())
    assert events(recorder) == ["PLACEMENT_EVENT_ACKNOWLEDGED"]
    (fill,) = recorder.on("ReportExecution")
    assert fill.placement_id == "PLC-01JAE00000000000000000001"
    assert as_decimal(fill.price.amount) == Decimal("1.08720")
    assert fill.price.currency_code == "USD"
    assert fill.computed_settlement_date == "2026-10-15"


def test_a_market_order_naming_no_quote_currency_is_rejected_unsupported() -> None:
    """An order naming no quote currency and no price names one currency
    only: nothing is sent."""
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(limit=None, tif=ops.TIME_IN_FORCE_FILL_OR_KILL)
        rig.holds(held, order_type=ops.ORDER_TYPE_MARKET)
        await rig.hear(held)
        assert rig.venue.orders() == []
        record = rig.raw.find(recorder.on("ReportPlacement")[0].raw_record.key)
        assert record is not None and "quote_currency" in json.dumps(record.document)

    run(work())
    assert reasons(recorder) == ["REJECTION_REASON_UNSUPPORTED"]
    kept = recorder.on("ReportPlacement")[0].raw_record.key
    assert kept.startswith(f"trading/{ACCOUNT}/") and kept.endswith("/offered")


def test_a_price_in_another_currency_than_the_quote_currency_is_not_sent() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(limit="0.85", quote="GBP")
        rig.holds(held, quote_currency=USD)
        await rig.hear(held)
        assert rig.venue.orders() == []

    run(work())
    assert reasons(recorder) == ["REJECTION_REASON_UNSUPPORTED"]


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


def rolls(recorder: Recorder) -> list[tuple[str, str, list[str], str]]:
    return [
        (r.from_value_date, r.to_value_date, list(r.charge_ids), r.idempotency_key)
        for r in recorder.on("ReportValueDateRoll")
    ]


def test_each_roll_charges_its_financing_and_moves_the_value_date() -> None:
    """OANDA's 17:00 New York roll (W11.25): the trade's financing a charge
    of kind financing on the placement that opened it, then the pair's
    pending lines rolled from the fill's value date to the date read's
    answer for the roll's moment, linked to the charge."""
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
        # Read again from a cursor before the roll: charged and rolled once.
        rig.trader = rig.make()
        await rig.trader.start()
        await rig.trader.poll()

    run(work())
    (charge,) = recorder.on("ReportCharge")
    assert charge.placement_id == "PLC-01JAE00000000000000000001"
    assert charge.fee.kind == ops.FEE_KIND_FINANCING
    # OANDA's financing of the 10,000 EUR long, paid: a fee, positive.
    assert as_decimal(charge.fee.amount.amount) == Decimal("0.2174")
    assert charge.fee.amount.currency_code == "USD"
    # The roll is at 17:00 New York, OANDA's next trading day's start.
    assert charge.trade_date == charge.settlement_date == "2026-10-14"
    (roll,) = recorder.on("ReportValueDateRoll")
    assert roll.venue_account_code == ACCOUNT
    assert list(roll.pair) == [EUR, USD]
    assert (roll.from_value_date, roll.to_value_date) == ("2026-10-15", "2026-10-16")
    assert roll.rolled_at_ns == moments.ns(CUT_13)
    assert list(roll.charge_ids) == ["CHG-1"]
    assert roll.value_date_basis.rule.kind == ops.DATE_RULE_KIND_SPOT
    assert roll.raw_record.key.endswith("/financing")
    # The roll's date is the date read's, asked at the roll's moment.
    asked = [c for c in recorder.on("ComputeDate") if c.spot.at_ns == roll.rolled_at_ns]
    assert asked and list(asked[0].spot.pair) == [EUR, USD]
    # The trade opened outside the platform before the plugin started is
    # financed too, and charged to nothing: it is the account's history.


def test_rolls_follow_each_other_and_a_wednesday_is_financed_three_days() -> None:
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
        # Wednesday: its 17:00 rolls Friday's value date over the weekend.
        rig.clock.at += timedelta(days=1)
        rig.venue.roll()
        await rig.trader.poll()

    run(work())
    assert [r[:3] for r in rolls(recorder)] == [
        ("2026-10-15", "2026-10-16", ["CHG-1"]),
        ("2026-10-16", "2026-10-19", ["CHG-2"]),
    ]
    amounts = [as_decimal(c.fee.amount.amount) for c in recorder.on("ReportCharge")]
    assert amounts == [Decimal("0.2174"), Decimal("0.6523")]


def test_a_roll_waits_for_its_date_never_guessed() -> None:
    recorder = Recorder("oanda")
    answers = {"held": True}

    def dates(params: ops.ComputeDateParams) -> ops.ComputeDateResult:
        return spot_of(params) if answers["held"] else not_held()

    async def work() -> None:
        rig = TradeRig(recorder, dates=dates)
        await rig.started()
        held = placement()
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()
        answers["held"] = False
        rig.venue.roll()
        await rig.trader.poll()
        assert recorder.on("ReportValueDateRoll") == [], "never a guessed date"
        assert len(rig.kept["rolling"].value["waiting_rolls"]) == 1
        # Started again: the waiting roll is kept in storage.
        rig.trader = rig.make()
        await rig.trader.start()
        answers["held"] = True
        await rig.trader.poll()
        assert rig.kept["rolling"].value["waiting_rolls"] == []

    run(work())
    assert [r[:3] for r in rolls(recorder)] == [("2026-10-15", "2026-10-16", ["CHG-1"])]


def test_a_fill_reported_after_the_roll_is_caught_up() -> None:
    """A fill waiting for its value date while OANDA rolled its trade: its
    financing held, then charged once the fill is reported, and a catch-up
    roll from the date it was recorded with to the date read's answer for
    the roll's moment (W11.25, approved 2026-10-13)."""
    recorder = Recorder("oanda")
    answers = {"held": False}

    def dates(params: ops.ComputeDateParams) -> ops.ComputeDateResult:
        return spot_of(params) if answers["held"] else not_held()

    async def work() -> None:
        rig = TradeRig(recorder, dates=dates)
        await rig.started()
        held = placement()
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()
        rig.venue.roll()
        await rig.trader.poll()
        assert recorder.on("ReportExecution") == []
        assert recorder.on("ReportCharge") == [], "held while its fill waits"
        answers["held"] = True
        await rig.trader.poll()

    run(work())
    (fill,) = recorder.on("ReportExecution")
    assert fill.computed_settlement_date == "2026-10-15"
    (charge,) = recorder.on("ReportCharge")
    assert charge.placement_id == fill.placement_id
    (roll,) = recorder.on("ReportValueDateRoll")
    assert (roll.from_value_date, roll.to_value_date) == ("2026-10-15", "2026-10-16")
    assert roll.rolled_at_ns == moments.ns(CUT_13)
    assert list(roll.charge_ids) == ["CHG-1"]
    assert roll.idempotency_key.startswith(f"catch-up:{ACCOUNT}:")


def test_a_fill_after_the_roll_is_not_caught_up() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        first = placement()
        rig.holds(first)
        await rig.hear(first)
        await rig.trader.poll()
        rig.venue.roll()
        await rig.trader.poll()
        rig.clock.at += timedelta(hours=8)
        second = placement("PLC-01JAE00000000000000000002")
        rig.holds(second)
        await rig.hear(second)
        await rig.trader.poll()

    run(work())
    assert [f.computed_settlement_date for f in recorder.on("ReportExecution")] == [
        "2026-10-15",
        "2026-10-16",
    ]
    assert [r[:2] for r in rolls(recorder)] == [("2026-10-15", "2026-10-16")]


def test_an_order_taken_in_oandas_app_is_reported_on_no_placement() -> None:
    """Contract v23: a fill of an order no placement names, reported on no
    placement with its instrument, side and OANDA's order ID, for the ems to
    accept or answer DK; no placement event."""
    recorder = Recorder("oanda")
    held: dict[str, str] = {}

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held["order"] = rig.venue.in_the_app("EUR_USD", Decimal(-500))
        await rig.trader.poll()
        # Read again: reported once.
        rig.trader = rig.make()
        await rig.trader.start()
        await rig.trader.poll()

    run(work())
    (fill,) = recorder.on("ReportExecution")
    assert fill.placement_id == "" and fill.venue_account_code == ACCOUNT
    assert fill.instrument_id == EUR and fill.side == ops.SIDE_SELL
    assert fill.venue_order_id == held["order"] and fill.venue_client_order_id == ""
    assert as_decimal(fill.quantity) == 500
    assert fill.price.currency_code == "USD"
    assert fill.computed_settlement_date == "2026-10-15"
    assert recorder.on("ReportPlacement") == []


def test_an_accept_after_a_roll_charges_again_and_catches_up() -> None:
    """The app's order rolled before its ems accepted it: the financing kept
    unplaced, then reported again on the placement the accept created, and a
    catch-up roll for its lines; the trade's next financing is charged to
    the placement, and its lines roll with the pair's."""
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        order_id = rig.venue.in_the_app("EUR_USD", Decimal(2000))
        await rig.trader.poll()
        rig.venue.roll()
        await rig.trader.poll()
        assert recorder.on("ReportValueDateRoll") == [], "nothing in the book to roll"
        made = placement("PLC-01JAE0000000000000000ACC", venue_order_id=order_id, filled=True)
        rig.holds(made)
        await rig.hear(made)
        await rig.hear(made)
        rig.clock.at += timedelta(days=1)
        rig.venue.roll()
        await rig.trader.poll()

    run(work())
    charges = recorder.on("ReportCharge")
    assert [c.placement_id for c in charges] == [
        "",
        "PLC-01JAE0000000000000000ACC",
        "PLC-01JAE0000000000000000ACC",
    ]
    assert all(c.fee.kind == ops.FEE_KIND_FINANCING for c in charges)
    assert charges[0].fee == charges[1].fee and charges[0].raw_record == charges[1].raw_record
    assert [r[:3] for r in rolls(recorder)] == [
        ("2026-10-15", "2026-10-16", ["CHG-1"]),
        ("2026-10-16", "2026-10-19", ["CHG-2"]),
    ]
    assert rolls(recorder)[0][3].startswith("catch-up:PLC-01JAE0000000000000000ACC:")


def test_an_accept_before_any_roll_needs_no_catch_up() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        order_id = rig.venue.in_the_app("EUR_USD", Decimal(2000))
        await rig.trader.poll()
        made = placement("PLC-01JAE0000000000000000ACC", venue_order_id=order_id, filled=True)
        rig.holds(made)
        await rig.hear(made)
        rig.venue.roll()
        await rig.trader.poll()

    run(work())
    (charge,) = recorder.on("ReportCharge")
    assert charge.placement_id == "PLC-01JAE0000000000000000ACC"
    assert [r[:3] for r in rolls(recorder)] == [("2026-10-15", "2026-10-16", ["CHG-1"])]


def test_a_commission_is_a_fee_of_kind_commission() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        rig.venue._fill_now(
            "EUR_USD",
            Decimal(1000),
            Decimal("1.08700"),
            rig.venue._clock.text(),
            "MARKET",
            commission=Decimal("0.50"),
        )
        await rig.trader.poll()

    run(work())
    (fill,) = recorder.on("ReportExecution")
    (fee,) = fill.fees
    assert fee.kind == ops.FEE_KIND_COMMISSION
    assert as_decimal(fee.amount.amount) == Decimal("0.50")


def test_a_metal_taken_in_oandas_app_is_not_reported() -> None:
    """XAU_USD is OANDA's metal, not a currency pair: not this plugin's."""
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        rig.venue.in_the_app("XAU_USD", Decimal(1))
        await rig.trader.poll()

    run(work())
    assert recorder.on("ReportExecution") == []


def test_the_catalogue_says_oanda_takes_no_execution_acknowledgement() -> None:
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.trader.start()

    run(work())
    (catalogue,) = recorder.on("ReportVenueCatalogue")
    assert catalogue.catalogue.execution_acknowledgement == ops.EXECUTION_ACKNOWLEDGEMENT_NONE


def test_the_catalogue_carries_the_environment_the_session_reached() -> None:
    """Contract v26, G1 question 5: the agents' guard's second key. The
    synthetic OANDA reaches no venue and reports `paper`; OANDA's practice
    host is `paper` and its live host `live` (test_venue.py)."""
    recorder = Recorder("oanda")

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.trader.start()
        await rig.trader.publish()  # unchanged: published once

    run(work())
    (catalogue,) = recorder.on("ReportVenueCatalogue")
    assert catalogue.catalogue.environment == "paper"


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
