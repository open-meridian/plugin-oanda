"""The pages (page.py), asked in-process as the sidecar would ask them,
over a read of the synthetic OANDA. SIMULATED ONLY."""

from __future__ import annotations

import asyncio
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

from rigs import ACCOUNT, EUR, CustodyRig, TradeRig, instrument_of, want

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


def test_connections_say_read_only_and_offer_enable_trading_to_connectivitys_admin() -> None:
    client, rig, _ = held()
    shown = client.get("/admin/connections", "admin").body
    assert "Read-only" in shown and "Enable trading" in shown
    read = client.call_tool("read_connections", level="admin")
    (connection,) = read.data["connections"]
    assert connection["trading"] is False
    assert connection["connection_id"] == f"synthetic:{ACCOUNT}"
    assert read.data["credentials"] == [{"label": "Access token", "set": False}]


def test_enable_trading_and_its_check() -> None:
    client, rig, _ = held()
    connection_id = f"synthetic:{ACCOUNT}"
    checked = client.call_tool(
        "check_enable_trading", {"connection_id": connection_id}, level="admin"
    )
    assert checked.outcome == "unchanged" and checked.data["trading"] is False
    refused = client.call_tool(
        "enable_trading", {"connection_id": "practice:nobody"}, level="admin"
    )
    assert refused.outcome == "refused" and refused.paths == ["connection_id"]
    done = client.post(
        "/admin/connections/enable-trading",
        "admin",
        {"connection_id": connection_id},
        roles=["connectivity"],
    )
    assert done.status == 200 and "Trading enabled on" in done.body
    current = rig.connections.current
    assert current is not None and current.trading
    assert "Read-only" not in client.get("/admin/connections", "admin").body


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
    client, rig, recorder = held()
    key = recorder.on("RecordHolding")[0].raw_record.key
    shown = client.get("/raw", "read", ref=key).body
    assert "The record a row references" in shown
    assert "lastTransactionID" in shown


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


def test_the_data_roles_connection_says_the_environment_and_its_badge() -> None:
    client, _, _ = held()
    read = client.call_tool("read_connection", level="admin")
    assert read.data["mode"] == "Sample data"
    assert read.data["environment"] == "Practice"
    assert read.data["reached_environment"] == "paper" and read.data["badge"] == "Demo"
    assert read.data["venue"] == "OANDA" and read.data["stream_state"] == "off"
    shown = client.get("/", "admin", roles="data").body
    assert "Agents&#39; orders" in shown and "Demo" in shown
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
    # A value saved before 0.5.0: the guard reads it live until saved again.
    rig.connections.configure(config_from({"sample_data": True, ENVIRONMENT: "practice"}))
    read = client.call_tool("read_connections", level="admin")
    assert read.data["environment_to_save"] == "practice"
    (connection,) = read.data["connections"]
    assert connection["badge"] == "Live (environment not confirmed)"
    assert "saved before this version" in client.get("/admin/connections", "admin").body
