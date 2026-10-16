"""The synthetic OANDA (synthetic.py): its account, its scenarios, and that
it keeps its state across a restart. It reaches nothing."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from oanda import synthetic
from oanda.synthetic import SyntheticOanda
from oanda.venue import VenueError

from conftest import NOW, Clock

ORDER = {
    "order": {
        "type": "LIMIT",
        "instrument": "EUR_USD",
        "units": "100",
        "price": "1.08",
        "timeInForce": "GTC",
        "clientExtensions": {"id": "PLC-1"},
    }
}


def test_its_history_holds_each_kind_the_plugin_converts() -> None:
    venue = SyntheticOanda(Clock(NOW))
    kinds = {t["type"] for t in venue.transactions()}
    assert {"CREATE", "TRANSFER_FUNDS", "ORDER_FILL", "DAILY_FINANCING"} <= kinds
    reasons = {t.get("fundingReason") for t in venue.transactions()}
    assert {"CLIENT_FUNDING", "ACCOUNT_TRANSFER", "ADJUSTMENT"} <= reasons
    assert venue.balance == Decimal("98733.8860")


def test_a_limit_order_works_then_fills_on_the_next_read() -> None:
    venue = SyntheticOanda(Clock(NOW))
    answer = asyncio.run(venue.place(synthetic.ACCOUNT_ID, ORDER))
    assert answer["orderCreateTransaction"]["type"] == "LIMIT_ORDER"
    asyncio.run(venue.transactions_since(synthetic.ACCOUNT_ID, "0"))
    assert venue.orders()[-1]["state"] == "PENDING"
    found, _ = asyncio.run(venue.transactions_since(synthetic.ACCOUNT_ID, "0"))
    assert found[-1]["type"] == "ORDER_FILL" and found[-1]["clientOrderID"] == "PLC-1"


def test_a_rejection_a_lost_answer_and_an_odd_state() -> None:
    venue = SyntheticOanda(Clock(NOW))
    with pytest.raises(VenueError) as rejected:
        asyncio.run(
            venue.place(synthetic.ACCOUNT_ID, {"order": {**ORDER["order"], "units": "13"}})
        )
    assert rejected.value.status == 400
    with pytest.raises(VenueError) as lost:
        asyncio.run(
            venue.place(
                synthetic.ACCOUNT_ID,
                {
                    "order": {
                        **ORDER["order"],
                        "units": "17",
                        "clientExtensions": {"id": "PLC-L"},
                    }
                },
            )
        )
    assert not lost.value.answered
    assert asyncio.run(venue.order(synthetic.ACCOUNT_ID, "@PLC-L"))["order"]["id"]
    asyncio.run(
        venue.place(
            synthetic.ACCOUNT_ID,
            {"order": {**ORDER["order"], "units": "19", "clientExtensions": {"id": "PLC-O"}}},
        )
    )
    first = asyncio.run(venue.order(synthetic.ACCOUNT_ID, "@PLC-O"))["order"]["state"]
    assert first == synthetic.ODD_STATE


def test_its_state_survives_a_restart(tmp_path: Path) -> None:
    state = synthetic.state_file(tmp_path)
    venue = SyntheticOanda(Clock(NOW), state)
    asyncio.run(venue.place(synthetic.ACCOUNT_ID, ORDER))
    again = SyntheticOanda(Clock(NOW), state)
    assert again.client_ids() == ["PLC-1"]


def test_another_account_is_refused() -> None:
    venue = SyntheticOanda(Clock(NOW))
    with pytest.raises(VenueError) as refused:
        asyncio.run(venue.account("001-001-9-001"))
    assert refused.value.status == 403


# ── A controlled clock (decision 66) ────────────────────────────────────────


def test_a_clock_asked_for_runs_the_accounts_times_from_it(tmp_path: Path) -> None:
    """The e2e sets the account's clock to a moment it chooses, so the trade
    dates reported never depend on the day it runs: each transaction after it
    is dated from that moment, at the wall clock's pace, kept across a
    restart."""
    wall = Clock(NOW)
    state = synthetic.state_file(tmp_path)
    venue = SyntheticOanda(wall, state)
    synthetic.ask(tmp_path, {"clock": "2026-10-20T16:00:00Z"})
    asyncio.run(venue.transactions_since(synthetic.ACCOUNT_ID, "0"))
    wall.at = NOW + timedelta(minutes=5)
    answer = asyncio.run(venue.place(synthetic.ACCOUNT_ID, ORDER))
    assert answer["orderCreateTransaction"]["time"].startswith("2026-10-20T16:05:00")
    again = SyntheticOanda(wall, state)
    assert again.trading_day().isoformat() == "2026-10-20"


def test_a_clock_is_never_set_back(tmp_path: Path) -> None:
    venue = SyntheticOanda(Clock(NOW), synthetic.state_file(tmp_path))
    assert not venue.set_clock("2026-10-01T16:00:00Z")
    assert not venue.set_clock("not a time")
    assert venue.set_clock("2026-10-20T16:00:00Z")
    assert not venue.set_clock("2026-10-19T16:00:00Z")


def test_setting_the_clock_rolls_nothing(tmp_path: Path) -> None:
    """The day the clock is set to counts as rolled: only the next 17:00 New
    York after it rolls the open trades."""
    wall = Clock(NOW)
    venue = SyntheticOanda(wall, synthetic.state_file(tmp_path))
    before = len(venue.transactions())
    assert venue.set_clock("2026-10-20T16:00:00Z")
    asyncio.run(venue.transactions_since(synthetic.ACCOUNT_ID, "0"))
    assert len(venue.transactions()) == before
    wall.at = NOW + timedelta(hours=6)
    asyncio.run(venue.transactions_since(synthetic.ACCOUNT_ID, "0"))
    assert {t["type"] for t in venue.transactions()[before:]} == {"DAILY_FINANCING"}
