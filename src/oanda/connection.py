"""The plugin's one connection to OANDA: an environment and an account, the
venue that reaches it, and whether trading is enabled on it.

**A new connection is read-only** until an admin of the plugin's
`connectivity` role enables trading on it (as SnapTrade's are; the order
path's step 5, Q10), on Connections, "Enable trading" (`enable_trading`,
`/admin/connections/enable-trading`, the names approved for SnapTrade). A
connection is its environment and account: a change of either in the
settings is a new connection, read-only again. Which connections trading is
enabled on, by whom and when, is kept in the plugin's storage
(`state/connections.json`), so a restart finds it.

Sample data's connection is the synthetic OANDA (synthetic.py), whatever the
settings name; nothing in it reaches OANDA.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import synthetic
from .kept import Kept, file_in
from .settings import Config
from .venue import Oanda, Venue

SYNTHETIC = "synthetic"


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


class Connections:
    """The connection the settings name, made again when they change."""

    def __init__(self, storage: Path | None, now: Callable[[], datetime]) -> None:
        self._storage = storage
        self._now = now
        self._enabled = Kept(file_in(storage, "connections.json"), {})
        self._synthetic: synthetic.SyntheticOanda | None = None
        self._real: tuple[tuple[str, str], Oanda] | None = None
        self.config = Config()

    def configure(self, config: Config) -> None:
        self.config = config

    def _venue(self) -> tuple[Venue, str, str, bool] | None:
        config = self.config
        if config.synthetic:
            if self._synthetic is None:
                self._synthetic = synthetic.SyntheticOanda(
                    self._now, synthetic.state_file(self._storage)
                )
            return self._synthetic, synthetic.ACCOUNT_ID, SYNTHETIC, True
        credentials = config.credentials
        if credentials is None:
            return None
        key = (credentials.token, credentials.environment)
        if self._real is None or self._real[0] != key:
            self._real = (key, Oanda(credentials.token, credentials.environment))
        return self._real[1], credentials.account_id, credentials.environment, False

    @property
    def current(self) -> Connection | None:
        made = self._venue()
        if made is None:
            return None
        venue, account_id, environment, is_synthetic = made
        connection_id = f"{environment}:{account_id}"
        return Connection(
            connection_id=connection_id,
            account_id=account_id,
            environment=environment,
            venue=venue,
            synthetic=is_synthetic,
            trading=connection_id in self._enabled.value,
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

    def enabled_by(self, connection_id: str) -> tuple[str, str]:
        held = self._enabled.value.get(connection_id) or {}
        return str(held.get("by", "")), str(held.get("at", ""))
