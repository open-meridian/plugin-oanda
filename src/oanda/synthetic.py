"""The synthetic OANDA: a simulated v20 account behind the `Venue` interface,
for sample data, the tests and `make e2e`.

SIMULATED ONLY. Nothing here reaches OANDA, its practice host or its live
host: an order "placed" here is a line in this module's state, and every
answer is shaped like OANDA's own (its account, its order and transaction
records, its reject bodies), invented, every value of it. Any run on a real
practice account is the product owner's alone, by hand, and never this.

What it holds survives the plugin's restart, as an account at OANDA does:
the state is a JSON file in the plugin's storage, written whole on every
change; with none given (a test) it lives in the process.

**The accounts.** The synthetic token reaches two (`accounts()`, OANDA's
`GET /v3/accounts`): the v20 account below, and a v1 account, its ID digits
only, which nothing here reads. **The v20 account** is one in US dollars,
opened 2026-06-01, a US account as OANDA's US division keeps it: positions
net by instrument, the oldest trade closed first, at a margin rate of 2%. Its history, from the
start, holds one of each kind of transaction the plugin converts: the
account funded, a EUR_USD buy taken outside the platform and still open,
a day's financing on it, a USD_JPY trade opened and closed with a realised
profit in yen converted to dollars and a commission, a transfer out to and
another in from one of the owner's other accounts, a withdrawal, an
adjustment, and an XAU_USD buy, a metal, still open.

**How it works an order**, each read (of the account, its transactions, an
order, or the stream) taking it one step on, so a test and the e2e see each:

- a market order fills when placed, in the same answer, as OANDA's do; it
  takes only FOK or IOC;
- a limit or stop order is placed PENDING (its create transaction in the
  answer), and fills whole on the next read, at its price;
- one of 1,000,000 units or more stays PENDING, working, until cancelled;
- cancelled: an ORDER_CANCEL; one already filled or cancelled answers 404
  with OANDA's ORDER_DOESNT_EXIST;
- replaced while it works: the order cancelled (`CLIENT_REQUEST_REPLACED`)
  and a new one created in its place, `replacesOrderID` naming it.

And by its units, so each of OANDA's answers has an order to show it:

- 13: rejected when placed, HTTP 400, an orderRejectTransaction with
  `INSUFFICIENT_MARGIN`;
- 17: placed, but the answer never arrives (the call fails with no status);
  the order is there when it is asked for by its client ID;
- 19: placed, answered with no create transaction, and read once with a
  state OANDA's mapping does not know (`PENDING_REVIEW`), then PENDING.

**The roll.** At each 17:00 New York the account's open trades are rolled:
a DAILY_FINANCING transaction at that moment, each open trade's financing
beside it, tripled on a Wednesday as OANDA's US financing FAQ describes.
`roll()` rolls them at once, as at the trading day's coming 17:00, for a test
and the e2e, so the day's own 17:00 rolls nothing again.

**OANDA's own app** (contract v23's venue-originated fills). `in_the_app()`
takes a market order as OANDA's platform or app takes one, outside the
plugin: no client extensions, filled at once. The e2e asks for one, or for a
roll, through `ask()`: a request file in the account's state directory, taken
on the synthetic account's next step, so nothing writes its state but itself.

**A controlled clock** (decision 66, 2026-10-16). Through `ask()` the e2e may
set the synthetic account's clock (`{"clock": "<RFC 3339 time>"}`): from
then on the account's own times -- each transaction's, each fill's, each
17:00 roll's -- run on from that moment at the wall clock's pace, kept with
its state, so the trade dates the plugin reports never depend on the day the
e2e happens to run. A clock is only ever set forward, never back, so the
account's transactions stay in time order.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from . import moments
from .venue import Json, Venue, VenueError, as_json

#: The synthetic account's ID, as OANDA's practice IDs are written.
ACCOUNT_ID = "101-001-0000000-001"
#: The accounts the synthetic token reaches (`GET /v3/accounts`): the v20
#: account above, and an older v1 account, its ID digits only, which the
#: plugin lists as unusable. Only the v20 account is usable, so Connections
#: preselects it, and it is still the admin's to confirm.
V1_ACCOUNT_ID = "7000001"
ACCOUNTS: tuple[Json, ...] = ({"id": ACCOUNT_ID, "tags": []}, {"id": V1_ACCOUNT_ID, "tags": []})
OPENED = "2026-06-01T13:00:00.000000000Z"
HOME = "USD"

#: The scenarios, by units (above).
REJECTED = Decimal(13)
LOST = Decimal(17)
ODD = Decimal(19)
WORKS = Decimal(1_000_000)
ODD_STATE = "PENDING_REVIEW"

#: The instruments it lists, each with its type, precision and a synthetic
#: price, as OANDA's instrument definitions state them.
INSTRUMENTS: dict[str, tuple[str, int, int, Decimal]] = {
    # name: (type, pipLocation, displayPrecision, price)
    "EUR_USD": ("CURRENCY", -4, 5, Decimal("1.08720")),
    "GBP_USD": ("CURRENCY", -4, 5, Decimal("1.27150")),
    "AUD_USD": ("CURRENCY", -4, 5, Decimal("0.65780")),
    "NZD_USD": ("CURRENCY", -4, 5, Decimal("0.60120")),
    "USD_CAD": ("CURRENCY", -4, 5, Decimal("1.36540")),
    "USD_CHF": ("CURRENCY", -4, 5, Decimal("0.88410")),
    "USD_JPY": ("CURRENCY", -2, 3, Decimal("149.820")),
    "USD_NOK": ("CURRENCY", -4, 5, Decimal("10.71250")),
    "USD_SEK": ("CURRENCY", -4, 5, Decimal("10.48300")),
    "EUR_GBP": ("CURRENCY", -4, 5, Decimal("0.85510")),
    "XAU_USD": ("METAL", -2, 3, Decimal("2401.150")),
}

#: Synthetic dollars per unit of each currency, for a profit's conversion.
DOLLARS = {
    "USD": Decimal(1),
    "EUR": Decimal("1.08720"),
    "GBP": Decimal("1.27150"),
    "AUD": Decimal("0.65780"),
    "NZD": Decimal("0.60120"),
    "CAD": Decimal(1) / Decimal("1.36540"),
    "CHF": Decimal(1) / Decimal("0.88410"),
    "JPY": Decimal(1) / Decimal("149.820"),
    "NOK": Decimal(1) / Decimal("10.71250"),
    "SEK": Decimal(1) / Decimal("10.48300"),
    "XAU": Decimal("2401.150"),
}

#: The daily financing rate the roll charges a long (and pays a short) on its
#: dollar value: a synthetic figure, not OANDA's.
FINANCING_RATE = Decimal("0.00002")

CENTS = Decimal("0.0001")


def _q(value: Decimal, places: Decimal = CENTS) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_EVEN)


def _s(value: Decimal) -> str:
    return f"{value:f}"


def _cut(day: date) -> str:
    """The 17:00 New York that ends a trading day, as OANDA writes a time."""
    ends = datetime.combine(day, time(moments.DAY_ENDS), tzinfo=moments.NEW_YORK)
    return moments.rfc3339(moments.of(ends))


def _days_financed(day: date) -> int:
    """A roll at a day's 17:00 finances one day; a Wednesday's three, the
    weekend's value days with it (OANDA's US financing FAQ)."""
    return 3 if day.weekday() == 2 else 1


def _price_text(instrument: str, value: Decimal) -> str:
    places = Decimal(1).scaleb(-INSTRUMENTS[instrument][2])
    return _s(value.quantize(places, rounding=ROUND_HALF_EVEN))


@dataclass
class _Clock:
    now: Callable[[], datetime]
    #: How far the account's clock runs ahead of `now` (a controlled clock).
    ahead_ns: int = 0

    def ns(self) -> int:
        return moments.of(self.now()) + self.ahead_ns

    def text(self) -> str:
        return moments.rfc3339(self.ns())


class SyntheticOanda(Venue):
    """The simulated account (above). `state` is where it is kept, or None."""

    def __init__(self, now: Callable[[], datetime], state: Path | None = None) -> None:
        self._clock = _Clock(now)
        self._file = state
        self.unanswered = 0
        self._state: Json = self._load()
        self._clock.ahead_ns = int(self._state.get("clock_ahead_ns", 0))

    # ── State ───────────────────────────────────────────────────────────

    def _load(self) -> Json:
        if self._file is not None and self._file.exists():
            try:
                held = json.loads(self._file.read_text(), parse_float=Decimal)
                if isinstance(held, dict) and held.get("account"):
                    return held
            except (OSError, ValueError):
                pass
        state: Json = {
            "account": {
                "id": ACCOUNT_ID,
                "alias": "Synthetic practice",
                "currency": HOME,
                "createdTime": OPENED,
                "marginRate": "0.02",
                "hedgingEnabled": False,
                "balance": "0",
            },
            "next": 1,
            "transactions": [],
            "orders": {},
            "trades": {},
            "odd_reads": {},
            "rolled": "",
        }
        self._state = state
        self._history()
        state["rolled"] = moments.trading_day(self._clock.ns()).isoformat()
        self._save()
        return state

    def _save(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        made, written = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "w") as file:
                json.dump(self._state, file, default=str, sort_keys=True)
            os.replace(written, self._file)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise

    def _id(self) -> str:
        made = int(self._state["next"])
        self._state["next"] = made + 1
        return str(made)

    @property
    def balance(self) -> Decimal:
        return Decimal(str(self._state["account"]["balance"]))

    def _move(self, amount: Decimal) -> str:
        now = _q(self.balance + amount)
        self._state["account"]["balance"] = _s(now)
        return _s(now)

    def _add(self, kind: str, at: str, **fields: Any) -> Json:
        made: Json = {
            "id": self._id(),
            "time": at,
            "type": kind,
            "accountID": ACCOUNT_ID,
            "userID": 1,
            "batchID": "",
            **fields,
        }
        made["batchID"] = made.get("batchID") or made["id"]
        self._state["transactions"].append(made)
        return made

    # ── The history it starts with ──────────────────────────────────────

    def _history(self) -> None:
        self._add("CREATE", OPENED, divisionID=1, siteID=101, homeCurrency=HOME)
        self._fund("CLIENT_FUNDING", Decimal("100000"), "2026-06-01T14:00:00.000000000Z")
        # A EUR_USD buy taken outside the platform, still open.
        self._fill_now(
            "EUR_USD",
            Decimal(10000),
            Decimal("1.08500"),
            "2026-06-02T13:30:00.000000000Z",
            "MARKET",
        )
        self._financing("2026-06-02T21:00:00.000000000Z", days=1)
        # A USD_JPY trade opened and closed: a profit in yen, converted.
        self._fill_now(
            "USD_JPY",
            Decimal(5000),
            Decimal("149.000"),
            "2026-06-03T13:00:00.000000000Z",
            "MARKET",
        )
        self._fill_now(
            "USD_JPY",
            Decimal(-5000),
            Decimal("150.000"),
            "2026-06-03T15:00:00.000000000Z",
            "MARKET",
            commission=Decimal("0.50"),
        )
        self._fund("ACCOUNT_TRANSFER", Decimal("-1000"), "2026-06-04T14:00:00.000000000Z")
        self._fund("CLIENT_FUNDING", Decimal("-500"), "2026-06-05T14:00:00.000000000Z")
        self._fund("ADJUSTMENT", Decimal("1.23"), "2026-06-08T14:00:00.000000000Z")
        self._fund("ACCOUNT_TRANSFER", Decimal("200"), "2026-06-09T14:00:00.000000000Z")
        # A metal, still open.
        self._fill_now(
            "XAU_USD",
            Decimal(1),
            Decimal("2390.000"),
            "2026-06-10T13:00:00.000000000Z",
            "MARKET",
        )

    def _fund(self, reason: str, amount: Decimal, at: str) -> None:
        balance = self._move(amount)
        self._add(
            "TRANSFER_FUNDS",
            at,
            amount=_s(amount),
            fundingReason=reason,
            accountBalance=balance,
        )

    # ── Filling ─────────────────────────────────────────────────────────

    def _fill_now(
        self,
        instrument: str,
        units: Decimal,
        price: Decimal,
        at: str,
        kind: str,
        *,
        order: Json | None = None,
        commission: Decimal = Decimal(0),
    ) -> Json:
        """A fill, positions netted oldest first as a US account's are."""
        base, quote = instrument.split("_")
        if order is None:
            order = self._add(
                f"{kind}_ORDER",
                at,
                instrument=instrument,
                units=_s(units),
                timeInForce="FOK",
                positionFill="DEFAULT",
                reason="CLIENT_ORDER",
            )
        rest = units
        closed: list[Json] = []
        reduced: Json | None = None
        quote_pl = Decimal(0)
        for trade in sorted(
            (
                t
                for t in self._state["trades"].values()
                if t["state"] == "OPEN" and t["instrument"] == instrument
            ),
            key=lambda t: int(t["id"]),
        ):
            open_units = Decimal(str(trade["currentUnits"]))
            if rest == 0 or (open_units > 0) == (rest > 0):
                continue
            take = min(abs(open_units), abs(rest))
            signed = -take if open_units > 0 else take
            pl = (price - Decimal(str(trade["price"]))) * take * (1 if open_units > 0 else -1)
            quote_pl += pl
            home_pl = _q(pl * DOLLARS[quote])
            entry = {
                "tradeID": trade["id"],
                "units": _s(signed),
                "price": _s(price),
                "realizedPL": _s(home_pl),
                "financing": "0.0000",
                "halfSpreadCost": "0.0000",
            }
            trade["currentUnits"] = _s(open_units + signed)
            trade["realizedPL"] = _s(Decimal(str(trade.get("realizedPL", "0"))) + home_pl)
            if Decimal(str(trade["currentUnits"])) == 0:
                trade["state"] = "CLOSED"
                trade["closeTime"] = at
                closed.append(entry)
            else:
                reduced = entry
            rest += -signed
        pl_home = _q(quote_pl * DOLLARS[quote])
        balance = self._move(pl_home - commission)
        fields: Json = {
            "orderID": order["id"],
            "instrument": instrument,
            "units": _s(units),
            "price": _s(price),
            "fullVWAP": _s(price),
            "fullPrice": {
                "type": "PRICE",
                "bids": [{"price": _s(price), "liquidity": "1000000"}],
                "asks": [{"price": _s(price), "liquidity": "1000000"}],
                "closeoutBid": _s(price),
                "closeoutAsk": _s(price),
            },
            "reason": str(order["type"]).removesuffix("_ORDER") + "_ORDER",
            "pl": _s(pl_home),
            "quotePL": _s(_q(quote_pl, Decimal("0.00001"))),
            "financing": "0.0000",
            "commission": _s(commission),
            "guaranteedExecutionFee": "0.0000",
            "accountBalance": balance,
            "halfSpreadCost": "0.0000",
            "gainQuoteHomeConversionFactor": _s(DOLLARS[quote]),
            "lossQuoteHomeConversionFactor": _s(DOLLARS[quote]),
        }
        client = (order.get("clientExtensions") or {}).get("id")
        if client:
            fields["clientOrderID"] = client
        if closed:
            fields["tradesClosed"] = closed
        if reduced is not None:
            fields["tradeReduced"] = reduced
        if rest != 0:
            filled = self._add("ORDER_FILL", at, **fields)
            # OANDA names the trade by the fill's own transaction ID.
            trade_id = filled["id"]
            filled["tradeOpened"] = {
                "tradeID": trade_id,
                "units": _s(rest),
                "price": _s(price),
                "guaranteedExecutionFee": "0.0000",
                "halfSpreadCost": "0.0000",
                "initialMarginRequired": "0",
            }
            if client:
                filled["tradeOpened"]["clientExtensions"] = dict(
                    as_json(order.get("clientExtensions"))
                )
            self._state["trades"][trade_id] = {
                "id": trade_id,
                "instrument": instrument,
                "price": _s(price),
                "openTime": at,
                "initialUnits": _s(rest),
                "currentUnits": _s(rest),
                "state": "OPEN",
                "realizedPL": "0.0000",
                "financing": "0.0000",
                "clientExtensions": dict(order.get("clientExtensions") or {}),
            }
            return filled
        return self._add("ORDER_FILL", at, **fields)

    def _financing(self, at: str, days: int) -> Json | None:
        """A roll: each open trade financed for `days`."""
        positions: dict[str, list[Json]] = {}
        total = Decimal(0)
        for trade in sorted(self._state["trades"].values(), key=lambda t: int(t["id"])):
            if trade["state"] != "OPEN":
                continue
            units = Decimal(str(trade["currentUnits"]))
            base = trade["instrument"].split("_")[0]
            charged = _q(-units * DOLLARS[base] * FINANCING_RATE * days)
            trade["financing"] = _s(Decimal(str(trade["financing"])) + charged)
            positions.setdefault(trade["instrument"], []).append(
                {"tradeID": trade["id"], "financing": _s(charged)}
            )
            total += charged
        if not positions:
            return None
        balance = self._move(total)
        return self._add(
            "DAILY_FINANCING",
            at,
            financing=_s(total),
            accountBalance=balance,
            accountFinancingMode="DAILY",
            positionFinancings=[
                {
                    "instrument": instrument,
                    "financing": _s(sum((Decimal(e["financing"]) for e in each), Decimal(0))),
                    "openTradeFinancings": each,
                }
                for instrument, each in sorted(positions.items())
            ],
        )

    def roll(self) -> Json | None:
        """Roll the open trades now, as at the trading day's coming 17:00
        New York (a Wednesday's tripled); that 17:00 then rolls nothing
        again."""
        day = moments.trading_day(self._clock.ns())
        made = self._financing(_cut(day), _days_financed(day))
        self._state["rolled"] = (day + timedelta(days=1)).isoformat()
        self._save()
        return made

    def set_clock(self, at: str) -> bool:
        """The account's clock set to `at`, an RFC 3339 time, running on from
        it (decision 66); refused, changing nothing, where it is not a time
        or would move the clock back. The day `at` falls in is taken as
        already rolled, so setting it rolls nothing."""
        wanted = moments.ns(at)
        if not wanted or wanted < self._clock.ns():
            return False
        self._clock.ahead_ns = wanted - moments.of(self._clock.now())
        self._state["clock_ahead_ns"] = self._clock.ahead_ns
        self._state["rolled"] = moments.trading_day(self._clock.ns()).isoformat()
        self._save()
        return True

    def in_the_app(self, instrument: str, units: Decimal) -> str:
        """A market order taken in OANDA's own platform or app, outside the
        plugin: no client extensions, filled at once. Its order's ID."""
        answer = self._create(
            {
                "type": "MARKET",
                "instrument": instrument,
                "units": _s(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        )
        self._save()
        return str(answer["orderCreateTransaction"]["id"])

    def _asked(self) -> bool:
        """The requests `ask()` left beside the state, each taken once."""
        if self._file is None:
            return False
        inbox = self._file.parent / "app"
        if not inbox.is_dir():
            return False
        taken = False
        for asked in sorted(inbox.glob("*.json")):
            try:
                request = json.loads(asked.read_text())
            except (OSError, ValueError):
                continue
            asked.unlink(missing_ok=True)
            if not isinstance(request, dict):
                continue
            clock = request.get("clock")
            if isinstance(clock, str) and self.set_clock(clock):
                taken = True
            if request.get("roll"):
                self.roll()
                taken = True
            order = request.get("order")
            if isinstance(order, dict) and order.get("instrument") in INSTRUMENTS:
                self.in_the_app(str(order["instrument"]), Decimal(str(order.get("units"))))
                taken = True
        return taken

    def _tick(self) -> None:
        """One step on: what was asked of it, a roll if a 17:00 New York has
        passed, then each working limit or stop order filled; saved where
        anything changed."""
        changed = self._asked()
        day = moments.trading_day(self._clock.ns())
        if self._state.get("rolled") and day.isoformat() > self._state["rolled"]:
            ended = day - timedelta(days=1)
            self._financing(_cut(ended), _days_financed(ended))
            self._state["rolled"] = day.isoformat()
            changed = True
        for order in sorted(self._state["orders"].values(), key=lambda o: int(o["id"])):
            if order.pop("fresh", None):
                changed = True
                continue
            if order["state"] != "PENDING":
                continue
            units = Decimal(str(order["units"]))
            if abs(units) >= WORKS:
                continue
            price = Decimal(str(order["price"]))
            fill = self._fill_now(
                order["instrument"],
                units,
                price,
                self._clock.text(),
                order["type"].removesuffix("_ORDER"),
                order=order,
            )
            order["state"] = "FILLED"
            order["fillingTransactionID"] = fill["id"]
            order["filledTime"] = fill["time"]
            changed = True
        if changed:
            self._save()

    # ── The interface ───────────────────────────────────────────────────

    async def accounts(self) -> list[Json]:
        return [dict(each) for each in ACCOUNTS]

    def _check(self, account_id: str, asked: str) -> None:
        if account_id not in (ACCOUNT_ID, ""):
            raise VenueError(
                asked,
                403,
                {
                    "errorCode": "INVALID_ACCOUNT",
                    "errorMessage": "The account specified is invalid",
                },
            )

    async def account(self, account_id: str) -> Json:
        self._check(account_id, "reading the account")
        self._tick()
        held = dict(self._state["account"])
        trades = [
            dict(t)
            for t in sorted(self._state["trades"].values(), key=lambda t: int(t["id"]))
            if t["state"] == "OPEN"
        ]
        positions: dict[str, Json] = {}
        for trade in trades:
            units = Decimal(trade["currentUnits"])
            side = "long" if units > 0 else "short"
            held_position = positions.setdefault(
                trade["instrument"],
                {
                    "instrument": trade["instrument"],
                    "long": {"units": "0", "tradeIDs": []},
                    "short": {"units": "0", "tradeIDs": []},
                },
            )
            this = held_position[side]
            this["units"] = _s(Decimal(this["units"]) + units)
            this["tradeIDs"].append(trade["id"])
        for position in positions.values():
            for side in ("long", "short"):
                ids = position[side]["tradeIDs"]
                if not ids:
                    continue
                cost = sum(
                    (
                        Decimal(self._state["trades"][i]["currentUnits"])
                        * Decimal(self._state["trades"][i]["price"])
                        for i in ids
                    ),
                    Decimal(0),
                )
                position[side]["averagePrice"] = _price_text(
                    position["instrument"], cost / Decimal(position[side]["units"])
                )
        # The account's own figures, at the synthetic prices: its NAV, the
        # balance and the open trades' unrealised profit in dollars, and the
        # margin they use.
        unrealised = Decimal(0)
        used = Decimal(0)
        for trade in trades:
            instrument = trade["instrument"]
            quote = instrument.split("_")[1]
            units = Decimal(trade["currentUnits"])
            now = INSTRUMENTS[instrument][3]
            unrealised += (now - Decimal(trade["price"])) * units * DOLLARS[quote]
            rate = (
                Decimal("0.02") if INSTRUMENTS[instrument][0] == "CURRENCY" else Decimal("0.05")
            )
            used += abs(units) * now * DOLLARS[quote] * rate
        held["unrealizedPL"] = _s(_q(unrealised))
        held["NAV"] = _s(_q(self.balance + unrealised))
        held["marginUsed"] = _s(_q(used))
        held["openTradeCount"] = len(trades)
        held["openPositionCount"] = len(positions)
        held["pendingOrderCount"] = sum(
            1 for o in self._state["orders"].values() if o["state"] == "PENDING"
        )
        held["trades"] = trades
        held["positions"] = list(positions.values())
        held["orders"] = [
            self._order_view(o)
            for o in self._state["orders"].values()
            if o["state"] == "PENDING"
        ]
        held["lastTransactionID"] = self._last()
        return {"account": held, "lastTransactionID": self._last()}

    def _last(self) -> str:
        return str(int(self._state["next"]) - 1)

    async def instruments(self, account_id: str) -> list[Json]:
        self._check(account_id, "reading instruments")
        return [
            {
                "name": name,
                "type": kind,
                "displayName": name.replace("_", "/"),
                "pipLocation": pip,
                "displayPrecision": precision,
                "tradeUnitsPrecision": 0,
                "minimumTradeSize": "1",
                "marginRate": "0.02" if kind == "CURRENCY" else "0.05",
            }
            for name, (kind, pip, precision, _) in sorted(INSTRUMENTS.items())
        ]

    async def transactions_since(
        self, account_id: str, since_id: str
    ) -> tuple[list[Json], str]:
        self._check(account_id, "reading transactions")
        self._tick()
        after = int(since_id or 0)
        found = [dict(t) for t in self._state["transactions"] if int(t["id"]) > after]
        return found, self._last()

    def _order_view(self, order: Json) -> Json:
        shown = {k: v for k, v in order.items() if k not in ("fresh",)}
        odd = self._state["odd_reads"].get(order["id"])
        if odd:
            shown["state"] = ODD_STATE
        return shown

    def _find(self, specifier: str) -> Json | None:
        orders = self._state["orders"]
        if specifier.startswith("@"):
            named = [
                o
                for o in orders.values()
                if (o.get("clientExtensions") or {}).get("id") == specifier[1:]
            ]
            return max(named, key=lambda o: int(o["id"])) if named else None
        found = orders.get(specifier)
        return found if isinstance(found, dict) else None

    async def order(self, account_id: str, specifier: str) -> Json:
        self._check(account_id, "reading an order")
        self._tick()
        found = self._find(specifier)
        if found is None:
            raise VenueError(
                "reading an order",
                404,
                {
                    "errorCode": "ORDER_DOESNT_EXIST",
                    "errorMessage": "The order specified does not exist",
                },
            )
        shown = self._order_view(found)
        if found["id"] in self._state["odd_reads"]:
            # Read once in the state OANDA's mapping does not know, then as
            # it is.
            self._state["odd_reads"].pop(found["id"])
            self._save()
        return {"order": shown, "lastTransactionID": self._last()}

    def _validated(self, body: Json) -> tuple[Json, str]:
        order = as_json(body.get("order"))
        kind = str(order.get("type") or "")
        tif = str(order.get("timeInForce") or "")
        instrument = str(order.get("instrument") or "")
        if kind not in ("MARKET", "LIMIT", "STOP"):
            return order, "INVALID_ORDER_TYPE"
        if instrument not in INSTRUMENTS:
            return order, "INSTRUMENT_NOT_TRADEABLE"
        if kind == "MARKET" and tif not in ("FOK", "IOC"):
            return order, "TIME_IN_FORCE_INVALID"
        if kind != "MARKET" and tif not in ("GTC", "GTD", "GFD", "FOK", "IOC"):
            return order, "TIME_IN_FORCE_INVALID"
        if kind != "MARKET" and not order.get("price"):
            return order, "PRICE_MISSING"
        try:
            units = Decimal(str(order.get("units")))
        except ArithmeticError:
            return order, "UNITS_INVALID"
        if units == 0:
            return order, "UNITS_INVALID"
        if abs(units) == REJECTED:
            return order, "INSUFFICIENT_MARGIN"
        return order, ""

    def _reject(self, asked: str, order: Json, reason: str, status: int = 400) -> VenueError:
        kind = str(order.get("type") or "MARKET")
        rejected = self._add(
            f"{kind}_ORDER_REJECT",
            self._clock.text(),
            instrument=order.get("instrument", ""),
            units=order.get("units", ""),
            rejectReason=reason,
            clientExtensions=order.get("clientExtensions") or {},
        )
        self._save()
        return VenueError(
            asked,
            status,
            {
                "orderRejectTransaction": rejected,
                "relatedTransactionIDs": [rejected["id"]],
                "lastTransactionID": self._last(),
                "errorCode": reason,
                "errorMessage": reason.replace("_", " ").capitalize(),
            },
        )

    def _create(self, order: Json, replaces: str = "") -> Json:
        kind = str(order["type"])
        fields: Json = {
            "instrument": order["instrument"],
            "units": str(order["units"]),
            "timeInForce": order["timeInForce"],
            "positionFill": order.get("positionFill", "DEFAULT"),
            "reason": "REPLACEMENT" if replaces else "CLIENT_ORDER",
            "clientExtensions": order.get("clientExtensions") or {},
        }
        for name in ("price", "gtdTime", "triggerCondition"):
            if order.get(name):
                fields[name] = order[name]
        if replaces:
            fields["replacesOrderID"] = replaces
        created = self._add(f"{kind}_ORDER", self._clock.text(), **fields)
        held = {
            "id": created["id"],
            "createTime": created["time"],
            "type": kind,
            "state": "PENDING",
            **{k: v for k, v in fields.items() if k != "reason"},
        }
        if kind == "MARKET":
            price = INSTRUMENTS[order["instrument"]][3]
            fill = self._fill_now(
                order["instrument"],
                Decimal(str(order["units"])),
                price,
                created["time"],
                "MARKET",
                order=held,
            )
            held["state"] = "FILLED"
            held["fillingTransactionID"] = fill["id"]
            self._state["orders"][held["id"]] = held
            return {"orderCreateTransaction": created, "orderFillTransaction": fill}
        held["fresh"] = True
        self._state["orders"][held["id"]] = held
        return {"orderCreateTransaction": created}

    async def place(self, account_id: str, body: Json) -> Json:
        self._check(account_id, "placing an order")
        order, reason = self._validated(body)
        if reason:
            raise self._reject("placing an order", order, reason)
        answer = self._create(order)
        created = answer["orderCreateTransaction"]
        units = abs(Decimal(str(order["units"])))
        answer["relatedTransactionIDs"] = [created["id"]]
        answer["lastTransactionID"] = self._last()
        if units == ODD:
            self._state["odd_reads"][created["id"]] = True
            self._save()
            return {"relatedTransactionIDs": [created["id"]], "lastTransactionID": self._last()}
        self._save()
        if units == LOST:
            self.unanswered += 1
            raise VenueError("placing an order")
        return answer

    async def cancel(self, account_id: str, specifier: str) -> Json:
        self._check(account_id, "cancelling an order")
        found = self._find(specifier)
        if found is None or found["state"] != "PENDING":
            rejected = self._add(
                "ORDER_CANCEL_REJECT",
                self._clock.text(),
                orderID=(found or {}).get("id", ""),
                rejectReason="ORDER_DOESNT_EXIST",
            )
            self._save()
            raise VenueError(
                "cancelling an order",
                404,
                {
                    "orderCancelRejectTransaction": rejected,
                    "errorCode": "ORDER_DOESNT_EXIST",
                    "errorMessage": "The Order specified does not exist",
                },
            )
        cancelled = self._add(
            "ORDER_CANCEL",
            self._clock.text(),
            orderID=found["id"],
            clientOrderID=(found.get("clientExtensions") or {}).get("id", ""),
            reason="CLIENT_REQUEST",
        )
        found["state"] = "CANCELLED"
        found["cancellingTransactionID"] = cancelled["id"]
        found["cancelledTime"] = cancelled["time"]
        self._save()
        return {
            "orderCancelTransaction": cancelled,
            "relatedTransactionIDs": [cancelled["id"]],
            "lastTransactionID": self._last(),
        }

    async def replace(self, account_id: str, specifier: str, body: Json) -> Json:
        self._check(account_id, "replacing an order")
        found = self._find(specifier)
        if found is None or found["state"] != "PENDING":
            raise VenueError(
                "replacing an order",
                404,
                {
                    "errorCode": "ORDER_DOESNT_EXIST",
                    "errorMessage": "The Order specified does not exist",
                },
            )
        order, reason = self._validated(body)
        if reason:
            raise self._reject("replacing an order", order, reason)
        cancelled = self._add(
            "ORDER_CANCEL",
            self._clock.text(),
            orderID=found["id"],
            clientOrderID=(found.get("clientExtensions") or {}).get("id", ""),
            reason="CLIENT_REQUEST_REPLACED",
        )
        found["state"] = "CANCELLED"
        found["cancellingTransactionID"] = cancelled["id"]
        answer = self._create(order, replaces=found["id"])
        found["replacedByOrderID"] = answer["orderCreateTransaction"]["id"]
        self._save()
        return {
            "orderCancelTransaction": cancelled,
            **answer,
            "relatedTransactionIDs": [cancelled["id"], answer["orderCreateTransaction"]["id"]],
            "lastTransactionID": self._last(),
        }

    async def stream(self, account_id: str) -> AsyncIterator[Json]:
        """New transactions as they are made, and a heartbeat between."""
        self._check(account_id, "streaming transactions")
        seen = int(self._last())
        quiet = 0
        while True:
            self._tick()
            fresh = [dict(t) for t in self._state["transactions"] if int(t["id"]) > seen]
            for transaction in fresh:
                seen = int(transaction["id"])
                yield transaction
            quiet = 0 if fresh else quiet + 1
            if quiet >= 25:
                quiet = 0
                yield {
                    "type": "HEARTBEAT",
                    "lastTransactionID": self._last(),
                    "time": self._clock.text(),
                }
            await asyncio.sleep(0.2)

    # ── For tests and the e2e ───────────────────────────────────────────

    def orders(self) -> list[Json]:
        """Every order it holds, as placed."""
        return [
            dict(o) for o in sorted(self._state["orders"].values(), key=lambda o: int(o["id"]))
        ]

    def client_ids(self) -> list[str]:
        """The client order ID of each order placed with one, as many times
        as orders carry it: a placement sent twice shows twice."""
        return [
            str((o.get("clientExtensions") or {}).get("id"))
            for o in self.orders()
            if (o.get("clientExtensions") or {}).get("id") and not o.get("replacesOrderID")
        ]

    def transactions(self) -> list[Json]:
        return [dict(t) for t in self._state["transactions"]]

    def trading_day(self) -> date:
        return moments.trading_day(self._clock.ns())


def state_file(storage: Path | None) -> Path | None:
    """Where the synthetic account is kept: in the plugin's storage."""
    return storage / "synthetic-oanda" / "state.json" if storage is not None else None


def ask(storage: Path, request: Json) -> None:
    """Ask the synthetic account in `storage` for an order in OANDA's own
    app (`{"order": {"instrument": ..., "units": ...}}`), a roll
    (`{"roll": true}`) or its clock set (`{"clock": "<RFC 3339 time>"}`),
    taken on its next step: the e2e's way in."""
    held = state_file(storage)
    assert held is not None
    inbox = held.parent / "app"
    inbox.mkdir(parents=True, exist_ok=True)
    made, written = tempfile.mkstemp(dir=inbox, suffix=".tmp")
    with os.fdopen(made, "w") as file:
        json.dump(request, file)
    os.replace(written, inbox / f"{moments.of(datetime.now().astimezone()):020d}.json")
