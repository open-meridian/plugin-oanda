"""The pair map: each instrument OANDA quotes, to the two currencies' cash
instruments it is a price between (ruled 2026-10-13, the spot-FX plugin's
Q6: no pair record; the plugin keeps its own map, as Kraken's does).

A pair is not an instrument of the deployment's ("a spot pair is not an
instrument", plans/the-lake-prices-the-book): `EUR_USD` is EUR's cash
instrument priced in USD's, and a price is recorded as OANDA quotes it,
never inverted or crossed. Each entry keeps what OANDA states of the
instrument -- its type, `pipLocation`, `displayPrecision`,
`tradeUnitsPrecision` and `minimumTradeSize` -- as venue metadata, and the
deployment's cash instrument for each side once resolved (by its ISO 4217
code, W3.1; the deployment pulls a currency from the platform, never mints
one). It is kept in the plugin's storage, read again from OANDA's
instrument list on start and whenever an instrument is not found.

`find(base, quote)` answers the instrument OANDA quotes the pair as, only in
that order: an order in USD's cash instrument against EUR is not EUR_USD
turned round, and is rejected `unsupported` with why (trading.py).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

#: OANDA's instrument types this plugin converts: a currency pair. A metal
#: (`METAL`) or a CFD (`CFD`) has no asset class here, and is not traded.
CURRENCY = "CURRENCY"


@dataclass(frozen=True)
class Pair:
    """One instrument OANDA quotes, and what it is between."""

    name: str
    base: str
    quote: str
    kind: str = CURRENCY
    pip_location: int = -4
    display_precision: int = 5
    units_precision: int = 0
    minimum_units: str = "1"
    # The deployment's cash instrument of each side, once resolved.
    base_instrument: str = ""
    quote_instrument: str = ""

    @property
    def tradeable(self) -> bool:
        return self.kind == CURRENCY

    def price_text(self, price: Decimal) -> str:
        """A price as OANDA takes it: the decimal as given, never rounded
        here (OANDA refuses one finer than its precision, as reported)."""
        return f"{price:f}"


def _pair(stated: dict[str, Any]) -> Pair | None:
    name = str(stated.get("name") or "")
    base, _, quote = name.partition("_")
    if not base or not quote:
        return None
    try:
        return Pair(
            name=name,
            base=base,
            quote=quote,
            kind=str(stated.get("type") or ""),
            pip_location=int(stated.get("pipLocation", -4)),
            display_precision=int(stated.get("displayPrecision", 5)),
            units_precision=int(stated.get("tradeUnitsPrecision", 0)),
            minimum_units=str(stated.get("minimumTradeSize") or "1"),
        )
    except (TypeError, ValueError):
        return None


class PairMap:
    """The pair map, kept in `file` where storage is granted."""

    def __init__(self, file: Path | None = None) -> None:
        self._file = file
        self._pairs: dict[str, Pair] = {}
        if file is not None and file.exists():
            with contextlib.suppress(OSError, ValueError, TypeError):
                for held in json.loads(file.read_text()):
                    pair = Pair(**held)
                    self._pairs[pair.name] = pair

    def __len__(self) -> int:
        return len(self._pairs)

    def __contains__(self, name: object) -> bool:
        return name in self._pairs

    def pairs(self) -> list[Pair]:
        return [self._pairs[name] for name in sorted(self._pairs)]

    def get(self, name: str) -> Pair | None:
        return self._pairs.get(name)

    def find(self, base: str, quote: str) -> Pair | None:
        """The instrument OANDA quotes as base_quote, or None."""
        return self._pairs.get(f"{base}_{quote}")

    def quoted_the_other_way(self, base: str, quote: str) -> Pair | None:
        return self._pairs.get(f"{quote}_{base}")

    def take(self, instruments: list[dict[str, Any]]) -> None:
        """OANDA's instrument list, as read now: each entry's metadata
        replaced, its resolved cash instruments kept."""
        for stated in instruments:
            pair = _pair(stated)
            if pair is None:
                continue
            held = self._pairs.get(pair.name)
            if held is not None:
                pair = replace(
                    pair,
                    base_instrument=held.base_instrument,
                    quote_instrument=held.quote_instrument,
                )
            self._pairs[pair.name] = pair
        self._save()

    def resolved(self, name: str, base_instrument: str, quote_instrument: str) -> Pair | None:
        """An entry with its two cash instruments, as the deployment named
        them."""
        held = self._pairs.get(name)
        if held is None:
            return None
        made = replace(held, base_instrument=base_instrument, quote_instrument=quote_instrument)
        if made != held:
            self._pairs[name] = made
            self._save()
        return made

    def _save(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        made, written = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "w") as file:
                json.dump([asdict(p) for p in self.pairs()], file, sort_keys=True)
            os.replace(written, self._file)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise


def pairs_file(storage: Path | None) -> Path | None:
    """Where the pair map is kept: in the plugin's storage."""
    return storage / "state" / "pairs.json" if storage is not None else None
