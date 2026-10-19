"""The plugin's pages, on the SDK's `meridian.Pages`: the data and custody
roles' common pages (spec/plugin-pages-follow-their-role), each template
extending the SDK's `meridian/roles/<role>/<page>.html` and adding OANDA's
own only in its blocks.

- **Connection** (`/`, `read_connection`) and **Datasets** (`/datasets`,
  `read_datasets`), at `admin`, for an admin of the data role (from 0.5.0):
  the data role's, the SDK's records and templates for a venue listing pairs
  (`meridian.roles.data.pairs`). Connection: the mode, the token set or not
  (never its value), the environment and the agents' guard's badge, OANDA's
  limits and the calls made, the venue, and the pricing stream; Datasets:
  `live` and `daily` as declared, licensed or not, each want, and OANDA's
  pairs. Neither shows a price: prices are the reading roles'.

- **Connections** (`/admin/connections`, `read_connections`) and **Account
  links** (`/admin/accounts`, `read_account_links`; `link_account`), at
  `admin`: setup only, account agnostic. Connections shows the connection
  -- its environment and account, whether the token is set (never its
  value), whether trading is enabled, and how many placements are working,
  held back, asked about or waiting for a value date -- and, for an admin
  of the `connectivity` role, "Enable trading" (`enable_trading`,
  `/admin/connections/enable-trading`): a new connection is read-only until
  then. From 0.3.0 its Account section lists the v20 accounts the token
  reaches, a v1 account unusable with why, and an admin of each of the
  plugin's roles picks the one to use ("Use this account", `choose_account`,
  `/admin/connections/choose-account`): the only usable one is preselected,
  never chosen until confirmed, and none is offered while the Account ID
  setting names the account (connection.py).
- **Statements** (`/statements`, `read_statements`; `/` before 0.5.0, now the
  data role's Connection) and **Raw records** (`/raw`), at
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
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import meridian
from meridian.roles import custody as custody_role
from meridian.roles import data as data_role
from meridian.roles.data import pairs as role_pairs

from . import records
from .connection import Connections, Listing
from .custody import Row, Syncer, account_kind
from .declared import CATALOGUE, KINDS, VENUE_NAME
from .lake import ENTITLED, Lake, connection_read
from .linking import Links, refusal, refused
from .raw import dumps
from .settings import LIVE, PAPER, TOKEN, label
from .trading import Trader

TITLE = "OANDA"
CUSTODY = "custody"
CONNECTIVITY = "connectivity"
DATA = "data"
KIT = "0.12.0"

_MOST_BODY = 8192

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates", kit=KIT)

CONNECTION = "/"
DATASETS = "/datasets"
STATEMENTS = "/statements"
RAW = "/raw"
CONNECTIONS = "/admin/connections"
ACCOUNTS = "/admin/accounts"
READ = "/read"
ENABLE_TRADING = f"{CONNECTIONS}/enable-trading"
CHOOSE_ACCOUNT = f"{CONNECTIONS}/choose-account"
LINK = "/admin/accounts/link"
CHECK = "/check"
INTENTS = ("link", "create", "unlink")
pages.environment.globals["paths"] = {
    "read": READ,
    "enable_trading": ENABLE_TRADING,
    "choose_account": CHOOSE_ACCOUNT,
    "link": LINK,
    "raw": RAW,
    "statements": STATEMENTS,
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
    lake: Lake | None = None


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


# ── The environment: the agents' guard's two keys ───────────────────────────


def _reached() -> str:
    """The environment the session reaches, read from OANDA's host (venue.py):
    `paper`, `live`, or empty before there is anything to reach."""
    venue = _now().connections.venue()
    return venue.environment if venue is not None else ""


def _environment_shown(setting: str) -> str:
    """The Environment setting as its form labels it."""
    return {PAPER: "Practice", LIVE: "Live"}.get(setting, f"{setting} (save it again)")


def _badge() -> str:
    """The badge both keys make (contract v26, W11.8): "Demo" where the
    setting says Practice and the session reached OANDA's practice host."""
    return meridian.connection_badge(_now().connections.config.environment_setting, _reached())


def _legacy_setting() -> str:
    """The Environment setting's value where it is one saved before 0.5.0,
    which the agents' guard reads as live; empty otherwise."""
    setting = _now().connections.config.environment_setting
    return "" if setting in (PAPER, LIVE) else setting


# ── The data role's Connection and Datasets, at admin ──────────────────────


def _data_mode() -> str:
    config = _now().connections.config
    if config.synthetic:
        return "Sample data"
    if config.credentials is None:
        return "No key"
    return "Live" if config.environment == LIVE else "Practice"


def _connection_read() -> records.LakeConnectionRead:
    held = _now()
    config = held.connections.config
    credentials = [data_role.Credential(label(TOKEN), set=config.credentials is not None)]
    read: records.LakeConnectionRead = connection_read(
        held.lake,
        credentials,
        _data_mode(),
        environment=_environment_shown(config.environment_setting),
        reached_environment=_reached(),
        badge=_badge(),
    )
    return read


@pages.page(
    CONNECTION,
    "Connection",
    levels="admin",
    roles=DATA,
    answers=records.LakeConnectionRead,
    name="read_connection",
    description=(
        "The data role's connection to OANDA's pricing: the mode (Practice, Live, Sample "
        "data or No key), whether the access token is set (never its value), the environment "
        "and the agents' guard's badge (Demo only while the setting says Practice and the "
        "session reached OANDA's practice host), OANDA's limits and the calls made, OANDA as "
        "the venue master holds it, and the pricing stream: its state, the pairs on it, its "
        "reconnects and messages. No price."
    ),
)
async def connection(request: meridian.Request) -> meridian.Response:
    return pages.answer("connection.html", _connection_read())


def _datasets_read() -> role_pairs.DatasetsRead:
    lake = _now().lake
    if lake is None:
        return role_pairs.datasets_read(None, CATALOGUE, TITLE.lower(), ENTITLED)
    return lake.datasets_read()


@pages.page(
    DATASETS,
    "Datasets",
    levels="admin",
    roles=DATA,
    answers=role_pairs.DatasetsRead,
    name="read_datasets",
    description=(
        "The datasets this plugin serves the lake, as its catalogue declares them -- live, "
        "each pair's quote and mid from OANDA's pricing, and daily, each pair's close on "
        "OANDA's day ending at 17:00 New York -- licensed or not, what each recorded and "
        "declined since the plugin started, each want heard, the standing ones kept current "
        "first, and OANDA's pairs: how many it lists and how many serve, and the currencies "
        "not resolved. No price."
    ),
)
async def datasets(request: meridian.Request) -> meridian.Response:
    return pages.answer(
        "meridian/roles/data/pairs/datasets.html", _datasets_read(), venue=VENUE_NAME
    )


# ── Connections, at admin ───────────────────────────────────────────────────


def _connections_read() -> records.ConnectionsRead:
    held = _now()
    config = held.connections.config
    connection = held.connections.current
    listing = held.connections.listing
    choice = held.connections.choice
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
                reached_environment=connection.venue.environment,
                badge=_badge(),
                trading=connection.trading,
                trading_enabled_by=by,
                trading_turned_off=""
                if connection.trading
                else _turned_off(connection_id=connection.connection_id),
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
        environment_to_save=_legacy_setting(),
        reached=[
            records.ReachedRead(a.account_id, a.usable, a.reason) for a in listing.accounts
        ],
        listed=listing.listed,
        listing_error=listing.error,
        account_id=choice.account_id,
        account_from=choice.source,
        account_chosen_by=choice.by,
        preselected="" if choice.account_id else listing.preselected,
    )


def _turned_off(connection_id: str) -> str:
    """When and why trading was last turned off on the connection by a
    switch away from it, or ""."""
    off = [e for e in _now().connections.turned_off() if e["connection_id"] == connection_id]
    if not off:
        return ""
    last = off[-1]
    return f"Trading was turned off here at {last['at']}, when {last['cause']}."


def _may_choose(request: meridian.Request) -> bool:
    """Whether the person may choose the account: admin on each of the
    plugin's roles, as the Account ID setting, which serves both, asks."""
    identity = getattr(request.plugin, "identity", None)
    held = [r for r in getattr(identity, "roles", ()) if r in (CUSTODY, CONNECTIVITY)]
    admin = meridian.AccessLevel.ACCESS_LEVEL_ADMIN
    return all(request.caller.level_for(r) == admin for r in held or (CUSTODY, CONNECTIVITY))


def _connections(
    request: meridian.Request, notice: Notice | None = None, selected: str = ""
) -> meridian.Response:
    data = _connections_read()
    config = _now().connections.config
    usable = [a for a in data.reached if a.usable]
    # The account the form shows selected: the one asked for, else the one
    # in use, else the only usable one -- shown, never chosen until sent.
    selected = selected or data.account_id or data.preselected
    return meridian.Response(
        pages.render(
            "connections.html",
            data=data,
            notice=notice,
            waiting=() if config.synthetic else config.missing,
            usable=usable,
            selected=selected if any(a.account_id == selected for a in usable) else "",
            may_choose=_may_choose(request),
        )
    )


async def _listed() -> Listing:
    """The accounts the token reaches, asked of OANDA where these settings
    have not listed them yet."""
    connections = _now().connections
    listing = connections.listing
    if not listing.listed and connections.venue() is not None:
        listing = await connections.discover()
    return listing


async def _prepared_account(request: meridian.Request) -> str:
    """The account a prepared choose_account names, where the page was
    opened from one (contract v20), for the form to start from."""
    prepared = await request.prepared()
    if prepared is None or prepared.tool_name != "choose_account":
        return ""
    try:
        asked = json.loads(prepared.arguments_json or "{}")
    except ValueError:
        return ""
    account_id = asked.get("account_id") if isinstance(asked, dict) else None
    return account_id.strip() if isinstance(account_id, str) else ""


@pages.page(
    CONNECTIONS,
    "Connections",
    levels="admin",
    roles=["custody", "connectivity"],
    answers=records.ConnectionsRead,
    name="read_connections",
    description=(
        "The OANDA connection: its environment and account, whether the access token is set "
        "(never its value), how the last read went, whether trading is enabled on it (a new "
        "connection is read-only), and how many placements it is working, holding back, "
        "asking OANDA about, or holding for a value date. And the accounts the token reaches "
        "(reached; a v1 account unusable, with why), the one in use and whether the Account "
        "ID setting or an admin's choice names it, and the one preselected because it is "
        "the only usable one, not in use until choose_account confirms it. No account's data."
    ),
)
async def connections(request: meridian.Request) -> meridian.Response:
    await _listed()
    if request.tool_name:
        return pages.answer("connections.html", _connections_read())
    return _connections(request, selected=await _prepared_account(request))


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
    return _connections(request, Notice(said, "good"))


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


def _choice_problem(request: meridian.Request, account_id: str) -> tuple[str, str, str] | None:
    """Why the account cannot be chosen by this person now, or None: the
    words, the field's note and the refusal's reason."""
    if not _may_choose(request):
        return (
            "Choosing the account needs admin on each of the plugin's roles, custody and "
            "connectivity, as the Account ID setting does.",
            "admin on custody and connectivity",
            "permission_denied",
        )
    if not account_id:
        return ("Choose the account to use.", "choose an account", "invalid_arguments")
    return _now().connections.why_not(account_id)


def _asked_account(request: meridian.Request) -> str:
    asked: records.AccountAsked | None = request.params
    return asked.account_id.strip() if asked is not None else ""


@pages.route(
    CHOOSE_ACCOUNT,
    levels="admin",
    roles=["custody", "connectivity"],
    methods=["POST"],
    params=records.AccountAsked,
    answers=records.AccountChosen,
    name="choose_account",
    checked_by="check_choose_account",
    made_on=CONNECTIONS,
    description=(
        "Choose which of the OANDA accounts the access token reaches the plugin uses, as "
        "read_connections lists them under reached: custody reads it and connectivity "
        "trades on it. Never made for you: an account the token alone reaches is "
        "preselected, not chosen until this. A v1 account cannot be chosen, nor any while "
        "the Account ID setting names one. A change of account is a new connection, "
        "read-only until trading is enabled on it, and turns trading off on the one left, "
        "so returning to it finds it read-only. Needs admin on custody and connectivity."
    ),
)
async def choose_account(request: meridian.Request) -> meridian.Response:
    if (too_big := _oversized(request)) is not None:
        return too_big
    held = _now()
    account_id = _asked_account(request)
    await held.connections.discover()
    problem = _choice_problem(request, account_id)
    if problem is not None:
        detail, note, reason = problem
        if request.tool_name:
            pages.refuse(detail, ("account_id", note), reason=reason)
        return _connections(request, Notice(detail, "warn"), selected=account_id)
    already = held.connections.choice.account_id == account_id
    held.connections.choose(account_id, request.caller.display_name or request.caller.subject)
    current = held.connections.current
    assert current is not None, "a venue, and the account just chosen"
    held.wake.set()
    if held.trader is not None:
        await held.trader.publish()
    said = (
        f"{account_id} is the account in use already."
        if already
        else f"Using {account_id}: the connection {current.connection_id}, "
        + (
            "trading enabled on it."
            if current.trading
            else "read-only until trading is enabled on it."
        )
    )
    if request.tool_name:
        return pages.answer(
            "connections.html",
            records.AccountChosen(account_id, current.connection_id, current.trading, said),
        )
    return _connections(request, Notice(said, "good"))


@pages.route(
    CHOOSE_ACCOUNT + CHECK,
    levels="admin",
    roles=["custody", "connectivity"],
    methods=["POST"],
    params=records.AccountAsked,
    answers=records.AccountChosen,
    reads=True,
    name="check_choose_account",
    description=(
        "Check choose_account without choosing: whether the access token reaches the "
        "account, whether it is usable (not a v1 account), whether the Account ID setting "
        "leaves the choice here, and the connection it would make."
    ),
)
async def check_choose_account(request: meridian.Request) -> meridian.Response:
    held = _now()
    account_id = _asked_account(request)
    await held.connections.discover()
    problem = _choice_problem(request, account_id)
    if problem is not None:
        detail, note, reason = problem
        pages.refuse(detail, ("account_id", note), reason=reason)
    connection_id = f"{held.connections.environment}:{account_id}"
    trading = held.connections.trading_on(connection_id)
    if held.connections.choice.account_id == account_id:
        said = f"{account_id} is the account in use already."
    else:
        said = f"Would use {account_id}: the connection {connection_id}, " + (
            "trading enabled on it." if trading else "read-only until trading is enabled on it."
        )
    return pages.answer(
        "connections.html", records.AccountChosen(account_id, connection_id, trading, said)
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
        return _connections(request, Notice(READING))
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
    if key.startswith("pricing/") and held.lake is not None:
        # The data role's: what OANDA's pricing answered, behind a price or a
        # quote in the lake. Market data, no account's.
        priced = held.lake.raw.get(key)
        if priced is not None:
            found = {"key": key, "text": dumps(priced, indent=2)}
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
