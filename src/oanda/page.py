"""The plugin's pages, on the SDK's `meridian.Pages`: the custody role's
common pages (spec/plugin-pages-follow-their-role), each template extending
the SDK's `meridian/roles/custody/<page>.html` and adding OANDA's own only
in its blocks.

- **Connections** (`/admin/connections`, `read_connections`) and **Account
  links** (`/admin/accounts`, `read_account_links`; `link_account`), at
  `admin`: setup only, account agnostic. Connections shows the connection
  the settings name -- its environment and account, whether the token is
  set (never its value), whether trading is enabled, and how many
  placements are working, held back, asked about or waiting for a value
  date -- and, for an admin of the `connectivity` role, "Enable trading"
  (`enable_trading`, `/admin/connections/enable-trading`): a new connection
  is read-only until then.
- **Statements** (`/`, `read_statements`) and **Raw records** (`/raw`), at
  `write` and `read`: the account's statement, its rows by currency with
  what is pending on each value date, for a person who may read the
  deployment's account it is linked to; and OANDA's raw records kept for
  it, with a row's reference followed. Refresh (`read_oanda_now`) under Open.

Every act is a POST carrying the SDK's CSRF token, and names the read tool
that checks it without changing anything (`checked_by`, contract v20).
"""

from __future__ import annotations

import asyncio
import http.server
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import meridian
from meridian.roles import custody as custody_role

from . import records
from .connection import Connections
from .custody import Row, Syncer, account_kind
from .declaration import KINDS
from .linking import Links, refusal, refused
from .raw import dumps
from .settings import TOKEN, label
from .trading import Trader

TITLE = "OANDA"
CUSTODY = "custody"
CONNECTIVITY = "connectivity"
KIT = "0.12.0"

_MOST_BODY = 8192

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates", kit=KIT)

STATEMENTS = "/"
RAW = "/raw"
CONNECTIONS = "/admin/connections"
ACCOUNTS = "/admin/accounts"
READ = "/read"
ENABLE_TRADING = f"{CONNECTIONS}/enable-trading"
LINK = "/admin/accounts/link"
CHECK = "/check"
INTENTS = ("link", "create", "unlink")
pages.environment.globals["paths"] = {
    "read": READ,
    "enable_trading": ENABLE_TRADING,
    "link": LINK,
    "raw": RAW,
}
pages.environment.globals["admin_level"] = meridian.AccessLevel.ACCESS_LEVEL_ADMIN

ONLY_DEPLOYMENT_ADMINS = "Only a deployment admin may create an account."
NOT_LINKABLE = (
    "Not linked: a deployment admin has not granted this plugin that account. A deployment "
    "admin grants this plugin the accounts it may link to, on its Accounts tab."
)
NOT_LINKABLE_REASON = "REFUSAL_REASON_ACCOUNT_NOT_LINKABLE"
READING = "Reading OANDA now. The statement shows it once the read is done."
_ASKING_SECONDS = 30.0


@dataclass(frozen=True)
class Held:
    syncer: Syncer
    connections: Connections
    links: Links
    wake: asyncio.Event
    trader: Trader | None = None


_held: Held | None = None


def hold(held: Held) -> None:
    global _held
    _held = held


def _now() -> Held:
    if _held is None:
        raise RuntimeError("the pages are served once hold() has given them what they read")
    return _held


def serve(plugin: meridian.Plugin, held: Held, port: int) -> http.server.ThreadingHTTPServer:
    hold(held)
    return pages.serve(plugin, port)


@dataclass(frozen=True)
class Notice:
    text: str
    tone: str = "info"


def _oversized(request: meridian.Request) -> meridian.Response | None:
    if len(request.body) > _MOST_BODY:
        return meridian.Response("That form is larger than this page takes.", 413)
    return None


def _field(form: Mapping[str, list[str]], name: str) -> str:
    values = form.get(name, [])
    return values[0].strip() if len(values) == 1 else ""


def _not_linkable(failed: Exception) -> bool:
    return isinstance(failed, meridian.CommandRefused) and (
        failed.reason_name == NOT_LINKABLE_REASON
    )


def _not_done(failed: meridian.MeridianError) -> str:
    if refused(failed):
        return f"The sidecar refused this: {refusal(failed)}"
    return f"That was not done: {refusal(failed)}"


# ── Connections, at admin ───────────────────────────────────────────────────


def _connections_read() -> records.ConnectionsRead:
    held = _now()
    config = held.connections.config
    connection = held.connections.current
    status = held.syncer.status
    shown = held.trader.shown if held.trader is not None else None
    credentials = [custody_role.Credential(label(TOKEN), set=config.credentials is not None)]
    rows = []
    if connection is not None:
        by, _ = held.connections.enabled_by(connection.connection_id)
        state = {
            "SYNC_STATE_CURRENT": "Current",
            "SYNC_STATE_STALE": "Stale",
            "SYNC_STATE_NEEDS_SIGN_IN": "Needs a new token",
        }.get(status.state, "Not read yet")
        rows.append(
            records.ConnectionRead(
                connection=connection.label,
                state=state,
                accounts_reached=1 if status.account else 0,
                last_read=status.read_at.isoformat(timespec="seconds")
                if status.read_at
                else "",
                connection_id=connection.connection_id,
                environment=connection.environment,
                trading=connection.trading,
                trading_enabled_by=by,
                working=shown.working if shown else 0,
                held=shown.held if shown else 0,
                unknown=shown.unknown if shown else 0,
                waiting_for_a_value_date=shown.waiting if shown else 0,
            )
        )
    return records.ConnectionsRead(
        credentials=credentials,
        connections=rows,
        mode="sample data: the synthetic OANDA" if config.synthetic else config.environment,
    )


def _connections(notice: Notice | None = None) -> meridian.Response:
    data = _connections_read()
    return meridian.Response(
        pages.render(
            "connections.html",
            data=data,
            notice=notice,
            waiting=_now().connections.config.missing,
        )
    )


@pages.page(
    CONNECTIONS,
    "Connections",
    levels="admin",
    roles=["custody", "connectivity"],
    answers=records.ConnectionsRead,
    name="read_connections",
    description=(
        "The OANDA connection the settings name: its environment and account, whether the "
        "access token is set (never its value), how the last read went, whether trading is "
        "enabled on it (a new connection is read-only), and how many placements it is "
        "working, holding back, asking OANDA about, or holding for a value date. No "
        "account's data."
    ),
)
async def connections(request: meridian.Request) -> meridian.Response:
    if request.tool_name:
        return pages.answer("connections.html", _connections_read())
    return _connections()


@pages.route(
    "/admin",
    levels="admin",
    roles=["custody", "connectivity"],
    tool=False,
    why="a browser's way in to Connections; an agent reads them with read_connections",
)
async def admin(request: meridian.Request) -> meridian.Response:
    query = urlencode(request.query)
    return meridian.Response(
        "", 303, headers=(("location", CONNECTIONS + (f"?{query}" if query else "")),)
    )


def _asked_connection(request: meridian.Request) -> str:
    asked: records.ConnectionAsked | None = request.params
    connection_id = asked.connection_id.strip() if asked is not None else ""
    current = _now().connections.current
    if current is None:
        pages.refuse(
            "Nothing to enable until OANDA's settings are given.", reason="unavailable"
        )
    if connection_id != current.connection_id:
        pages.refuse(
            "No such connection.",
            ("connection_id", "not the connection the settings name"),
            reason="not_found",
        )
    return connection_id


@pages.route(
    ENABLE_TRADING,
    levels="admin",
    roles=CONNECTIVITY,
    methods=["POST"],
    params=records.ConnectionAsked,
    answers=records.TradingEnabled,
    name="enable_trading",
    checked_by="check_enable_trading",
    made_on=CONNECTIONS,
    description=(
        "Enable trading on the OANDA connection: until then it is read-only, and every "
        "placement on it is rejected unsupported. The connection is the settings' "
        "environment and account; a change of either is a new connection, read-only again."
    ),
)
async def enable_trading(request: meridian.Request) -> meridian.Response:
    if (too_big := _oversized(request)) is not None:
        return too_big
    held = _now()
    connection_id = _asked_connection(request)
    held.connections.enable_trading(
        connection_id, request.caller.display_name or request.caller.subject
    )
    if held.trader is not None:
        await held.trader.publish()
    said = f"Trading enabled on {connection_id}: placements on it are now sent to OANDA."
    if request.tool_name:
        return pages.answer(
            "connections.html", records.TradingEnabled(connection_id, True, said)
        )
    return _connections(Notice(said, "good"))


@pages.route(
    ENABLE_TRADING + CHECK,
    levels="admin",
    roles=CONNECTIVITY,
    methods=["POST"],
    params=records.ConnectionAsked,
    answers=records.TradingEnabled,
    reads=True,
    name="check_enable_trading",
    description=(
        "Check enable_trading without enabling anything: whether the connection is the one "
        "the settings name, and whether trading is enabled on it already."
    ),
)
async def check_enable_trading(request: meridian.Request) -> meridian.Response:
    connection_id = _asked_connection(request)
    current = _now().connections.current
    already = current is not None and current.trading
    said = (
        f"Trading is enabled on {connection_id} already."
        if already
        else f"Would enable trading on {connection_id}."
    )
    return pages.answer(
        "connections.html", records.TradingEnabled(connection_id, already, said)
    )


# ── Account links, at admin ─────────────────────────────────────────────────


async def _links_read(request: meridian.Request) -> records.AccountLinksRead:
    held = _now()
    account = held.syncer.status.account
    connection = held.connections.current
    external = []
    if connection is not None and account:
        external.append(
            custody_role.ExternalAccountRead(
                connection.account_id,
                str(account.get("alias") or connection.account_id),
                connection.label,
            )
        )
    offered = await held.links.offered(request.caller.header)
    return records.AccountLinksRead(
        external_accounts=external,
        accounts=[
            custody_role.AccountRead(a.account_id, a.label())
            for a in offered.accounts
            if a.open
        ],
        links=[
            custody_role.LinkRead(link.external_account_id, link.account_id, link.account_name)
            for link in held.links.scope.links
        ],
    )


async def _accounts(
    request: meridian.Request, notice: Notice | None = None
) -> meridian.Response:
    data = await _links_read(request)
    kind, _ = account_kind(_now().syncer.status.account)
    account_type = "margin" if kind == "ACCOUNT_KIND_MARGIN" else ""
    # The map's accounts with what a new account would be: held at OANDA,
    # its type where OANDA's margin rate says it.
    handed = {
        "external_accounts": [
            {
                "external_account_id": a.external_account_id,
                "name": a.name,
                "detail": a.detail,
                "custodian": "OANDA",
                "account_type": account_type,
            }
            for a in data.external_accounts
        ],
        "accounts": [{"account_id": a.account_id, "name": a.name} for a in data.accounts],
        "links": [
            {
                "external_account_id": link.external_account_id,
                "account_id": link.account_id,
                "account_name": link.account_name,
            }
            for link in data.links
        ],
    }
    return meridian.Response(
        pages.render(
            "accounts.html",
            data=data,
            notice=notice,
            action=LINK,
            map=handed,
            account_type=account_type,
        )
    )


@pages.page(
    ACCOUNTS,
    "Account links",
    levels="admin",
    roles=CUSTODY,
    answers=records.AccountLinksRead,
    name="read_account_links",
    description=(
        "The OANDA account the connection reaches, by identity alone; the deployment's "
        "accounts a deployment admin granted this plugin to link to; and the links between "
        "them. No account's data."
    ),
)
async def accounts(request: meridian.Request) -> meridian.Response:
    if request.tool_name:
        return pages.answer("accounts.html", await _links_read(request))
    return await _accounts(request)


def _external_ids() -> set[str]:
    held = _now()
    connection = held.connections.current
    return (
        {connection.account_id}
        if connection is not None and held.syncer.status.account
        else set()
    )


async def _link(caller: meridian.Caller, asked: records.LinkAsked) -> tuple[str, str]:
    """The link made, or a refusal raised as the SDK raises it: the account
    linked to and the words for it."""
    held = _now()
    external_id = asked.external_account_id.strip()
    account_id = asked.account_id.strip() if asked.intent == "link" else ""
    name = asked.new_account_name.strip() if asked.intent == "create" else ""
    linked_to = await asyncio.wait_for(
        held.links.link(
            caller.header,
            external_id,
            account_id,
            name,
            asked.new_account_custodian.strip() if asked.intent == "create" else "",
            asked.new_account_type.strip() if asked.intent == "create" else "",
        ),
        _ASKING_SECONDS,
    )
    held.wake.set()
    now = held.links.of(external_id)
    to = now.account_name if now.account_id == linked_to else ""
    said = (
        f"Unlinked {external_id}."
        if asked.intent == "unlink"
        else f"Created {name} and linked {external_id} to it."
        if asked.intent == "create"
        else f"Linked {external_id} to {to or linked_to}."
    )
    return linked_to, said


@pages.route(LINK, levels="admin", roles=CUSTODY, methods=["POST"])
async def link(request: meridian.Request) -> meridian.Response:
    """The account map's forms: link, create and link, or unlink."""
    if (too_big := _oversized(request)) is not None:
        return too_big
    form = parse_qs(request.body.decode("utf-8", errors="replace"))
    asked = records.LinkAsked(
        intent=_field(form, "intent"),
        external_account_id=_field(form, "external_account_id"),
        account_id=_field(form, "account_id"),
        new_account_name=_field(form, "new_account_name")[:200],
        new_account_custodian=_field(form, "new_account_custodian"),
        new_account_type=_field(form, "new_account_type"),
    )
    if asked.intent not in INTENTS:
        return meridian.Response("This form does not say what to do.", 400)
    if asked.external_account_id not in _external_ids():
        return meridian.Response("No such account.", 404)
    if asked.intent == "link" and not asked.account_id:
        return await _accounts(request, Notice("Choose the account to link it to.", "warn"))
    if asked.intent == "create" and not request.caller.deployment_admin:
        return await _accounts(request, Notice(ONLY_DEPLOYMENT_ADMINS, "warn"))
    if asked.intent == "create" and not asked.new_account_name:
        return await _accounts(request, Notice("Name the new account.", "warn"))
    try:
        _, said = await _link(request.caller, asked)
    except meridian.MeridianError as failed:
        notice = Notice(NOT_LINKABLE if _not_linkable(failed) else _not_done(failed), "bad")
        return await _accounts(request, notice)
    except Exception as failed:
        return await _accounts(request, Notice(f"That failed: {type(failed).__name__}", "bad"))
    return await _accounts(request, Notice(said, "good"))


def _checked_link(caller: meridian.Caller, asked: records.LinkAsked) -> None:
    if asked.external_account_id.strip() not in _external_ids():
        pages.refuse(
            "No such account.",
            ("external_account_id", "not the account the connection reaches"),
            reason="not_found",
        )
    if asked.intent not in INTENTS:
        pages.refuse("Say what to do.", ("intent", "link, create or unlink"))
    if asked.intent == "link" and not asked.account_id.strip():
        pages.refuse("Choose the account to link it to.", ("account_id", "name the account"))
    if asked.intent == "create" and not caller.deployment_admin:
        pages.refuse(
            ONLY_DEPLOYMENT_ADMINS,
            ("intent", ONLY_DEPLOYMENT_ADMINS),
            reason="permission_denied",
        )
    if asked.intent == "create" and not asked.new_account_name.strip():
        pages.refuse("Name the new account.", ("new_account_name", "name the new account"))


@pages.tool(
    replaces=LINK,
    params=records.LinkAsked,
    answers=records.Linked,
    name="link_account",
    checked_by="check_link_account",
    accounts=["account_id"],
    description=(
        "Link the OANDA account read_account_links lists to one of the deployment's accounts "
        "(intent link, with account_id), create a new account for it and link it (intent "
        "create, with new_account_name: a deployment admin alone), or unlink it (intent "
        "unlink)."
    ),
)
async def link_tool(request: meridian.Request) -> meridian.Response:
    asked: records.LinkAsked = request.params
    _checked_link(request.caller, asked)
    try:
        linked_to, said = await _link(request.caller, asked)
    except meridian.MeridianError as failed:
        if _not_linkable(failed):
            pages.refuse(
                NOT_LINKABLE,
                ("account_id", "not an account a deployment admin granted this plugin"),
                reason="account_not_linkable",
            )
        pages.refuse(_not_done(failed))
    now = _now().links.of(asked.external_account_id.strip())
    return pages.answer(
        "accounts.html",
        records.Linked(asked.external_account_id.strip(), linked_to, now.account_name, said),
    )


@pages.route(
    LINK + CHECK,
    levels="admin",
    roles=CUSTODY,
    methods=["POST"],
    params=records.LinkAsked,
    answers=records.Linked,
    reads=True,
    name="check_link_account",
    description=(
        "Check link_account without linking anything: whether the external account is the "
        "one the connection reaches, and the account it would be linked to one a deployment "
        "admin granted this plugin (intent link); or a new account named by a deployment "
        "admin (intent create); or unlinking it (intent unlink)."
    ),
)
async def check_link_account(request: meridian.Request) -> meridian.Response:
    asked: records.LinkAsked = request.params
    _checked_link(request.caller, asked)
    external_id = asked.external_account_id.strip()
    if asked.intent == "unlink":
        return pages.answer(
            "accounts.html", records.Linked(external_id, "", "", f"Would unlink {external_id}.")
        )
    if asked.intent == "create":
        name = asked.new_account_name.strip()
        return pages.answer(
            "accounts.html",
            records.Linked(
                external_id, "", name, f"Would create {name} and link {external_id} to it."
            ),
        )
    offered = await _now().links.offered(request.caller.header)
    granted = {a.account_id: a for a in offered.accounts}
    account_id = asked.account_id.strip()
    if account_id not in granted:
        pages.refuse(
            NOT_LINKABLE,
            ("account_id", "not an account a deployment admin granted this plugin"),
            reason="account_not_linkable",
        )
    return pages.answer(
        "accounts.html",
        records.Linked(
            external_id,
            account_id,
            granted[account_id].name,
            f"Would link {external_id} to {granted[account_id].label()}.",
        ),
    )


# ── Statements, at write and read ───────────────────────────────────────────


def _readable(caller: meridian.Caller) -> bool:
    held = _now()
    connection = held.connections.current
    if connection is None:
        return False
    link = held.links.of(connection.account_id)
    return bool(link.account_id) and link.account_id in caller.read_for(CUSTODY)


def _row_read(row: Row) -> records.RowRead:
    return records.RowRead(
        currency=row.code,
        quantity=f"{row.quantity:f}",
        settled=f"{row.settled:f}",
        pending=[
            records.PendingRead(leg.value_date or "not stated", f"{leg.quantity:f}")
            for leg in row.pending
        ],
    )


def _statements_read(caller: meridian.Caller) -> records.StatementsRead:
    held = _now()
    if not _readable(caller):
        return records.StatementsRead()
    connection = held.connections.current
    status = held.syncer.status
    assert connection is not None
    link = held.links.of(connection.account_id)
    state = (
        "Recorded"
        if status.statement_id
        else ("Not read yet" if not status.read_at else "Not recorded")
    )
    return records.StatementsRead(
        statements=[
            records.StatementRead(
                account=link.account_name or link.account_id,
                custodian="OANDA",
                as_of=status.as_of,
                state=state,
                rows=[_row_read(row) for row in status.rows],
            )
        ]
    )


@pages.page(
    STATEMENTS,
    "Statements",
    levels=["write", "read"],
    roles=CUSTODY,
    answers=records.StatementsRead,
    name="read_statements",
    description=(
        "The OANDA account's statement, for a person who may read the deployment's account "
        "it is linked to: each currency's quantity, its settled quantity, and what is "
        "pending on each value date (an open position's two cash legs, rolling)."
    ),
)
async def statements(request: meridian.Request) -> meridian.Response:
    data = _statements_read(request.caller)
    return pages.answer(
        "statements.html",
        data,
        what=("This plugin brings an OANDA account into this deployment"),
    )


@pages.route(
    READ,
    levels=["admin", "write"],
    roles=CUSTODY,
    methods=["POST"],
    params=records.ReadAsked,
    answers=records.ReadStarted,
    name="read_oanda_now",
    checked_by="check_read_oanda_now",
    description="Read OANDA now and record what it reads. Changes nothing at OANDA.",
)
async def read_now(request: meridian.Request) -> meridian.Response:
    if (too_big := _oversized(request)) is not None:
        return too_big
    _now().wake.set()
    if request.tool_name:
        return pages.answer("statements.html", records.ReadStarted(READING))
    if request.caller.admin:
        return _connections(Notice(READING))
    return pages.answer(
        "statements.html", _statements_read(request.caller), notice=Notice(READING)
    )


@pages.route(
    READ + CHECK,
    levels=["admin", "write"],
    roles=CUSTODY,
    methods=["POST"],
    params=records.ReadAsked,
    answers=records.ReadStarted,
    reads=True,
    name="check_read_oanda_now",
    description="Check read_oanda_now without reading: whether there is a connection to read.",
)
async def check_read_oanda_now(request: meridian.Request) -> meridian.Response:
    if _now().connections.current is None:
        pages.refuse("Nothing to read until OANDA's settings are given.", reason="unavailable")
    return pages.answer("statements.html", records.ReadStarted("Would read OANDA now."))


# ── Raw records, at write and read ──────────────────────────────────────────


@pages.page(
    RAW,
    "Raw records",
    levels=["write", "read"],
    roles=CUSTODY,
    tool=False,
    why="a raw record is followed from a row's reference, and read as received",
)
async def raw_records(request: meridian.Request) -> meridian.Response:
    held = _now()
    kept: list[dict[str, Any]] = []
    found: dict[str, Any] | None = None
    key = request.query.get("ref", "")
    connection = held.connections.current
    if _readable(request.caller) and held.syncer.raw is not None and connection is not None:
        store = held.syncer.raw
        for kind in ("responses", "trading", "activity"):
            for record in store.latest(kind, connection.account_id, most=5):
                kept.append({"key": record.key, "kind": kind, "at": record.at})
        hit = store.find(key) if key else None
        # Only a record of the account the person may read is shown.
        if hit is not None and hit.account == connection.account_id:
            found = {"key": hit.key, "text": dumps(hit.document, indent=2)}
    plugin = request.plugin
    return meridian.Response(
        pages.render(
            "raw.html",
            kinds=list(KINDS),
            stored=list(getattr(plugin, "stored", []) or []),
            past={
                f"{kind.name}_past_window": held.syncer.connections.config.windows.of(
                    kind.name
                ).past
                for kind in KINDS
            },
            key=key,
            found=plugin.find_record(key) if key and hasattr(plugin, "find_record") else None,
            kept=kept,
            record=found,
            readable=_readable(request.caller),
        )
    )
