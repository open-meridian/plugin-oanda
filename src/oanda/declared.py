"""What this version declares beside its roles (W8.1, W4.1, contract v11).

Built from the plugin's code and used twice: `__main__` registers with it,
and `meridian plugin upload` reads the same one from the built image
(pyproject.toml's `[tool.meridian] declaration`). Its secret setting's name,
`token`, is the one settings.py declares secret.

What OANDA sends and this plugin does not carry, by name only, each with
why (spec/vendor-differences-have-a-place-in-the-contract, Q15): the
evidence that the common model may need to grow. Each is counted as it is
seen, and the count rides on the heartbeat, never leaving the deployment.

And the storage it asks for, at the edge, for OANDA's raw records
(decisions/028), in three kinds (contract v16; raw.py): a reported
activity's record, seven years by default; each read's raw responses, 30
days by default; and each trading exchange (`trading`, as SnapTrade's,
approved 2026-10-13), seven years by default; all archivable.
"""

from __future__ import annotations

from meridian.declaration import NotCarried, RecordKind

from .settings import (
    ACTIVITY,
    DEFAULT_ACTIVITY_RETENTION_DAYS,
    DEFAULT_RAW_RETENTION_DAYS,
    DEFAULT_TRADING_RETENTION_DAYS,
    RESPONSES,
    TRADING,
)

ACTIVITY_KIND = RecordKind(
    ACTIVITY, "Reported activity", window_days=DEFAULT_ACTIVITY_RETENTION_DAYS, archivable=True
)
RESPONSES_KIND = RecordKind(
    RESPONSES, "Raw responses", window_days=DEFAULT_RAW_RETENTION_DAYS, archivable=True
)
TRADING_KIND = RecordKind(
    TRADING, "Trading", window_days=DEFAULT_TRADING_RETENTION_DAYS, archivable=True
)
KINDS = (ACTIVITY_KIND, RESPONSES_KIND, TRADING_KIND)

ACCOUNT = "oanda:account"
FILL = "oanda:order-fill"
FINANCING = "oanda:daily-financing"

#: Each name not carried, with why.
NOT_CARRIED = (
    # The account's own figures: OANDA's margin available is no buying power,
    # and its unrealised profit is a valuation, which is reporting's.
    NotCarried("custody", ACCOUNT, "marginAvailable", "no_contract_meaning"),
    NotCarried("custody", ACCOUNT, "unrealizedPL", "no_contract_meaning"),
    NotCarried("custody", ACCOUNT, "marginCloseoutPercent", "no_contract_meaning"),
    # A fill's cost of the spread and its conversion factors to the home
    # currency: no field of an execution or an activity holds them.
    NotCarried("connectivity", FILL, "halfSpreadCost", "no_contract_meaning"),
    NotCarried("connectivity", FILL, "gainQuoteHomeConversionFactor", "no_contract_meaning"),
    NotCarried("connectivity", FILL, "lossQuoteHomeConversionFactor", "no_contract_meaning"),
    NotCarried("custody", FILL, "halfSpreadCost", "no_contract_meaning"),
    # The financing's split by open trade: custody reports the whole as one
    # activity; the split is each placement's charge (connectivity).
    NotCarried("custody", FINANCING, "positionFinancings", "no_contract_meaning"),
)


def seen_in_fill(fill: dict[str, object], role: str) -> list[tuple[str, str]]:
    """The names not carried one of OANDA's fills shows, for `role`."""
    names = (
        ("halfSpreadCost",)
        if role == "custody"
        else (
            "halfSpreadCost",
            "gainQuoteHomeConversionFactor",
            "lossQuoteHomeConversionFactor",
        )
    )
    return [(FILL, name) for name in names if fill.get(name) not in (None, "", "0", "0.0000")]


def seen_in_account(account: dict[str, object]) -> list[tuple[str, str]]:
    return [
        (ACCOUNT, name)
        for name in ("marginAvailable", "unrealizedPL", "marginCloseoutPercent")
        if account.get(name) not in (None, "")
    ]


def seen_in_financing(financing: dict[str, object]) -> list[tuple[str, str]]:
    return [(FINANCING, "positionFinancings")] if financing.get("positionFinancings") else []
