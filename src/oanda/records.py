"""The typed records the pages' routes take and answer, for a person's form
and an agent's tool alike (contract v12).

Each action's record names its inputs as its form names them, so one path
names one field for a person and one argument for an agent, and a refusal
names it by that path. The reads answer what their page shows, as data:
under Manage, how the connection is and which external account it reaches,
linked or not, and no account's data (a plugin admin is account agnostic);
under Open and View, the statement of the account the person may read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from meridian.roles import custody

#: The longest ID or name a form here takes.
MOST_ID = 200


# ── Actions ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConnectionAsked:
    """Which connection, by the ID Connections and read_connections name it
    by: its environment and account, `practice:101-001-1234567-001`."""

    connection_id: str = field(
        default="",
        metadata={
            "max_length": MOST_ID,
            "description": "the connection, as read_connections names it",
        },
    )


@dataclass(frozen=True)
class TradingEnabled:
    """A connection's trading, as it stands after the action."""

    connection_id: str
    trading: bool
    said: str


@dataclass(frozen=True)
class AccountAsked:
    """Which of the accounts the token reaches to use, by its v20 ID, as
    read_connections lists it under `reached`."""

    account_id: str = field(
        default="",
        metadata={
            "max_length": MOST_ID,
            "description": "the v20 account, as read_connections lists it under reached",
        },
    )


@dataclass(frozen=True)
class AccountChosen:
    """The account in use after the action, and the connection it makes:
    a new one is read-only."""

    account_id: str
    connection_id: str
    trading: bool
    said: str


@dataclass(frozen=True)
class ReadAsked:
    """Read OANDA now; `back` is the page a browser is answered with."""

    back: str = field(default="", metadata={"max_length": MOST_ID})


@dataclass(frozen=True)
class ReadStarted:
    said: str


@dataclass(frozen=True)
class LinkAsked:
    """Link the external account the connection reaches to one of the
    deployment's (`link`), create one for it and link it (`create`, for a
    deployment admin alone), or unlink it (`unlink`)."""

    intent: str = field(default="", metadata={"choices": ("link", "create", "unlink")})
    external_account_id: str = field(
        default="",
        metadata={"max_length": MOST_ID, "description": "as read_account_links names it"},
    )
    account_id: str = field(
        default="",
        metadata={"max_length": MOST_ID, "description": "the deployment's account, to link"},
    )
    new_account_name: str = field(default="", metadata={"max_length": MOST_ID})
    new_account_custodian: str = field(default="", metadata={"max_length": MOST_ID})
    new_account_type: str = field(default="", metadata={"max_length": MOST_ID})


@dataclass(frozen=True)
class Linked:
    external_account_id: str
    account_id: str
    account_name: str
    said: str


# ── Reads ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConnectionRead(custody.ConnectionRead):
    """The connection, and its trading: read-only until enabled, and how
    many placements it is working, holding back or asking about."""

    connection_id: str = ""
    environment: str = ""
    trading: bool = False
    trading_enabled_by: str = ""
    working: int = 0
    held: int = 0
    unknown: int = 0
    waiting_for_a_value_date: int = 0


@dataclass(frozen=True)
class ReachedRead:
    """One account the token reaches, as OANDA lists it: usable, or not and
    why (a v1 account)."""

    account_id: str
    usable: bool = True
    reason: str = ""


@dataclass(frozen=True)
class ConnectionsRead(custody.ConnectionsRead):
    """The connection, and the account it is for: the accounts the token
    reaches (`reached`), the one in use (`account_id`) and where it came from
    (`account_from`: `setting`, the Account ID setting; `chosen`, an admin's
    pick here, by `account_chosen_by`; empty, none yet), and the one
    preselected because it is the only usable one, which is not in use until
    an admin confirms it with choose_account."""

    connections: list[ConnectionRead] = field(default_factory=list)  # type: ignore[assignment]
    mode: str = ""
    reached: list[ReachedRead] = field(default_factory=list)
    listed: bool = False
    listing_error: str = ""
    account_id: str = ""
    account_from: str = ""
    account_chosen_by: str = ""
    preselected: str = ""


@dataclass(frozen=True)
class AccountLinksRead(custody.AccountLinksRead):
    pass


@dataclass(frozen=True)
class PendingRead:
    value_date: str
    quantity: str


@dataclass(frozen=True)
class RowRead:
    """One row of a statement: a currency (or a metal), its quantity, its
    settled quantity, and what is pending on each value date."""

    currency: str
    quantity: str
    settled: str
    pending: list[PendingRead] = field(default_factory=list)


@dataclass(frozen=True)
class StatementRead(custody.StatementRead):
    rows: list[RowRead] = field(default_factory=list)


@dataclass(frozen=True)
class StatementsRead(custody.StatementsRead):
    statements: list[StatementRead] = field(default_factory=list)  # type: ignore[assignment]
