"""What the plugin needs from the deployment, declared as settings (W4.7).

The names are the ones approved on 2026-10-13 (the spot-FX plugin's Q7):
`token`, OANDA's personal access token, a secret setting; `account_id`, the
v20 account it trades and reads; and `environment`, `practice` or `live`,
which says which of OANDA's hosts it reaches. The token is set once in the
dashboard's settings form, never read back, displayed, logged or kept: it
reaches this process in `meridian.Settings` and nowhere else, `Credentials`
keeps it out of its repr, and raw.py redacts it before anything is written.

**`account_id` is optional** from 0.3.0 (approved by the product owner,
2026-10-14). Once the token is set, Connections lists the v20 accounts it
reaches and an admin picks one there (`choose_account`, connection.py); an
`account_id` given here is the choice instead, as before 0.3.0, and the
page offers no pick while it is set.

While the token is missing the sidecar reports the plugin unhealthy, naming
it, and the plugin says it is waiting for it. Sample data (`sample_data`,
the SDK's developer setting) runs without it, against the synthetic OANDA
(synthetic.py), which never reaches OANDA.

**The windows** (contract v16). How long each kind of raw record stays in
storage, and what is done with it past that, are the SDK's two settings per
kind (`<kind>_window_days`, `<kind>_past_window`), from the kinds
declaration.py declares: `activity`, `responses` and `trading`. This module
declares neither, and reads them as delivered (`windows_from`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

import meridian
from meridian.roles import custody

TOKEN = "token"
ACCOUNT_ID = "account_id"
ENVIRONMENT = "environment"
POLL_SECONDS = "poll_seconds"
STALE_AFTER_HOURS = "stale_after_hours"
SYNTHETIC = "sample_data"

PRACTICE = "practice"
LIVE = "live"
ENVIRONMENTS = (PRACTICE, LIVE)

DEFAULT_POLL_SECONDS = 300
LEAST_POLL_SECONDS = 30
DEFAULT_STALE_AFTER_HOURS = 24

# The kinds of raw record (declaration.py) by the names their window settings
# take.
RESPONSES = "responses"
ACTIVITY = "activity"
TRADING = "trading"
ARCHIVED, KEPT, DELETED = "archived", "kept", "deleted"
PAST_WINDOW = (ARCHIVED, KEPT, DELETED)
# Each read's raw responses: 30 days by default, at least a day.
DEFAULT_RAW_RETENTION_DAYS = 30
LEAST_RETENTION_DAYS = 1
# A reported activity's raw record: seven years by default (the custody
# guidance of 2026-10-05), at most the SDK's bound.
DEFAULT_ACTIVITY_RETENTION_DAYS = 2555
MOST_RETENTION_DAYS = 36500
# Each trading exchange: seven years by default, as SnapTrade's (approved
# 2026-10-13). A default, never a claim about any rule.
DEFAULT_TRADING_RETENTION_DAYS = 2555

# The roles each setting serves (contract v15): the token, the account and
# the environment serve both, since trading and reading reach the same
# account with the same token; the reads' cadence is custody's.
BOTH = ("custody", "connectivity")
CUSTODY_ONLY = ("custody",)

_VENDORS: tuple[meridian.Setting, ...] = (
    # The environment first, before every other setting (the role pages
    # spec's Q12), in OANDA's own words: practice or live (approved
    # 2026-10-13).
    meridian.Setting(
        ENVIRONMENT,
        str,
        roles=BOTH,
        required=True,
        label="Environment",
        default=PRACTICE,
        choices=(
            meridian.Choice(PRACTICE, "Practice", "OANDA's practice host, a practice account."),
            meridian.Choice(LIVE, "Live", "OANDA's live host, a live account."),
        ),
        description="Which of OANDA's hosts the account is on. Practice first.",
    ),
    meridian.Setting(
        TOKEN,
        str,
        roles=BOTH,
        required=True,
        secret=True,
        label="Access token",
        description=(
            "The personal access token made in OANDA's hub for this account. OANDA does not "
            "keep it: if it is lost, revoke it there and make a new one."
        ),
    ),
    meridian.Setting(
        ACCOUNT_ID,
        str,
        roles=BOTH,
        label="Account ID",
        description=(
            "Optional. Leave it empty to choose the account on Connections, from those the "
            "token reaches. Given, it is the v20 account, as OANDA shows it: "
            "101-001-1234567-001."
        ),
    ),
    custody.poll_seconds(
        roles=CUSTODY_ONLY,
        default=DEFAULT_POLL_SECONDS,
        description=f"How often to read the account. At least {LEAST_POLL_SECONDS}.",
    ),
    custody.stale_after_hours(
        roles=CUSTODY_ONLY,
        default=DEFAULT_STALE_AFTER_HOURS,
        description=(
            "How old the last good read of the account may be, while OANDA does not "
            "answer, before it is reported stale."
        ),
    ),
)

# Sample data, last; a statement of its own, since `meridian plugin check`
# reads a common setting's helper inside one statement as coming before the
# settings declared beside it.
SAMPLE_DATA = meridian.sample_data(
    roles=BOTH,
    description=(
        "Trade with and read a synthetic OANDA built into the plugin instead of OANDA, to "
        "develop against before any token exists. Nothing reaches OANDA."
    ),
)
DECLARED = (*_VENDORS, SAMPLE_DATA)

Values = Mapping[str, str | int | bool | list[dict[str, str]]]


@dataclass(frozen=True)
class Credentials:
    """What OANDA is called with. Never in a repr, a log line or a page."""

    token: str = field(repr=False)
    environment: str = PRACTICE

    def secrets(self) -> tuple[str, ...]:
        """The values never to be kept anywhere, for raw.py to redact."""
        return (self.token,) if self.token else ()


@dataclass(frozen=True)
class Window:
    """One kind's window, and what is done with a record past it."""

    days: int
    past: str

    @property
    def length(self) -> timedelta:
        return timedelta(days=self.days)


@dataclass(frozen=True)
class Windows:
    """The three kinds' windows (contract v16)."""

    responses: Window = Window(DEFAULT_RAW_RETENTION_DAYS, KEPT)
    activity: Window = Window(DEFAULT_ACTIVITY_RETENTION_DAYS, KEPT)
    trading: Window = Window(DEFAULT_TRADING_RETENTION_DAYS, KEPT)

    def of(self, kind: str) -> Window:
        return {RESPONSES: self.responses, TRADING: self.trading}.get(kind, self.activity)


@dataclass(frozen=True)
class Config:
    """The settings as this plugin uses them, with defaults applied."""

    synthetic: bool = False
    credentials: Credentials | None = None
    # The `account_id` setting as given: the choice where it is set, "" where
    # the account is chosen on Connections.
    account_id: str = ""
    environment: str = PRACTICE
    # The names of the required settings not yet given, for saying so.
    missing: tuple[str, ...] = ()
    poll_seconds: int = DEFAULT_POLL_SECONDS
    stale_after: timedelta = timedelta(hours=DEFAULT_STALE_AFTER_HOURS)
    windows: Windows = field(default_factory=lambda: Windows())

    @property
    def ready(self) -> bool:
        """Whether there is something to reach: synthetic, or the settings."""
        return self.synthetic or self.credentials is not None

    def secrets(self) -> tuple[str, ...]:
        return self.credentials.secrets() if self.credentials is not None else ()


def _text(values: Values, name: str) -> str:
    value = values.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _number(values: Values, name: str) -> int | None:
    value = values.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def config_from(values: Values) -> Config:
    """The plugin's configuration from its delivered settings. The
    credentials are the token or absent: a missing token is named, never
    guessed, and there is no default token anywhere to fall back on. The
    account is not a credential: the `account_id` setting where it is given,
    else the one an admin chooses on Connections (connection.py)."""
    environment = _text(values, ENVIRONMENT)
    environment = environment if environment in ENVIRONMENTS else PRACTICE
    missing = tuple(name for name in (TOKEN,) if not _text(values, name))
    credentials = (
        None if missing else Credentials(token=_text(values, TOKEN), environment=environment)
    )
    poll = _number(values, POLL_SECONDS)
    stale = _number(values, STALE_AFTER_HOURS)
    return Config(
        synthetic=values.get(SYNTHETIC) is True,
        credentials=credentials,
        account_id=_text(values, ACCOUNT_ID),
        environment=environment,
        missing=missing,
        poll_seconds=DEFAULT_POLL_SECONDS if poll is None else max(poll, LEAST_POLL_SECONDS),
        stale_after=timedelta(
            hours=stale if stale is not None and stale > 0 else DEFAULT_STALE_AFTER_HOURS
        ),
        windows=windows_from(values),
    )


def _window(values: Values, kind: str, default: int) -> Window:
    """A kind's window as delivered, within the SDK's bound."""
    days = _number(values, f"{kind}_window_days")
    if days is None:
        days = default
    days = min(max(days, LEAST_RETENTION_DAYS), MOST_RETENTION_DAYS)
    chosen = values.get(f"{kind}_past_window")
    return Window(days, chosen if isinstance(chosen, str) and chosen in PAST_WINDOW else KEPT)


def windows_from(values: Values) -> Windows:
    """Each kind's window and what is done past it, from the settings the SDK
    declares for it. Past the window is `kept` where the settings say
    nothing."""
    return Windows(
        responses=_window(values, RESPONSES, DEFAULT_RAW_RETENTION_DAYS),
        activity=_window(values, ACTIVITY, DEFAULT_ACTIVITY_RETENTION_DAYS),
        trading=_window(values, TRADING, DEFAULT_TRADING_RETENTION_DAYS),
    )


def label(name: str) -> str:
    """What the settings form calls a setting."""
    return next((s.label for s in DECLARED if s.name == name and s.label), name)
