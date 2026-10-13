"""The custody work (custody.py): OANDA's transactions as activities, the
account's kind, the statement's legs, and the sync states. SIMULATED ONLY."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from oanda.custody import account_kind, activities_of

from rigs import CustodyRig, not_held


def test_a_fill_is_a_purchase_its_fee_its_own_and_a_yen_profit_its_own_conversion() -> None:
    fill = {
        "id": "9",
        "type": "ORDER_FILL",
        "time": "2026-06-03T15:00:00.000000000Z",
        "instrument": "USD_JPY",
        "units": "-5000",
        "fullVWAP": "150.000",
        "pl": "33.3734",
        "quotePL": "5000.00000",
        "commission": "0.50",
        "financing": "-0.1200",
    }
    made = {each.external_activity_id: each for each in activities_of(fill, "USD")}
    assert made["9"].kind == "ACTIVITY_KIND_SALE" and made["9"].units == Decimal(-5000)
    assert made["9"].amount is not None and made["9"].amount.amount == Decimal("750000.000")
    assert made["9"].amount.currency_code == "JPY" and made["9"].code == "USD"
    assert made["9:commission"].kind == "ACTIVITY_KIND_FEE"
    assert made["9:commission"].amount is not None
    assert made["9:commission"].amount.amount == Decimal("-0.50")
    conversion = made["9:conversion"]
    assert conversion.kind == "ACTIVITY_KIND_JOURNAL" and conversion.code == "JPY"
    assert conversion.units == Decimal("-5000.00000")
    assert conversion.amount is not None and conversion.amount.amount == Decimal("33.3734")
    assert made["9:financing"].kind == "ACTIVITY_KIND_INTEREST"


def test_a_profit_already_in_the_accounts_currency_needs_no_conversion() -> None:
    fill = {
        "id": "5",
        "type": "ORDER_FILL",
        "time": "2026-06-03T15:00:00.000000000Z",
        "instrument": "EUR_USD",
        "units": "-10000",
        "fullVWAP": "1.09",
        "pl": "50.0000",
        "quotePL": "50.00000",
    }
    assert [a.external_activity_id for a in activities_of(fill, "USD")] == ["5"]


def test_each_rolls_financing_is_its_own_interest() -> None:
    financing = {
        "id": "20",
        "type": "DAILY_FINANCING",
        "time": "2026-10-13T21:00:00.000000000Z",
        "financing": "-0.6522",
    }
    (made,) = activities_of(financing, "USD")
    assert made.kind == "ACTIVITY_KIND_INTEREST"
    # 17:00 New York is the next trading day's.
    assert made.trade_date == "2026-10-14"


def test_funds_by_their_reason() -> None:
    def kind(reason: str, amount: str) -> str:
        (made,) = activities_of(
            {
                "id": "1",
                "type": "TRANSFER_FUNDS",
                "amount": amount,
                "fundingReason": reason,
                "time": "2026-06-01T14:00:00Z",
            },
            "USD",
        )
        return made.kind

    assert kind("CLIENT_FUNDING", "100") == "ACTIVITY_KIND_CONTRIBUTION"
    assert kind("CLIENT_FUNDING", "-100") == "ACTIVITY_KIND_WITHDRAWAL"
    assert kind("ACCOUNT_TRANSFER", "100") == "ACTIVITY_KIND_TRANSFER_IN"
    assert kind("ACCOUNT_TRANSFER", "-100") == "ACTIVITY_KIND_TRANSFER_OUT"
    assert kind("DIVISION_MIGRATION", "100") == "ACTIVITY_KIND_UNSPECIFIED"


def test_a_transaction_that_moved_no_cash_is_no_activity() -> None:
    assert activities_of({"id": "3", "type": "LIMIT_ORDER", "time": ""}, "USD") == []


def test_the_accounts_kind_from_its_margin_rate() -> None:
    assert account_kind({"marginRate": "0.02"}) == ("ACCOUNT_KIND_MARGIN", None)
    kind, said = account_kind({"marginRate": "1"})
    assert kind == "ACCOUNT_KIND_UNSPECIFIED" and said is not None and said.code == "1"


def test_a_row_whose_value_date_the_date_read_cannot_answer_states_no_split() -> None:
    """The street takes no pending quantity without its date, and none is
    guessed: the settled quantity is left unset, nothing pending."""
    recorder = Recorder("oanda")
    rig = CustodyRig(recorder, dates=lambda _: not_held())
    asyncio.run(rig.syncer.run_once())
    eur = next(
        h
        for h in recorder.on("RecordHolding")
        if h.instrument_id == "INS-01JEUR00000000000000000001"
    )
    assert as_decimal(eur.quantity) == Decimal(10000)
    assert not eur.HasField("settle_date_quantity") and list(eur.pending) == []


def test_a_read_with_nothing_new_opens_no_second_statement() -> None:
    recorder = Recorder("oanda")
    rig = CustodyRig(recorder)
    asyncio.run(rig.syncer.run_once())
    asyncio.run(rig.syncer.run_once())
    ids = {s.external_statement_id for s in recorder.on("RecordHoldingsStatement")}
    assert len(ids) == 1


def test_an_unlinked_account_is_reported_but_never_its_rows() -> None:
    recorder = Recorder("oanda")
    rig = CustodyRig(recorder, linked=False)
    asyncio.run(rig.syncer.run_once())
    assert recorder.on("ReportExternalAccounts") and recorder.on("ReportSyncStatus")
    assert recorder.on("RecordHoldingsStatement") == []
    assert recorder.on("RecordActivity") == []
    (sync,) = recorder.on("ReportSyncStatus")
    assert sync.state == ops.SYNC_STATE_CURRENT and sync.history_from == "2026-06-01"
