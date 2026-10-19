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
(decisions/028), in four kinds (contract v16; raw.py): a reported
activity's record, seven years by default; each read's raw responses, 30
days by default; each trading exchange (`trading`, as SnapTrade's,
approved 2026-10-13), seven years by default; and from 0.5.0 what OANDA's
pricing answered the data role (`pricing`, lake.py), a week by default; all
archivable.

**Its catalogue** (from 0.5.0, the data role; spec/the-lake, "The
catalogue"), of the lake's existing kinds, prices and quotes:

- `live`: each pair's best bid and ask with their liquidity, as a quote, and
  their mid, as a price of kind `mid`, from OANDA's pricing: pulled for the
  latest, or streamed for a standing want, at most once a second a pair. Its
  default terms keep its rows 90 days, a quarter, for TCA's look-back
  (spec/the-fx-and-multi-asset-sample-plugins §1.5); retention is the
  deployment's to change on Data sources, within OANDA's terms.
- `daily`: each pair's close, the mid of OANDA's daily candle, on its day
  ending at 17:00 New York (`day_time_zone` America/New_York,
  `day_end_minute` 1020), OANDA's default alignment; pulled; kept.

Neither names a venue in the catalogue: each row names OANDA, the venue
master's record for it (lake.py). Each licence is the default a
deployment's licence confirms or replaces, never a statement of what
OANDA's terms allow.
"""

from __future__ import annotations

from meridian import DatasetDeclaration, DatasetLicence
from meridian.declaration import NotCarried, RecordKind

from .settings import (
    ACTIVITY,
    DEFAULT_ACTIVITY_RETENTION_DAYS,
    DEFAULT_PRICING_RETENTION_DAYS,
    DEFAULT_RAW_RETENTION_DAYS,
    DEFAULT_TRADING_RETENTION_DAYS,
    PRICING,
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
PRICING_KIND = RecordKind(
    PRICING, "Pricing", window_days=DEFAULT_PRICING_RETENTION_DAYS, archivable=True
)
KINDS = (ACTIVITY_KIND, RESPONSES_KIND, TRADING_KIND, PRICING_KIND)

ACCOUNT = "oanda:account"
FILL = "oanda:order-fill"
FINANCING = "oanda:daily-financing"
PRICE_SCHEME = "oanda:price"

# ── The data role's catalogue ───────────────────────────────────────────────

VENDOR = "OANDA"
DAILY, LIVE = "daily", "live"
#: OANDA's day: it ends at 17:00 New York.
DAY_TIME_ZONE = "America/New_York"
DAY_END_MINUTE = 17 * 60
#: How long the live dataset's rows are kept by its default terms: a quarter.
LIVE_RETENTION_DAYS = 90
#: OANDA as the venue master knows it (W3.14), by its own name.
VENUE_NAME = "OANDA"
VENUE_IDENTIFIERS: tuple[tuple[str, str, str], ...] = (("symbol", "oanda", "oanda"),)

CATALOGUE = [
    DatasetDeclaration(
        key=LIVE,
        vendor=VENDOR,
        data_types=["meridian.v1.Price", "meridian.v1.Quote"],
        modes=["pull", "stream"],
        cadence=1,
        history=0,
        licence_default=DatasetLicence(
            kept=True, retention_days=LIVE_RETENTION_DAYS, derived_use=True, display=True
        ),
        day_time_zone=DAY_TIME_ZONE,
        day_end_minute=DAY_END_MINUTE,
    ),
    DatasetDeclaration(
        key=DAILY,
        vendor=VENDOR,
        data_types=["meridian.v1.Price"],
        modes=["pull"],
        cadence=86_400,
        history=0,
        licence_default=DatasetLicence(kept=True, derived_use=True, display=True),
        day_time_zone=DAY_TIME_ZONE,
        day_end_minute=DAY_END_MINUTE,
    ),
]

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
    # The price at which OANDA would close a position out: no field of a
    # quote or a price holds it.
    NotCarried("data", PRICE_SCHEME, "closeoutBid", "no_contract_meaning"),
    NotCarried("data", PRICE_SCHEME, "closeoutAsk", "no_contract_meaning"),
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
