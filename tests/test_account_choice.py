"""The account an admin chooses from those the token reaches (connection.py,
and Connections in page.py), from 0.3.0.

OANDA is stood in for by a venue that answers `GET /v3/accounts` from a
list the test gives, and is never asked for anything else; sample data by
the synthetic OANDA. SIMULATED ONLY: nothing here reaches OANDA, practice
or live, or any network, and the token is a made-up string.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, cast

import meridian
from meridian.suites import Recorder
from meridian.testing import PageClient

from oanda import page, synthetic
from oanda.connection import CHOSEN, SETTING, V1, Connections
from oanda.custody import Syncer
from oanda.linking import Links
from oanda.pairs import PairMap
from oanda.settings import (
    ACCOUNT_ID,
    ENVIRONMENT,
    LIVE,
    PRACTICE,
    SYNTHETIC,
    TOKEN,
    config_from,
)
from oanda.venue import Json, Venue, VenueError

from conftest import NOW, Clock

A = "101-001-1111111-001"
B = "101-001-1111111-002"
V1_ID = "1234567"
NOT_A_TOKEN = "made-up-test-token-not-oanda-0000"


class Listed(Venue):
    """OANDA's account list, and nothing else: any other call fails the test."""

    def __init__(self, ids: list[str], fail: VenueError | None = None) -> None:
        self.ids = ids
        self.fail = fail
        self.asked = 0

    async def accounts(self) -> list[Json]:
        self.asked += 1
        if self.fail is not None:
            raise self.fail
        return [{"id": each, "tags": []} for each in self.ids]

    def _not_asked(self, *_: Any) -> Any:
        raise AssertionError("only the account list is asked for")

    async def account(self, account_id: str) -> Json:
        return cast(Json, self._not_asked())

    async def instruments(self, account_id: str) -> list[Json]:
        return cast(list[Json], self._not_asked())

    async def transactions_since(
        self, account_id: str, since_id: str
    ) -> tuple[list[Json], str]:
        return cast(tuple[list[Json], str], self._not_asked())

    async def place(self, account_id: str, body: Json) -> Json:
        return cast(Json, self._not_asked())

    async def order(self, account_id: str, specifier: str) -> Json:
        return cast(Json, self._not_asked())

    async def cancel(self, account_id: str, specifier: str) -> Json:
        return cast(Json, self._not_asked())

    async def replace(self, account_id: str, specifier: str, body: Json) -> Json:
        return cast(Json, self._not_asked())

    async def stream(self, account_id: str) -> AsyncIterator[Json]:
        self._not_asked()
        yield {}

    async def pricing(self, account_id: str, instruments: Sequence[str]) -> Json:
        return cast(Json, self._not_asked())

    async def candles(
        self, account_id: str, instrument: str, from_time: str, to_time: str
    ) -> list[Json]:
        return cast(list[Json], self._not_asked())

    async def pricing_stream(
        self, account_id: str, instruments: Sequence[str]
    ) -> AsyncIterator[Json]:
        self._not_asked()
        yield {}


def connections(
    venue: Listed, *, storage: Path | None = None, settings: dict[str, str] | None = None
) -> Connections:
    made = Connections(storage or Path(tempfile.mkdtemp()), Clock(NOW), lambda *_: venue)
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, **(settings or {})}))
    return made


# ── The listing ─────────────────────────────────────────────────────────────


def test_nothing_is_listed_until_the_token_is_set() -> None:
    venue = Listed([A])
    made = Connections(Path(tempfile.mkdtemp()), Clock(NOW), lambda *_: venue)
    made.configure(config_from({}))
    assert asyncio.run(made.discover()).listed is False
    assert venue.asked == 0 and made.current is None
    assert made.waiting() == "waiting for its settings: token"


def test_a_token_reaching_no_account() -> None:
    made = connections(Listed([]))
    listing = asyncio.run(made.discover())
    assert listing.listed and listing.accounts == () and listing.preselected == ""
    assert made.current is None
    assert made.waiting() == "waiting for an admin to choose the OANDA account on Connections"
    why = made.why_not(A)
    assert why is not None and why[2] == "not_found"


def test_one_account_is_preselected_and_not_chosen_until_confirmed() -> None:
    made = connections(Listed([A]))
    listing = asyncio.run(made.discover())
    assert [each.account_id for each in listing.usable] == [A]
    assert listing.preselected == A
    # Preselected only: nothing is read or traded, and no choice is kept.
    assert made.choice.account_id == "" and made.current is None
    assert made.choose(A, "Ada Admin") == ""
    current = made.current
    assert current is not None and current.account_id == A
    assert current.connection_id == f"{PRACTICE}:{A}" and not current.trading
    assert made.choice.source == CHOSEN and made.choice.by == "Ada Admin"


def test_several_accounts_and_none_preselected() -> None:
    made = connections(Listed([A, B, A]))
    listing = asyncio.run(made.discover())
    assert [each.account_id for each in listing.accounts] == [A, B]
    assert listing.preselected == ""
    assert made.current is None
    assert made.choose(B, "Ada Admin") == ""
    assert made.current is not None and made.current.account_id == B


def test_a_v1_account_is_listed_unusable_with_why() -> None:
    made = connections(Listed([V1_ID, A]))
    listing = asyncio.run(made.discover())
    v1 = listing.of(V1_ID)
    assert v1 is not None and not v1.usable and v1.reason == V1
    assert "v1 account" in v1.reason and "v20" in v1.reason
    # The one usable account is preselected; the v1 account never chosen.
    assert listing.preselected == A
    why = made.why_not(V1_ID)
    assert why is not None and why[2] == "unusable"
    assert made.choose(V1_ID, "Ada Admin").startswith(f"{V1_ID} cannot be used")
    assert made.current is None


def test_an_account_the_token_does_not_reach_is_never_chosen() -> None:
    made = connections(Listed([A]))
    asyncio.run(made.discover())
    assert made.choose(B, "Ada Admin") == "The access token reaches no such account."
    assert made.current is None


def test_a_listing_that_fails_keeps_the_last_and_says_why() -> None:
    venue = Listed([A])
    made = connections(venue)
    asyncio.run(made.discover())
    venue.fail = VenueError(
        "listing accounts", 401, {"errorCode": "INSUFFICIENT_AUTHORIZATION"}
    )
    listing = asyncio.run(made.discover())
    assert listing.error.startswith("OANDA refused the access token")
    assert NOT_A_TOKEN not in listing.error
    assert [each.account_id for each in listing.accounts] == [A]


def test_new_settings_list_again() -> None:
    made = connections(Listed([A]))
    asyncio.run(made.discover())
    made.configure(config_from({TOKEN: NOT_A_TOKEN + "x", ENVIRONMENT: PRACTICE}))
    assert made.listing.listed is False


# ── The choice ──────────────────────────────────────────────────────────────


def test_an_explicit_account_id_wins_as_before() -> None:
    storage = Path(tempfile.mkdtemp())
    made = connections(Listed([A, B]), storage=storage)
    asyncio.run(made.discover())
    assert made.choose(B, "Ada Admin") == ""
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, ACCOUNT_ID: A}))
    assert made.choice.source == SETTING and made.choice.account_id == A
    current = made.current
    assert current is not None and current.connection_id == f"{PRACTICE}:{A}"
    why = made.why_not(B)
    assert why is not None and why[2] == "failed_precondition"
    # The setting is the choice whether or not the token lists it, as before.
    asyncio.run(made.discover())
    elsewhere = "101-001-9999999-001"
    unlisted = connections(Listed([]), storage=storage, settings={ACCOUNT_ID: elsewhere})
    assert unlisted.current is not None and unlisted.current.account_id == elsewhere


def test_a_change_of_choice_is_a_new_read_only_connection() -> None:
    made = connections(Listed([A, B]))
    asyncio.run(made.discover())
    made.choose(A, "Ada Admin")
    first = made.current
    assert first is not None and made.enable_trading(first.connection_id, "Ada Admin")
    assert made.current is not None and made.current.trading
    made.choose(B, "Ada Admin")
    second = made.current
    assert second is not None and second.connection_id == f"{PRACTICE}:{B}"
    assert second.connection_id != first.connection_id and not second.trading


def test_returning_to_a_chosen_account_finds_it_read_only() -> None:
    """A enabled, then B chosen, then A again: A's trading was turned off
    when it was left, recorded naming the choice that caused it, and stays
    off until a person enables it again."""
    made = connections(Listed([A, B]))
    asyncio.run(made.discover())
    made.choose(A, "Ada Admin")
    assert made.enable_trading(f"{PRACTICE}:{A}", "Ada Admin")
    made.choose(B, "Bo Admin")
    (off,) = made.turned_off()
    assert off["connection_id"] == f"{PRACTICE}:{A}" and off["to"] == f"{PRACTICE}:{B}"
    assert off["cause"] == "choose_account by Bo Admin" and off["by"] == "Bo Admin"
    assert off["enabled_by"] == "Ada Admin" and off["at"]
    made.choose(A, "Bo Admin")
    back = made.current
    assert back is not None and back.account_id == A and not back.trading
    assert made.enable_trading(back.connection_id, "Bo Admin")
    assert made.current is not None and made.current.trading


def test_returning_through_the_settings_finds_it_read_only() -> None:
    """The same through the account_id setting, and through the environment,
    and a change made while the plugin was stopped found when it starts."""
    storage = Path(tempfile.mkdtemp())
    made = connections(Listed([A, B]), storage=storage, settings={ACCOUNT_ID: A})
    assert made.enable_trading(f"{PRACTICE}:{A}", "Ada Admin")
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, ACCOUNT_ID: B}))
    (off,) = made.turned_off()
    assert off["connection_id"] == f"{PRACTICE}:{A}" and off["by"] == ""
    assert off["cause"] == f"the account_id setting changed the account from {A} to {B}"
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, ACCOUNT_ID: A}))
    assert made.current is not None and not made.current.trading

    # The environment: practice:A enabled, live:A, then practice:A again.
    assert made.enable_trading(f"{PRACTICE}:{A}", "Ada Admin")
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: LIVE, ACCOUNT_ID: A}))
    assert made.turned_off()[-1]["cause"] == (
        f"the settings changed the environment from {PRACTICE} to {LIVE}"
    )
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, ACCOUNT_ID: A}))
    assert made.current is not None and not made.current.trading

    # Enabled, then the plugin stopped, the setting changed and started again.
    assert made.enable_trading(f"{PRACTICE}:{A}", "Ada Admin")
    restarted = connections(Listed([A, B]), storage=storage, settings={ACCOUNT_ID: B})
    assert restarted.turned_off()[-1]["connection_id"] == f"{PRACTICE}:{A}"
    restarted.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, ACCOUNT_ID: A}))
    assert restarted.current is not None and not restarted.current.trading


def test_the_same_connection_again_keeps_its_trading() -> None:
    """A settings delivery that leaves the connection as it was, or one with
    no connection for a while, turns nothing off."""
    made = connections(Listed([A]), settings={ACCOUNT_ID: A})
    assert made.enable_trading(f"{PRACTICE}:{A}", "Ada Admin")
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, ACCOUNT_ID: A}))
    made.configure(config_from({ACCOUNT_ID: A}))
    made.configure(config_from({TOKEN: NOT_A_TOKEN, ENVIRONMENT: PRACTICE, ACCOUNT_ID: A}))
    assert made.turned_off() == []
    assert made.current is not None and made.current.trading


def test_the_choice_survives_a_restart_and_is_per_environment() -> None:
    storage = Path(tempfile.mkdtemp())
    made = connections(Listed([A]), storage=storage)
    asyncio.run(made.discover())
    made.choose(A, "Ada Admin")
    again = connections(Listed([A]), storage=storage)
    assert again.current is not None and again.current.account_id == A
    live = connections(Listed([A]), storage=storage, settings={ENVIRONMENT: LIVE})
    assert live.current is None


def test_sample_data_lists_the_synthetic_accounts_and_waits_for_the_choice() -> None:
    made = Connections(Path(tempfile.mkdtemp()), Clock(NOW))
    made.configure(config_from({SYNTHETIC: True}))
    listing = asyncio.run(made.discover())
    assert [(a.account_id, a.usable) for a in listing.accounts] == [
        (synthetic.ACCOUNT_ID, True),
        (synthetic.V1_ACCOUNT_ID, False),
    ]
    assert listing.preselected == synthetic.ACCOUNT_ID and made.current is None
    assert made.choose(synthetic.ACCOUNT_ID, "Ada Admin") == ""
    assert made.current is not None
    assert made.current.connection_id == f"synthetic:{synthetic.ACCOUNT_ID}"


def test_sample_data_with_an_account_id_is_the_synthetic_account_as_before() -> None:
    made = Connections(Path(tempfile.mkdtemp()), Clock(NOW))
    made.configure(config_from({SYNTHETIC: True, ACCOUNT_ID: A}))
    assert made.current is not None and made.current.account_id == synthetic.ACCOUNT_ID


# ── Connections, the page and its tools ─────────────────────────────────────


def held(made: Connections) -> PageClient:
    recorder = Recorder("oanda")
    syncer = Syncer(
        recorder, made, now=Clock(NOW), raw=None, pairs=PairMap(None), linked=lambda _: False
    )
    links = Links(cast(Any, recorder))
    asyncio.run(links.hold(meridian.AccountScope()))
    page.hold(page.Held(syncer, made, links, asyncio.Event(), None))
    return PageClient(page.pages, recorder, roles=["custody", "connectivity"])


def sample() -> Connections:
    made = Connections(Path(tempfile.mkdtemp()), Clock(NOW))
    made.configure(config_from({SYNTHETIC: True}))
    return made


def test_connections_list_the_accounts_and_preselect_the_only_usable_one() -> None:
    made = sample()
    client = held(made)
    read = client.call_tool("read_connections", level="admin")
    assert read.data["reached"] == [
        {"account_id": synthetic.ACCOUNT_ID, "usable": True, "reason": ""},
        {"account_id": synthetic.V1_ACCOUNT_ID, "usable": False, "reason": V1},
    ]
    assert read.data["preselected"] == synthetic.ACCOUNT_ID
    assert read.data["account_id"] == "" and read.data["connections"] == []
    shown = client.get("/admin/connections", "admin").body
    assert "Waiting for the account" in shown and "Use this account" in shown
    assert f'<option value="{synthetic.ACCOUNT_ID}" selected>' in shown
    assert f'<option value="{synthetic.V1_ACCOUNT_ID}"' not in shown
    assert "Unusable" in shown and made.current is None


def test_choose_account_and_its_check() -> None:
    made = sample()
    client = held(made)
    checked = client.call_tool(
        "check_choose_account", {"account_id": synthetic.ACCOUNT_ID}, level="admin"
    )
    assert checked.outcome == "unchanged" and made.current is None
    assert checked.data["said"].startswith("Would use")
    refused = client.call_tool(
        "choose_account", {"account_id": synthetic.V1_ACCOUNT_ID}, level="admin"
    )
    assert refused.outcome == "refused" and refused.paths == ["account_id"]
    assert refused.json["reason"] == "unusable"
    done = client.call_tool(
        "choose_account", {"account_id": synthetic.ACCOUNT_ID}, level="admin"
    )
    assert done.outcome == "made"
    assert done.data["connection_id"] == f"synthetic:{synthetic.ACCOUNT_ID}"
    assert done.data["trading"] is False
    read = client.call_tool("read_connections", level="admin")
    assert read.data["account_id"] == synthetic.ACCOUNT_ID
    assert read.data["account_from"] == "chosen" and read.data["preselected"] == ""
    (connection,) = read.data["connections"]
    assert connection["trading"] is False


def test_choosing_through_the_form() -> None:
    made = sample()
    client = held(made)
    client.get("/admin/connections", "admin")
    done = client.post(
        "/admin/connections/choose-account", "admin", {"account_id": synthetic.ACCOUNT_ID}
    )
    assert done.status == 200 and f"Using {synthetic.ACCOUNT_ID}" in done.body
    assert "read-only until trading is enabled" in done.body
    assert made.current is not None


def test_choosing_needs_admin_on_both_roles() -> None:
    made = sample()
    client = held(made)
    client.get("/admin/connections", "admin")
    refused = client.call_tool(
        "choose_account",
        {"account_id": synthetic.ACCOUNT_ID},
        level="admin",
        roles=["connectivity"],
    )
    assert refused.outcome == "refused" and refused.json["reason"] == "permission_denied"
    shown = client.post(
        "/admin/connections/choose-account",
        "admin",
        {"account_id": synthetic.ACCOUNT_ID},
        roles=["custody"],
    )
    assert "needs admin on each of the plugin" in shown.body
    assert made.current is None


def test_no_choice_is_offered_while_the_setting_names_the_account() -> None:
    made = connections(Listed([A, B]), settings={ACCOUNT_ID: A})
    client = held(made)
    shown = client.get("/admin/connections", "admin").body
    assert "The Account ID setting names the account" in shown
    assert "Use this account" not in shown
    refused = client.call_tool("check_choose_account", {"account_id": B}, level="admin")
    assert refused.outcome == "refused" and refused.json["reason"] == "failed_precondition"
    read = client.call_tool("read_connections", level="admin")
    assert read.data["account_id"] == A and read.data["account_from"] == "setting"


def test_connections_say_why_a_returned_to_connection_is_read_only() -> None:
    made = connections(Listed([A, B]))
    client = held(made)
    for account in (A, B):
        done = client.call_tool("choose_account", {"account_id": account}, level="admin")
        assert done.outcome == "made"
        if account == A:
            assert made.enable_trading(f"{PRACTICE}:{A}", "Ada Admin")
    back = client.call_tool("choose_account", {"account_id": A}, level="admin")
    assert back.data["trading"] is False and "read-only" in back.data["said"]
    (connection,) = client.call_tool("read_connections", level="admin").data["connections"]
    assert connection["trading"] is False
    assert connection["trading_turned_off"].startswith("Trading was turned off here at ")
    assert "choose_account by" in connection["trading_turned_off"]
    assert "Turned off on a switch" in client.get("/admin/connections", "admin").body
