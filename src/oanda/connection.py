"""The plugin's one connection to OANDA: an environment and an account, the
venue that reaches it, and whether trading is enabled on it.

**The account** (from 0.3.0, approved by the product owner on 2026-10-14).
Once the token is set, the plugin lists the v20 accounts it reaches
(OANDA's `GET /v3/accounts`, `discover`), and an admin picks one on
Connections ("Use this account", `choose_account`,
`/admin/connections/choose-account`, checked by `check_choose_account`), as
Alpaca's and Tradier's admins pick from the accounts their key reaches. The
pick is the admin's act, declared as `enable_trading` is: the dashboard
admits it as the person's, made at the page or by an agent through the
deployment's MCP (which records the call as theirs), and can hold it for a
second person; never a setting written by the plugin (plugins set no
setting: option A, 2026-10-05). Who chose
which account, and when, is kept in the plugin's storage
(`state/account.json`), per environment, so a restart finds it, as
`enable_trading`'s is.

- Where the token reaches exactly one usable account, it is preselected on
  the page, and is still not chosen until an admin confirms it. Nothing is
  ever picked silently.
- A v1 account (its ID digits only) is listed as unusable, with why: the
  v20 API this plugin speaks does not reach one.
- An `account_id` setting, where given, is the choice, as before 0.3.0, and
  no pick is taken while it is set.

**A new connection is read-only** until an admin of the plugin's
`connectivity` role enables trading on it (as SnapTrade's are; the order
path's step 5, Q10), on Connections, "Enable trading" (`enable_trading`,
`/admin/connections/enable-trading`, the names approved for SnapTrade). A
connection is its environment and account: a change of either -- in the
settings, or of the account chosen -- is a new connection, read-only again.
Which connections trading is enabled on, by whom and when, is kept in the
plugin's storage (`state/connections.json`), so a restart finds it.

Sample data's connection is the synthetic OANDA (synthetic.py), whose
accounts are picked from in the same way; an `account_id` setting there
means the synthetic account, as before 0.3.0. Nothing in it reaches OANDA.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import synthetic
from .kept import Kept, file_in
from .settings import Config
from .venue import Json, Oanda, Venue, VenueError

SYNTHETIC = "synthetic"

#: Where the choice came from: the `account_id` setting, or an admin's pick.
SETTING = "setting"
CHOSEN = "chosen"

#: Why a v1 account cannot be used.
V1 = (
    "a v1 account (its ID is digits only): this plugin speaks OANDA's v20 API, which does not "
    "reach v1 accounts"
)


@dataclass(frozen=True)
class Reached:
    """One account the token reaches, as OANDA lists it, and whether this
    plugin can use it."""

    account_id: str
    usable: bool = True
    reason: str = ""


def reached_from(listed: list[Json]) -> tuple[Reached, ...]:
    """OANDA's `accounts`, each once, in its order: a v1 account unusable."""
    found: list[Reached] = []
    seen: set[str] = set()
    for each in listed:
        account_id = str(each.get("id") or "").strip()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        found.append(
            Reached(account_id, False, V1) if account_id.isdigit() else Reached(account_id)
        )
    return tuple(found)


@dataclass(frozen=True)
class Listing:
    """The accounts the token reaches, as last listed: `listed` once OANDA
    has been asked for them with these settings, `error` where it did not
    answer the last time."""

    accounts: tuple[Reached, ...] = ()
    listed: bool = False
    error: str = ""

    @property
    def usable(self) -> tuple[Reached, ...]:
        return tuple(each for each in self.accounts if each.usable)

    @property
    def preselected(self) -> str:
        """The one usable account, where the token reaches exactly one: shown
        selected, never chosen until an admin confirms it."""
        usable = self.usable
        return usable[0].account_id if len(usable) == 1 else ""

    def of(self, account_id: str) -> Reached | None:
        return next((each for each in self.accounts if each.account_id == account_id), None)


@dataclass(frozen=True)
class Choice:
    """The account in use: from the setting, from an admin's pick (by whom
    and when), or none yet."""

    account_id: str = ""
    source: str = ""
    by: str = ""
    at: str = ""


@dataclass(frozen=True)
class Connection:
    """One environment's account, and how to reach it."""

    connection_id: str
    account_id: str
    environment: str
    venue: Venue
    synthetic: bool
    trading: bool

    @property
    def code(self) -> str:
        """The venue account code a placement names it by, and the external
        account custody reports it under: OANDA's account ID."""
        return self.account_id

    @property
    def label(self) -> str:
        where = "synthetic OANDA" if self.synthetic else f"OANDA {self.environment}"
        return f"{self.account_id} ({where})"


def _said(failure: VenueError) -> str:
    """Why OANDA did not list the accounts, in words for the page."""
    if failure.unauthorised:
        return (
            "OANDA refused the access token (HTTP 401): make a new one and enter it in Settings"
        )
    return f"OANDA did not answer ({failure})"


class Connections:
    """The connection the settings and the admin's choice name, made again
    when either changes."""

    def __init__(
        self,
        storage: Path | None,
        now: Callable[[], datetime],
        make_venue: Callable[[str, str], Venue] = Oanda,
    ) -> None:
        self._storage = storage
        self._now = now
        self._make_venue = make_venue
        self._enabled = Kept(file_in(storage, "connections.json"), {})
        self._chosen = Kept(file_in(storage, "account.json"), {})
        self._synthetic: synthetic.SyntheticOanda | None = None
        self._real: tuple[tuple[str, str], Venue] | None = None
        self._listing: tuple[tuple[str, ...], Listing] | None = None
        self.config = Config()

    def configure(self, config: Config) -> None:
        self.config = config

    def _key(self) -> tuple[str, ...] | None:
        """What a listing was made for: the synthetic OANDA, or a token on a
        host. Held in memory only, as the venue holds the token."""
        config = self.config
        if config.synthetic:
            return (SYNTHETIC,)
        if config.credentials is None:
            return None
        return (config.credentials.token, config.credentials.environment)

    @property
    def environment(self) -> str:
        """Where the account is: the synthetic OANDA, or OANDA's practice or
        live host."""
        return SYNTHETIC if self.config.synthetic else self.config.environment

    def venue(self) -> Venue | None:
        """The venue the settings reach, before any account is chosen."""
        config = self.config
        if config.synthetic:
            if self._synthetic is None:
                self._synthetic = synthetic.SyntheticOanda(
                    self._now, synthetic.state_file(self._storage)
                )
            return self._synthetic
        credentials = config.credentials
        if credentials is None:
            return None
        key = (credentials.token, credentials.environment)
        if self._real is None or self._real[0] != key:
            self._real = (key, self._make_venue(credentials.token, credentials.environment))
        return self._real[1]

    # ── The accounts the token reaches ──────────────────────────────────

    @property
    def listing(self) -> Listing:
        """The accounts last listed with these settings; nothing listed yet
        where the settings changed since."""
        key = self._key()
        if key is None or self._listing is None or self._listing[0] != key:
            return Listing()
        return self._listing[1]

    def take(self, listed: list[Json]) -> Listing:
        """OANDA's list of accounts, as these settings' listing."""
        key = self._key()
        if key is None:
            return Listing()
        listing = Listing(reached_from(listed), listed=True)
        self._listing = (key, listing)
        return listing

    async def discover(self) -> Listing:
        """Ask OANDA for the accounts the token reaches (`GET /v3/accounts`).
        Where it does not answer, the last listing stands, saying why."""
        venue, key = self.venue(), self._key()
        if venue is None or key is None:
            return Listing()
        try:
            listed = await venue.accounts()
        except VenueError as failure:
            if self._key() != key:
                return self.listing
            listing = Listing(self.listing.accounts, listed=True, error=_said(failure))
            self._listing = (key, listing)
            return listing
        if self._key() != key:
            return self.listing
        return self.take(listed)

    # ── The account chosen ──────────────────────────────────────────────

    @property
    def choice(self) -> Choice:
        """The `account_id` setting where it is given (in sample data, the
        synthetic account, as before 0.3.0); else the account an admin chose
        on Connections for this environment; else none."""
        config = self.config
        if config.account_id:
            return Choice(
                synthetic.ACCOUNT_ID if config.synthetic else config.account_id, SETTING
            )
        held = self._chosen.value.get(self.environment)
        if not isinstance(held, dict) or not held.get("account_id"):
            return Choice()
        return Choice(
            str(held["account_id"]), CHOSEN, str(held.get("by", "")), str(held.get("at", ""))
        )

    def why_not(self, account_id: str) -> tuple[str, str, str] | None:
        """Why `account_id` cannot be chosen now, or None: the words, the
        field's note and the refusal's reason."""
        if self.venue() is None:
            return (
                "Nothing to choose until the access token is set.",
                "no token is set",
                "unavailable",
            )
        if self.config.account_id:
            return (
                f"The Account ID setting names the account ({self.choice.account_id}): clear "
                "it in Settings to choose the account here.",
                "the Account ID setting names the account",
                "failed_precondition",
            )
        reached = self.listing.of(account_id)
        if reached is None:
            return (
                "The access token reaches no such account.",
                "not an account the token reaches",
                "not_found",
            )
        if not reached.usable:
            return (f"{account_id} cannot be used: {reached.reason}.", "unusable", "unusable")
        return None

    def choose(self, account_id: str, by: str) -> str:
        """The account an admin chose, by the person named, kept for this
        environment. Returns why not, or "" once it is the choice."""
        problem = self.why_not(account_id)
        if problem is not None:
            return problem[0]
        held = self._chosen.value.get(self.environment)
        if not isinstance(held, dict) or held.get("account_id") != account_id:
            self._chosen.value[self.environment] = {
                "account_id": account_id,
                "by": by,
                "at": self._now().isoformat(),
            }
            self._chosen.save()
        return ""

    def waiting(self) -> str:
        """What the plugin waits for while there is no connection."""
        if self.venue() is None:
            return "waiting for its settings: " + ", ".join(self.config.missing)
        return "waiting for an admin to choose the OANDA account on Connections"

    # ── The connection ──────────────────────────────────────────────────

    @property
    def current(self) -> Connection | None:
        venue = self.venue()
        account_id = self.choice.account_id
        if venue is None or not account_id:
            return None
        environment = self.environment
        connection_id = f"{environment}:{account_id}"
        return Connection(
            connection_id=connection_id,
            account_id=account_id,
            environment=environment,
            venue=venue,
            synthetic=self.config.synthetic,
            trading=self.trading_on(connection_id),
        )

    @property
    def synthetic_venue(self) -> synthetic.SyntheticOanda | None:
        return self._synthetic

    def enable_trading(self, connection_id: str, by: str) -> bool:
        """Trading enabled on a connection, by the person named; False where
        the connection is not the one the settings name."""
        current = self.current
        if current is None or current.connection_id != connection_id:
            return False
        if connection_id not in self._enabled.value:
            self._enabled.value[connection_id] = {"by": by, "at": self._now().isoformat()}
            self._enabled.save()
        return True

    def trading_on(self, connection_id: str) -> bool:
        """Whether trading is enabled on the connection named."""
        return connection_id in self._enabled.value

    def enabled_by(self, connection_id: str) -> tuple[str, str]:
        held = self._enabled.value.get(connection_id) or {}
        return str(held.get("by", "")), str(held.get("at", ""))
