"""The connectivity suite (contract v22): every case OANDA presents, from
this plugin's own Trader (trading.py) and the synthetic OANDA (synthetic.py),
only the sidecar stood in for. SIMULATED ONLY: nothing reaches OANDA.

Each case is mapped to what OANDA's v20 API answers for it, in OANDA's own
terms (`PRODUCERS`), or marked not presented with why where the suite lets a
case be (`NOT_PRESENTED`: a case `about` a kind of work or a field OANDA
never states).

Two required cases cannot be presented by a spot FX plugin on OANDA, and
are held here as the suite's gap, each with why (`GAPS`), reported for the
product owner rather than gamed:

- `reports-a-fill-with-its-settlement-date` expects the fill's date read
  with *settlement* inputs (an instrument's settlement-date rule). A spot FX
  fill's value date is the pair's spot date, asked with the spot inputs
  (W3.20, `SpotDateInputs`): the plugin asks that, and its ReportExecution
  carries the computed date and its basis, which the case's second row
  checks and passes.
- `reports-a-bust`: OANDA's v20 transactions have no bust or trade
  correction, so a fill is never taken back and there is nothing to report.
"""

from __future__ import annotations

import asyncio

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder, names, run, suite

from oanda import synthetic

from rigs import Producer, TradeRig, placement

NOT_PRESENTED = {
    "reads-a-calendar": (
        "OANDA's fills take their value date from the date read (ComputeDate, the pair's "
        "spot-date rule); the plugin reads no calendar's days"
    ),
    "records-a-locate": "OANDA offers no locate: a currency is sold, never borrowed to sell",
    "resolves-a-venue-symbol-by-the-facts-date": (
        "OANDA offers no locate, so it resolves no locate's symbol"
    ),
    "resolves-an-executions-venue": (
        "OANDA is the counterparty to every fill and states no place of execution"
    ),
    "reports-a-venue-not-held": (
        "OANDA is the counterparty to every fill and states no place of execution"
    ),
    "reads-whether-releases-wait-for-compliance": (
        "it places what its ems released; whether releases wait for compliance changes "
        "nothing it sends"
    ),
    "hears-an-accounts-attributes-change": (
        "it places what its ems released; whether releases wait for compliance changes "
        "nothing it sends"
    ),
}

#: Required cases a spot FX plugin on OANDA cannot present, and why: the
#: suite's gap, reported (above).
GAPS = {
    "reports-a-fill-with-its-settlement-date": (
        "a spot FX fill's value date is asked of the date read with the spot inputs (the "
        "pair's spot-date rule), not the settlement inputs the case expects"
    ),
    "reports-a-bust": "OANDA's v20 transactions have no bust or correction",
}


async def acknowledged(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.started()
    held = placement(quantity=f"{synthetic.WORKS}")
    rig.holds(held)
    await rig.hear(held)


async def another_instances(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.started()
    held = placement(instance="elsewhere")
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()


async def not_offered(recorder: Recorder) -> None:
    """A stop-limit order, which OANDA's order request has no type for."""
    rig = TradeRig(recorder)
    await rig.started()
    held = placement()
    rig.holds(held, order_type=ops.ORDER_TYPE_STOP_LIMIT)
    await rig.hear(held)


async def a_strategy(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.started()
    held = placement(strategy=ops.StrategyInstruction(kind=ops.STRATEGY_KIND_TWAP))
    rig.holds(held)
    await rig.hear(held)


def by_units(units: str, polls: int = 0) -> Producer:
    async def produce(recorder: Recorder) -> None:
        rig = TradeRig(recorder)
        await rig.started()
        held = placement(quantity=units)
        rig.holds(held)
        await rig.hear(held)
        for _ in range(polls):
            await rig.trader.poll()

    return produce


async def filled(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.started()
    held = placement()
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()


async def the_same_fill_twice(recorder: Recorder) -> None:
    """A fill taken, then the plugin started again with its cursor before
    the fill and the book holding none of it: the same derived ID twice."""
    rig = TradeRig(recorder)
    await rig.started()
    held = placement()
    rig.holds(held)
    rig.executions.clear()
    before = dict(rig.kept["cursor"].value)
    await rig.hear(held)
    await rig.trader.poll()
    worked = next(iter(rig.trader.working.values()))
    again = placement(state=ops.PLACEMENT_STATE_WORKING, venue_order_id=worked.oanda_order_id)
    rig.holds(again)
    rig.executions.clear()
    rig.kept["cursor"].value = before
    rig.kept["cursor"].save()
    rig.trader = rig.make()
    await rig.trader.start()
    await rig.trader.poll()


async def charged(recorder: Recorder) -> None:
    """Filled, then rolled at 17:00 New York: the trade's financing, a
    charge on the placement that opened it."""
    rig = TradeRig(recorder)
    await rig.started()
    held = placement()
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()
    rig.venue.roll()
    await rig.trader.poll()


async def unknown_symbol(recorder: Recorder) -> None:
    """A placement priced in a currency the deployment holds no cash
    instrument for: OANDA's pair names it, and it meets none."""
    rig = TradeRig(recorder)
    recorder.answer(
        "ResolveIdentifier",
        lambda _: ops.ResolveIdentifierResult(
            found=False, miss_reason=ops.MISS_REASON_NOT_FOUND
        ),
    )
    await rig.started()
    held = placement()
    rig.holds(held)
    await rig.hear(held)


async def paused(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.started()
    await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
    held = placement()
    rig.holds(held)
    await rig.hear(held)


async def stopped(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.started()
    held = placement(quantity=f"{synthetic.WORKS}")
    rig.holds(held)
    await rig.hear(held)
    await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_STOP)


async def started(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.trader.start()


async def caught_up(recorder: Recorder) -> None:
    rig = TradeRig(recorder)
    await rig.trader.start()
    await rig.trader.poll()


PRODUCERS: dict[str, Producer] = {
    "acknowledges-a-placement-naming-it": acknowledged,
    "hears-no-placement-of-another-instance": another_instances,
    "sends-the-venue-account-code-its-ems-set": acknowledged,
    "rejects-what-the-venue-does-not-offer": not_offered,
    "rejects-a-strategy-its-broker-lacks": a_strategy,
    "venue-rejected-with-its-reason-as-reported": by_units(f"{synthetic.REJECTED}"),
    "unmapped-status-is-outcome-unknown": by_units(f"{synthetic.ODD}"),
    "resolves-an-unanswered-send-by-asking": by_units(f"{synthetic.LOST}", 1),
    "reports-a-fill-with-its-settlement-date": filled,
    "derives-the-same-execution-id-twice": the_same_fill_twice,
    "reports-a-charge": charged,
    "sends-nothing-new-on-a-pause": paused,
    "cancels-at-the-venue-on-a-stop": stopped,
    "publishes-its-catalogue": started,
    "reads-the-instrument-it-places": acknowledged,
    "reads-its-placements-orders-on-start": started,
    "catches-up-placements-after-a-loss": caught_up,
    "reads-the-instructions-in-force-on-start": started,
    "reports-a-symbol-it-cannot-resolve": unknown_symbol,
}


def test_every_case_is_mapped_said_not_presented_or_held_as_a_gap() -> None:
    every = set(names("connectivity"))
    assert every == set(PRODUCERS) | set(NOT_PRESENTED) | {"reports-a-bust"}
    assert not set(PRODUCERS) & set(NOT_PRESENTED)
    held = {case.name: case.about or case.about_field for case in suite("connectivity").cases}
    assert all(held[name] for name in NOT_PRESENTED), "a required case is never skipped"
    assert not any(held[name] for name in GAPS), "a gap is a required case"


def test_the_connectivity_suite_passes_but_for_its_two_gaps() -> None:
    report = run("connectivity", PRODUCERS, not_presented=NOT_PRESENTED, instance_id="oanda")
    assert set(report.failures) == set(GAPS), report.failures
    # The fill case fails on its date read's inputs alone: the spot inputs
    # are asked, and the execution carries the computed date and basis.
    assert "settlement" in report.failures["reports-a-fill-with-its-settlement-date"]
    assert "expect[0]" in report.failures["reports-a-fill-with-its-settlement-date"]
    assert "no producer" in report.failures["reports-a-bust"]
    assert len(report.passed_cases) == 18
    assert sorted(report.not_presented) == sorted(NOT_PRESENTED)


def test_the_fill_case_passes_its_execution_row() -> None:
    """The fill case's second row -- the execution with its quantity, price,
    computed date, basis and raw record -- holds."""
    recorder = Recorder("oanda")
    asyncio.run(filled(recorder))
    (execution,) = recorder.on("ReportExecution")
    assert execution.computed_settlement_date == "2026-10-15"
    assert execution.settlement_date == ""
    assert execution.settlement_date_basis.rule.subject == "EUR/USD"
    assert execution.raw_record.key
    (asked,) = recorder.on("ComputeDate")
    assert asked.HasField("spot") and list(asked.spot.pair) and asked.spot.at_ns


def test_a_plugin_sending_on_a_pause_fails_the_suite() -> None:
    async def ignoring(recorder: Recorder) -> None:
        rig = TradeRig(recorder)
        await rig.started()
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
        rig.trader.instructions.clear()
        held = placement()
        rig.holds(held)
        await rig.hear(held)

    report = run(
        "connectivity",
        {**PRODUCERS, "sends-nothing-new-on-a-pause": ignoring},
        not_presented=NOT_PRESENTED,
        instance_id="oanda",
    )
    assert "sends-nothing-new-on-a-pause" in report.failures
