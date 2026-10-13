"""OANDA's raw records, kept in the plugin's own storage (decisions/028): what
OANDA answered, as received, behind every row and fact the plugin sends.

Three kinds (declaration.py), each kept in units the archive moves whole
(contract v16; archive.py):

- `responses`: each custody read of an account -- the account, its
  instruments, its transactions since the last read -- in the day it was
  read (`responses/<account>/<YYYY-MM-DD>/<read>.json.gz`);
- `trading`: each exchange with OANDA's order endpoints, and each
  transaction the trading work converted, in the month it was made
  (`trading/<account>/<YYYY-MM>/<key>.json.gz`, approved 2026-10-13);
- `activity`: each transaction custody reported as an activity, its own
  record, in the month it was received
  (`activity/<account>/<YYYY-MM>/<transaction>.json.gz`).

A row's reference names its record by key: `<account>/<read>/<call>` for a
read, `trading/<account>/<key>/<call>` for an exchange, and
`activity/<account>/<transaction>` for an activity. Nothing is read back from
here into what is sent; the Raw records tab and a reference followed are
the only readers.

**Never a credential.** `redact` replaces the token wherever it appears, and
any field named as a credential, before anything is written. OANDA's
answers carry none, and a request is kept by its method and path, never its
headers.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .settings import ACTIVITY, RESPONSES, TRADING

Json = dict[str, Any]

STAND_IN = Path(tempfile.gettempdir()) / "oanda" / "raw"
REDACTED = "[redacted]"
_SECRET_NAMES = frozenset({"token", "authorization", "access_token", "password", "secret"})
_SUFFIX = ".json.gz"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
LEDGER = "moved.json"

#: Each call a record may hold, by its name, with OANDA's method and path.
ACCOUNT = ("reading the account", "GET /v3/accounts/{accountID}")
INSTRUMENTS = ("reading instruments", "GET /v3/accounts/{accountID}/instruments")
TRANSACTIONS = ("reading transactions", "GET /v3/accounts/{accountID}/transactions/sinceid")
PLACE = ("placing an order", "POST /v3/accounts/{accountID}/orders")
ORDER = ("reading an order", "GET /v3/accounts/{accountID}/orders/{orderSpecifier}")
CANCEL = ("cancelling an order", "PUT /v3/accounts/{accountID}/orders/{orderSpecifier}/cancel")
REPLACE = ("replacing an order", "PUT /v3/accounts/{accountID}/orders/{orderSpecifier}")
STREAMED = ("a transaction", "GET /v3/accounts/{accountID}/transactions/stream")
OFFERED = ("stating what OANDA offers", "the plugin's own decision, nothing sent")
CALLS = {
    c[0]: c[1]
    for c in (
        ACCOUNT,
        INSTRUMENTS,
        TRANSACTIONS,
        PLACE,
        ORDER,
        CANCEL,
        REPLACE,
        STREAMED,
        OFFERED,
    )
}


def storage_root(granted: Path | None = None) -> Path:
    """The storage the deployment grants, or the stand-in where none is."""
    return granted if granted is not None else STAND_IN


def _secret_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SECRET_NAMES or lowered.endswith(("_token", "secret", "password"))


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    """`value` with every credential replaced: a field named as one, and any
    of `secrets` wherever it appears in a string."""
    hidden = [s for s in secrets if s]
    if isinstance(value, dict):
        return {
            k: REDACTED if _secret_name(str(k)) else redact(v, hidden) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, hidden) for v in value]
    if isinstance(value, str):
        for secret in hidden:
            value = value.replace(secret, REDACTED)
        return value
    return value


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:f}"
    return str(value)


def dumps(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, default=_default, indent=indent, sort_keys=True)


def call(asked: tuple[str, str], body: Any, note: str = "") -> Json:
    """One call as kept: its name, OANDA's method and path, and the answer."""
    made: Json = {"call": asked[0], "asked": asked[1], "body": body}
    if note:
        made["note"] = note
    return made


def failed(asked: tuple[str, str], said: str, answered: Any = None, note: str = "") -> Json:
    """A call that failed, as kept: what failed, and OANDA's body if any."""
    made: Json = {"call": asked[0], "asked": asked[1], "failed": said}
    if answered:
        made["body"] = answered
    if note:
        made["note"] = note
    return made


def read_key(at: datetime) -> str:
    return f"{at.astimezone(UTC):%Y%m%dT%H%M%S.%fZ}"


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", part) or "_"


@dataclass(frozen=True)
class Unit:
    """A unit of one kind: a directory of records the archive moves whole."""

    kind: str
    unit: str
    path: Path
    files: tuple[str, ...]
    record_count: int
    first_received_ns: int
    last_received_ns: int


@dataclass
class Record:
    """One kept record, read back for a page."""

    key: str
    kind: str
    account: str
    at: str
    document: Json = field(default_factory=dict)


class RawStore:
    """The plugin's raw records, under `root` (above). `storage` is the
    storage the deployment granted, or None for the stand-in, from which
    nothing is moved."""

    def __init__(self, root: Path, storage: Path | None = None) -> None:
        self.root = root
        self.storage = storage

    # ── Writing ─────────────────────────────────────────────────────────

    def _write(self, path: Path, record: Json, secrets: Sequence[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = gzip.compress(dumps(redact(record, secrets)).encode())
        made, written = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "wb") as file:
                file.write(data)
            os.replace(written, path)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise

    def _unique(self, directory: Path, key: str) -> str:
        made, n = key, 1
        while (directory / f"{made}{_SUFFIX}").exists():
            n += 1
            made = f"{key}-{n}"
        return made

    def keep_read(
        self,
        account: str,
        at: datetime,
        calls: list[Json],
        synthetic: bool,
        secrets: Sequence[str] = (),
    ) -> str:
        """A custody read of `account`, in its day; its key."""
        directory = self.root / RESPONSES / _safe(account) / f"{at.astimezone(UTC):%Y-%m-%d}"
        key = self._unique(directory, read_key(at))
        self._write(
            directory / f"{key}{_SUFFIX}",
            {
                "account": account,
                "read_at": at.isoformat(),
                "synthetic": synthetic,
                "calls": calls,
                "key": key,
            },
            secrets,
        )
        return key

    def keep_trading(
        self,
        account: str,
        at: datetime,
        calls: list[Json],
        synthetic: bool,
        secrets: Sequence[str] = (),
    ) -> str:
        """A trading exchange with OANDA, in its month; its key."""
        directory = self.root / TRADING / _safe(account) / f"{at.astimezone(UTC):%Y-%m}"
        key = self._unique(directory, read_key(at))
        self._write(
            directory / f"{key}{_SUFFIX}",
            {
                "account": account,
                "at": at.isoformat(),
                "synthetic": synthetic,
                "calls": calls,
                "key": key,
            },
            secrets,
        )
        return key

    def keep_activity(
        self,
        account: str,
        transaction: Json,
        at: datetime,
        synthetic: bool,
        secrets: Sequence[str] = (),
    ) -> str:
        """A transaction custody reports as an activity: written once, in the
        month it was first received; its key. Kept already, it is left."""
        transaction_id = _safe(str(transaction.get("id") or ""))
        held = self._activity_file(account, transaction_id)
        if held is None:
            directory = self.root / ACTIVITY / _safe(account) / f"{at.astimezone(UTC):%Y-%m}"
            self._write(
                directory / f"{transaction_id}{_SUFFIX}",
                {
                    "account": account,
                    "received_at": at.isoformat(),
                    "synthetic": synthetic,
                    "transaction": transaction,
                    "key": transaction_id,
                },
                secrets,
            )
        return f"{ACTIVITY}/{account}/{transaction_id}"

    def _activity_file(self, account: str, transaction_id: str) -> Path | None:
        directory = self.root / ACTIVITY / _safe(account)
        if not directory.is_dir():
            return None
        for month in sorted(directory.iterdir()):
            found = month / f"{transaction_id}{_SUFFIX}"
            if found.exists():
                return found
        return None

    # ── Reading ─────────────────────────────────────────────────────────

    @staticmethod
    def _read(path: Path) -> Json | None:
        with contextlib.suppress(OSError, ValueError, EOFError):
            held = json.loads(gzip.decompress(path.read_bytes()))
            return held if isinstance(held, dict) else None
        return None

    def find(self, key: str) -> Record | None:
        """The record a reference names, where it is still in storage."""
        parts = key.split("/")
        if parts[0] == ACTIVITY and len(parts) >= 3:
            path = self._activity_file(parts[1], _safe(parts[2]))
            held = self._read(path) if path is not None else None
            if held is None:
                return None
            return Record(key, ACTIVITY, parts[1], str(held.get("received_at", "")), held)
        kind, rest = (TRADING, parts[1:]) if parts[0] == TRADING else (RESPONSES, parts)
        if len(rest) < 2:
            return None
        account, read = rest[0], rest[1]
        directory = self.root / kind / _safe(account)
        if not directory.is_dir():
            return None
        for period in sorted(directory.iterdir(), reverse=True):
            path = period / f"{read}{_SUFFIX}"
            if path.exists():
                held = self._read(path)
                if held is None:
                    return None
                at = str(held.get("read_at") or held.get("at") or "")
                return Record(key, kind, account, at, held)
        return None

    def latest(self, kind: str, account: str, most: int = 20) -> list[Record]:
        """The newest records of a kind for an account, newest first."""
        directory = self.root / kind / _safe(account)
        if not directory.is_dir():
            return []
        found: list[Record] = []
        for period in sorted(directory.iterdir(), reverse=True):
            if not period.is_dir():
                continue
            for path in sorted(period.glob(f"*{_SUFFIX}"), reverse=True):
                held = self._read(path)
                if held is None:
                    continue
                key = path.name.removesuffix(_SUFFIX)
                at = str(held.get("read_at") or held.get("at") or held.get("received_at") or "")
                prefix = {TRADING: f"{TRADING}/{account}/", ACTIVITY: f"{ACTIVITY}/{account}/"}
                found.append(
                    Record(prefix.get(kind, f"{account}/") + key, kind, account, at, held)
                )
                if len(found) >= most:
                    return found
        return found

    def accounts(self) -> list[str]:
        """Every account a record is kept for."""
        named: set[str] = set()
        for kind in (RESPONSES, TRADING, ACTIVITY):
            directory = self.root / kind
            if directory.is_dir():
                named.update(p.name for p in directory.iterdir() if p.is_dir())
        return sorted(named)

    # ── Units, for the archive ──────────────────────────────────────────

    def units(self, kind: str) -> list[Unit]:
        """Each unit of a kind in storage, oldest first."""
        directory = self.root / kind
        if not directory.is_dir():
            return []
        pattern = _DAY if kind == RESPONSES else _MONTH
        found: list[Unit] = []
        for account in sorted(p for p in directory.iterdir() if p.is_dir()):
            for period in sorted(p for p in account.iterdir() if p.is_dir()):
                if not pattern.match(period.name):
                    continue
                files = sorted(p.name for p in period.glob(f"*{_SUFFIX}"))
                if not files:
                    continue
                times = [int(os.stat(period / f).st_mtime_ns) for f in files]
                unit = f"{kind}/{account.name}/{period.name}"
                found.append(
                    Unit(kind, unit, period, tuple(files), len(files), min(times), max(times))
                )
        return found

    def note(self, unit: Unit, now: datetime) -> None:
        """A unit about to be moved, noted in its account's ledger first."""
        ledger = unit.path.parent / LEDGER
        held: Json = {}
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(ledger.read_text())
            held = loaded if isinstance(loaded, dict) else {}
        held[unit.unit] = {
            "files": list(unit.files),
            "noted_at": now.isoformat(),
            "record_count": unit.record_count,
        }
        ledger.write_text(json.dumps(held, sort_keys=True))

    def noted(self, unit: Unit) -> tuple[str, ...] | None:
        ledger = unit.path.parent / LEDGER
        with contextlib.suppress(OSError, ValueError):
            held = json.loads(ledger.read_text())
            entry = held.get(unit.unit) if isinstance(held, dict) else None
            if isinstance(entry, dict):
                return tuple(entry.get("files") or ())
        return None
