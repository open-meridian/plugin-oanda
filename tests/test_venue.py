"""OANDA's v20 client (venue.py), handed an opener that records each request
and answers it in process: no request reaches a network, OANDA's practice
host or its live one."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any

import pytest

from oanda.venue import Oanda, VenueError

TOKEN = "synthetic-token-not-a-real-one-0123456789"


class Answer(io.BytesIO):
    status = 200


class Opener:
    """Records each request; answers with `body`, or raises `fail`."""

    def __init__(self, body: Any = None, fail: BaseException | None = None) -> None:
        self.body = body if body is not None else {}
        self.fail = fail
        self.asked: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Answer:
        self.asked.append(request)
        if self.fail is not None:
            raise self.fail
        return Answer(json.dumps(self.body).encode())


def test_a_request_names_the_environments_host_and_the_token_only_in_its_header() -> None:
    opener = Opener({"order": {"id": "7", "state": "PENDING"}, "lastTransactionID": "7"})
    venue = Oanda(TOKEN, "practice", opener)
    asyncio.run(venue.order("101-001-1-001", "@PLC-1"))
    (asked,) = opener.asked
    assert (
        asked.full_url
        == "https://api-fxpractice.oanda.com/v3/accounts/101-001-1-001/orders/@PLC-1"
    )
    assert asked.get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in asked.full_url
    assert TOKEN not in repr(venue)


def test_the_live_host_for_live() -> None:
    opener = Opener({"accounts": [{"id": "001-001-1-001"}]})
    asyncio.run(Oanda(TOKEN, "live", opener).accounts())
    assert opener.asked[0].full_url == "https://api-fxtrade.oanda.com/v3/accounts"


def test_an_order_is_posted_as_json() -> None:
    opener = Opener({"orderCreateTransaction": {"id": "9"}})
    body = {"order": {"type": "LIMIT", "instrument": "EUR_USD", "units": "10", "price": "1.1"}}
    asyncio.run(Oanda(TOKEN, "practice", opener).place("101-001-1-001", body))
    (asked,) = opener.asked
    assert asked.get_method() == "POST"
    assert json.loads(asked.data or b"") == body  # type: ignore[arg-type]


def test_numbers_are_read_exactly() -> None:
    opener = Opener()
    opener.body = None
    answer = Answer(b'{"lastTransactionID": "3", "x": 0.1}')
    venue = Oanda(TOKEN, "practice", lambda request, timeout: answer)
    said = asyncio.run(venue.account("101-001-1-001"))
    assert said["x"] == Decimal("0.1")


def test_a_refusal_says_its_status_and_code_never_the_token() -> None:
    body = io.BytesIO(
        json.dumps(
            {"errorCode": "INVALID_AUTHORIZATION", "errorMessage": "Insufficient authorization"}
        ).encode()
    )
    refused = urllib.error.HTTPError("u", 401, "Unauthorized", {}, body)  # type: ignore[arg-type]
    venue = Oanda(TOKEN, "practice", Opener(fail=refused))
    with pytest.raises(VenueError) as failed:
        asyncio.run(venue.account("101-001-1-001"))
    assert failed.value.answered and failed.value.unauthorised
    assert "INVALID_AUTHORIZATION" in str(failed.value)
    assert TOKEN not in str(failed.value)


def test_a_call_never_answered_has_no_status() -> None:
    venue = Oanda(TOKEN, "practice", Opener(fail=urllib.error.URLError("down")))
    with pytest.raises(VenueError) as failed:
        asyncio.run(venue.place("101-001-1-001", {"order": {}}))
    assert not failed.value.answered


def test_an_unknown_environment_is_refused() -> None:
    with pytest.raises(ValueError):
        Oanda(TOKEN, "staging", Opener())


def test_the_environment_the_session_reaches_is_read_from_oandas_host() -> None:
    """Contract v26, G1 question 5: api-fxpractice is `paper`, api-fxtrade
    `live`; the setting's two hosts and nothing else."""
    assert Oanda(TOKEN, "practice", Opener()).environment == "paper"
    assert Oanda(TOKEN, "live", Opener()).environment == "live"


def test_prices_candles_and_the_pricing_stream_are_asked_of_the_account() -> None:
    opener = Opener()
    venue = Oanda(TOKEN, "practice", opener)
    asyncio.run(venue.pricing("101-001-1-001", ["EUR_USD", "USD_JPY"]))
    asyncio.run(
        venue.candles(
            "101-001-1-001", "EUR_USD", "2026-10-12T21:00:00Z", "2026-10-13T21:00:00Z"
        )
    )
    asked = [request.full_url for request in opener.asked]
    assert asked[0] == (
        "https://api-fxpractice.oanda.com/v3/accounts/101-001-1-001/pricing"
        "?instruments=EUR_USD%2CUSD_JPY"
    )
    assert asked[1].startswith(
        "https://api-fxpractice.oanda.com/v3/accounts/101-001-1-001/instruments/EUR_USD/candles"
        "?price=M&granularity=D&from=2026-10-12T21%3A00%3A00Z"
    )
    assert all(TOKEN not in url for url in asked)
