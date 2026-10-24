"""The pages (page.py), asked in-process as the sidecar would ask them,
over a read of the synthetic OANDA. SIMULATED ONLY."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, cast

import meridian
from meridian import edge
from meridian.suites import Recorder
from meridian.testing import PageClient

from oanda import moments, page
from oanda.lake import Lake
from oanda.linking import Links
from oanda.pairs import PairMap
from oanda.settings import ENVIRONMENT, config_from

from rigs import ACCOUNT, EUR, CustodyRig, TradeRig, delivered, instrument_of, want

READER_ACCOUNT = "ACC-1"


def held(linked: bool = True) -> tuple[PageClient, CustodyRig, Recorder]:
    recorder = Recorder("oanda")
    rig = CustodyRig(recorder, linked=linked)
    asyncio.run(rig.syncer.run_once())
    links = Links(cast(Any, recorder))
    scope = meridian.AccountScope(
        links=(meridian.LinkedExternalAccount(ACCOUNT, READER_ACCOUNT, "E2E OANDA"),)
        if linked
        else ()
    )
    asyncio.run(links.hold(scope))
    trader = TradeRig(Recorder("oanda")).trader
    recorder.answer("ResolveInstrument", instrument_of)
    lake = Lake(
        recorder,
        rig.connections,
        PairMap(None),
        edge.RawStore(rig.storage, kind="prices"),
        clock_ns=lambda: moments.of(rig.clock()),
    )
    page.hold(page.Held(rig.syncer, rig.connections, links, asyncio.Event(), trader, lake))
    client = PageClient(
        page.pages, recorder, read=[READER_ACCOUNT], roles=["custody", "connectivity", "data"]
    )
    return client, rig, recorder


def test_every_page_at_admin_shows_no_account_data() -> None:
    client, _, _ = held()
    client.assert_no_account_data("98733.8860", "85493.88600", "10000", "2026-10-15")


def test_connection_says_read_only_and_offers_enable_trading_to_connectivitys_admin() -> None:
    """From 0.6.0 the connection's trading is the SDK's one Connection for
    the data role and connectivity (457), Connections custody's alone."""
    client, rig, _ = held()
    shown = client.get("/", "admin", tab="trading").body
    assert "Read-only" in shown and "Enable trading" in shown
    read = client.call_tool("read_connection", level="admin").data
    assert read["trading"]["accounts"] == [
        {"venue_account_code": ACCOUNT, "trading": False, "order_types": [], "mapped": False}
    ]
    assert read["credentials"] == [{"label": "Access token", "set": False}]
    connections = client.get("/admin/connections", "admin").body
    assert "Enable trading" not in connections and "Read-only" not in connections
    (connection,) = client.call_tool("read_connections", level="admin").data["connections"]
    assert connection["connection_id"] == f"synthetic:{ACCOUNT}" and "trading" not in connection


def test_enable_trading_and_its_check() -> None:
    client, rig, recorder = held()
    checked = client.call_tool(
        "check_enable_trading", {"venue_account_code": ACCOUNT}, level="admin"
    )
    assert checked.outcome == "unchanged" and "Would enable trading" in checked.data["said"]
    refused = client.call_tool(
        "enable_trading", {"venue_account_code": "nobody"}, level="admin"
    )
    assert refused.outcome == "refused" and refused.paths == ["venue_account_code"]
    done = client.post(
        "/enable-trading", "admin", {"venue_account_code": ACCOUNT}, roles=["connectivity"]
    )
    assert done.status == 200 and "Trading enabled on" in done.body
    current = rig.connections.current
    assert current is not None and current.trading
    assert "Read-only" not in client.get("/", "admin", tab="trading").body
    read = client.call_tool("read_connection", level="admin").data
    (account,) = read["trading"]["accounts"]
    assert account["trading"] is True
    assert account["order_types"] == ["market", "limit", "stop"]
    # Under the data role's admin alone, nothing is enabled.
    alone = client.post(
        "/enable-trading", "admin", {"venue_account_code": ACCOUNT}, roles=["data"]
    )
    assert alone.status == 403


def test_the_statement_for_a_person_who_may_read_the_linked_account() -> None:
    client, _, _ = held()
    read = client.call_tool("read_statements", level="read")
    (statement,) = read.data["statements"]
    assert statement["account"] == "E2E OANDA"
    rows = {row["currency"]: row for row in statement["rows"]}
    assert rows["EUR"]["settled"] == "0"
    assert rows["EUR"]["pending"] == [{"value_date": "2026-10-15", "quantity": "10000"}]
    nobody = PageClient(page.pages, Recorder("oanda"), read=[], roles=["custody"])
    assert nobody.call_tool("read_statements", level="read").data["statements"] == []
    assert "Nothing here for you" in nobody.get("/statements", "read").body


def test_a_rows_raw_record_is_followed() -> None:
    """From 0.6.0 the SDK's Raw records: the record shown to a person who
    may read the account it is linked to, and to no one else."""
    client, rig, recorder = held()
    key = recorder.on("RecordHolding")[0].raw_record.key
    shown = client.get("/raw", "read", ref=key).body
    assert "The record" in shown and "lastTransactionID" in shown
    stranger = PageClient(page.pages, recorder, read=["ACC-ELSEWHERE"], roles=["custody"])
    assert "lastTransactionID" not in stranger.get("/raw", "read", ref=key).body


def test_the_account_links_and_a_link() -> None:
    client, _, recorder = held(linked=False)
    read = client.call_tool("read_account_links", level="admin")
    assert [a["external_account_id"] for a in read.data["external_accounts"]] == [ACCOUNT]
    checked = client.call_tool(
        "check_link_account",
        {"intent": "create", "external_account_id": ACCOUNT, "new_account_name": "E2E"},
        level="admin",
    )
    assert checked.outcome == "refused" and checked.paths == ["intent"]


def test_account_links_carries_plain_forms_and_links_through_them() -> None:
    client = held(linked=False)[0]
    client.deployment_admin = True
    shown = client.get("/admin/accounts", "admin").body
    assert 'name="csrf"' in shown and 'value="create"' in shown
    assert 'name="new_account_type" value="margin"' in shown
    done = client.post(
        "/admin/accounts/link",
        "admin",
        {"intent": "create", "external_account_id": ACCOUNT, "new_account_name": "E2E OANDA"},
        roles=["custody"],
    )
    assert done.status == 200 and "Created E2E OANDA and linked" in done.body


def test_the_connection_says_the_environment_and_its_badge() -> None:
    client, _, _ = held()
    read = client.call_tool("read_connection", level="admin")
    assert read.data["mode"] == "Sample data"
    trading = read.data["trading"]
    assert trading["environment"] == "Practice"
    assert trading["reached_environment"] == "paper" and trading["badge"] == "Demo"
    assert read.data["venue"] == "OANDA" and read.data["stream_state"] == "off"
    shown = client.get("/", "admin", roles="data").body
    assert "agents may place orders only on a Demo connection" in shown and "Demo" in shown
    # Under Open or View, the data role's Connection is not served.
    assert client.get("/", "read").status == 403


def test_the_data_roles_datasets_after_a_want() -> None:
    client, rig, recorder = held()
    lake = page._now().lake
    assert lake is not None
    recorder.answer("ObservationsWanted", lambda _: [want("WNT-1", "live", [EUR])])
    asyncio.run(recorder.receive(observations_wanted=lake.on_want))
    read = client.call_tool("read_datasets", level="admin")
    assert [s["dataset"] for s in read.data["served"]] == ["oanda:live", "oanda:daily"]
    assert read.data["served"][0]["recorded"] > 0
    shown = client.get("/datasets", "admin", roles="data").body
    assert "oanda:live" in shown and "products mapped" in shown


def test_connections_carry_the_demo_badge_and_say_an_old_environment_value() -> None:
    client, rig, _ = held()
    (connection,) = client.call_tool("read_connections", level="admin").data["connections"]
    assert connection["badge"] == "Demo" and connection["reached_environment"] == "paper"
    # A value saved before 0.5.0, refused by the SDK and delivered unset: the
    # guard counts the connection live until it is saved again.
    (now,) = delivered({"sample_data": "true", ENVIRONMENT: "practice"})
    rig.connections.configure(config_from(now.values, now.refused))
    read = client.call_tool("read_connections", level="admin")
    assert read.data["environment_to_save"] == "practice"
    (connection,) = read.data["connections"]
    assert connection["badge"] == "Live (environment not confirmed)"
    shown = client.get("/admin/connections", "admin").body
    assert "no longer offered: save it again" in shown
    data = client.call_tool("read_connection", level="admin").data["trading"]
    assert data["environment"] == "Not set: save it again"
    assert data["badge"] == "Live (environment not confirmed)"
    assert data["environment_to_save"] == "practice"


def test_connection_draws_both_roles_sections_and_each_sessions_latency() -> None:
    """From 0.6.0 the SDK's one Connection for the data role and connectivity
    (457), from SDK 0.32.3 one group at a time under a tab row: Key, Limits
    and use, Stream, Trading (connectivity's sections), and Sessions once a
    session is measured (contract v27)."""
    import re

    from meridian.roles import Latency

    client, rig, recorder = held()

    def tabs_and_sections(**query: str) -> tuple[list[str], list[str]]:
        shown = client.get("/", "admin", **query).text
        tabs = re.findall(r'<a class="tab(?: on)?" href="\?tab=[^"]*"[^>]*>([^<]*)</a>', shown)
        return tabs, re.findall(r"<h2>([^<]*)</h2>", shown)

    assert tabs_and_sections() == (["Key", "Limits and use", "Stream", "Trading"], ["Key"])
    assert tabs_and_sections(tab="trading")[1] == ["Environment", "Accounts", "Placements"]
    latency = Latency(recorder)
    latency.rtt("orders", 12_000_000)
    held_now = page._now()
    page.hold(dataclasses.replace(held_now, latency=latency))
    tabs, sections = tabs_and_sections(tab="sessions")
    assert "Sessions" in tabs and sections == ["Sessions"]
    assert "RTT p99" in client.get("/", "admin", tab="sessions").text
    (session,) = client.call_tool("read_connection", level="admin").data["trading"][
        "session_latency"
    ]
    assert session["session"] == "orders"
