"""The accounts listed again after OANDA fails to answer (connection.py, the
poll in __main__.py, and "List again" on Connections in page.py).

A server error (HTTP 5xx) or a call never answered is OANDA's to put right,
so the listing is asked again by itself, backing off to a bound; a refusal
(HTTP 4xx, such as a bad token) is not, and says so. OANDA is stood in for
by a venue that answers `GET /v3/accounts` as the test scripts it.
SIMULATED ONLY: nothing here reaches OANDA, practice or live, or any
network, and the token is a made-up string.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from oanda import connection
from oanda.__main__ import Oanda
from oanda.connection import Connections
from oanda.settings import ENVIRONMENT, PRACTICE, TOKEN, config_from
from oanda.venue import Json, VenueError
from test_account_choice import NOT_A_TOKEN, A, Listed, held

from conftest import NOW, Clock


class Scripted(Listed):
    """OANDA's account list, failing first as `failures` say, in order."""

    def __init__(self, ids: list[str], *failures: VenueError) -> None:
        super().__init__(ids)
        self.failures = list(failures)

    async def accounts(self) -> list[Json]:
        self.asked += 1
        if self.failures:
            raise self.failures.pop(0)
        return [{"id": each, "tags": []} for each in self.ids]


def server_error(status: int = 504) -> VenueError:
    return VenueError("listing accounts", status)


def unanswered() -> VenueError:
    return VenueError("listing accounts")


def connections(venue: Listed, clock: Clock) -> Connections:
    made = Connections(Path(tempfile.mkdtemp()), clock, lambda *_: venue)
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE}))
    return made


def test_a_server_error_is_listed_again_after_a_back_off() -> None:
    clock = Clock(NOW)
    venue = Scripted([A], server_error(504))
    made = connections(venue, clock)
    failed = asyncio.run(made.discover())
    assert "HTTP 504" in failed.error and failed.accounts == ()
    assert made.retry_in() == connection.RETRY_FIRST_SECONDS
    # The health says the listing is being retried, and when: not a dead end.
    waiting = made.waiting()
    assert "HTTP 504" in waiting and "listing the accounts again at" in waiting
    assert "choose the OANDA account" not in waiting
    clock.at += timedelta(seconds=connection.RETRY_FIRST_SECONDS)
    assert made.retry_in() == 0
    listed = asyncio.run(made.discover())
    assert [each.account_id for each in listed.accounts] == [A]
    assert listed.error == "" and made.retry_in() is None
    assert made.waiting() == "waiting for an admin to choose the OANDA account on Connections"


def test_repeated_server_errors_back_off_to_a_bound() -> None:
    clock = Clock(NOW)
    failures = [server_error(503), unanswered(), server_error(500)] * 4
    venue = Scripted([A], *failures)
    made = connections(venue, clock)
    waits = []
    for _ in failures:
        asyncio.run(made.discover())
        wait = made.retry_in()
        assert wait is not None
        waits.append(wait)
        clock.at += timedelta(seconds=wait)
    first, most = connection.RETRY_FIRST_SECONDS, connection.RETRY_MOST_SECONDS
    assert waits[:4] == [first, first * 2, first * 4, first * 8]
    assert max(waits) == most and waits[-3:] == [most, most, most]
    assert waits == sorted(waits), "never shorter while OANDA keeps failing"
    assert f"try {len(failures) + 1}" in made.waiting()
    assert NOT_A_TOKEN not in made.waiting()
    asyncio.run(made.discover())
    assert made.retry_in() is None and made.listing.attempts == 0


@pytest.mark.parametrize(
    ("status", "said"),
    [(401, "OANDA refused the access token"), (403, "OANDA refused the listing")],
)
def test_a_refusal_is_not_retried_and_says_so(status: int, said: str) -> None:
    clock = Clock(NOW)
    venue = Scripted([A], VenueError("listing accounts", status))
    made = connections(venue, clock)
    listing = asyncio.run(made.discover())
    assert listing.error.startswith(said)
    assert made.retry_in() is None and listing.retry_at is None
    assert made.waiting().startswith(said) and "again at" not in made.waiting()
    assert NOT_A_TOKEN not in made.waiting()


def test_list_again_asks_at_once_and_starts_the_back_off_over() -> None:
    clock = Clock(NOW)
    venue = Scripted([A], server_error(), server_error(), server_error())
    made = connections(venue, clock)
    asyncio.run(made.discover())
    asyncio.run(made.discover())
    assert made.retry_in() == connection.RETRY_FIRST_SECONDS * 2
    asyncio.run(made.list_again())
    assert venue.asked == 3, "asked at once, not at the back-off's time"
    assert made.retry_in() == connection.RETRY_FIRST_SECONDS
    listed = asyncio.run(made.list_again())
    assert [each.account_id for each in listed.accounts] == [A]
    assert made.retry_in() is None


class _Keeper:
    async def tend(self, _windows: Any) -> int:
        return 0


def test_the_poll_lists_again_without_anyone_acting(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 504 then a 200: the plugin's own poll lists the accounts again at
    the back-off's time, long before its next read, and nobody acts."""
    monkeypatch.setattr(connection, "RETRY_FIRST_SECONDS", 0.05)
    venue = Scripted([A], server_error(504))
    shared = Oanda()
    monkeypatch.setattr(shared.connections, "_make_venue", lambda *_: venue)
    monkeypatch.setattr(shared, "keeper", lambda: _Keeper())
    shared.connections.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE}))
    assert shared.connections.config.poll_seconds >= 30

    async def work() -> None:
        polling = asyncio.create_task(shared._poll())
        try:
            async with asyncio.timeout(5):
                while not shared.connections.listing.accounts:
                    await asyncio.sleep(0.01)
        finally:
            polling.cancel()

    asyncio.run(work())
    assert venue.asked == 2
    assert [each.account_id for each in shared.connections.listing.accounts] == [A]
    assert shared.connections.listing.error == ""


# ── List again, on Connections and as a tool ────────────────────────────────


def test_list_again_on_the_page_and_as_a_tool() -> None:
    clock = Clock(NOW)
    venue = Scripted([A], server_error(504), server_error(502))
    made = connections(venue, clock)
    client = held(made)
    shown = client.get("/admin/connections", "admin").body
    assert "HTTP 504" in shown and "Listing them again at" in shown
    assert "List again" in shown and "Waiting for the account" not in shown
    read = client.call_tool("read_connections", level="admin").data
    assert read["listing_retry_at"] and "HTTP 504" in read["listing_error"]
    # The tool lists at once: OANDA fails again, and says so.
    again = client.call_tool("list_accounts_again", level="admin")
    assert again.outcome == "unchanged" and venue.asked == 2
    assert "HTTP 502" in again.data["listing_error"] and again.data["listing_retry_at"]
    assert again.data["accounts"] == 0
    # Then it answers; through the form, the page shows the accounts.
    done = client.post("/admin/connections/list-again", "admin", {})
    assert done.status == 200 and venue.asked == 3
    assert "Listing them again at" not in done.body
    assert f'<option value="{A}" selected>' in done.body
    listed = client.call_tool("list_accounts_again", level="admin").data
    assert listed["accounts"] == 1 and listed["listing_error"] == ""
    assert listed["listing_retry_at"] == ""
