"""The pages (page.py), asked in-process as the sidecar would ask them,
over a read of the synthetic OANDA. SIMULATED ONLY."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import meridian
from meridian.suites import Recorder
from meridian.testing import PageClient

from oanda import page
from oanda.linking import Links

from rigs import ACCOUNT, CustodyRig, TradeRig

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
    page.hold(page.Held(rig.syncer, rig.connections, links, asyncio.Event(), trader))
    client = PageClient(
        page.pages, recorder, read=[READER_ACCOUNT], roles=["custody", "connectivity"]
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
    assert "Nothing here for you" in nobody.get("/", "read").body


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
