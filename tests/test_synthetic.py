"""The synthetic OANDA (synthetic.py): its account, its scenarios, and that
it keeps its state across a restart. It reaches nothing."""

from __future__ import annotations

import asyncio
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
