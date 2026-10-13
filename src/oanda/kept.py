"""Small JSON files the plugin keeps in its storage, each written whole: what
a restart must find again (the placements sent, the trading cursor, the
trades a placement opened, the connections trading is enabled on). With no
storage granted (a test) each lives in the process alone.

They live under `<storage>/state/`, apart from the raw records' kinds
(raw.py), so the archive never moves one.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class Kept:
    """One JSON document, read once and written whole on every change."""

    def __init__(self, file: Path | None, empty: Any) -> None:
        self._file = file
        self.value: Any = empty
        if file is not None and file.exists():
            with contextlib.suppress(OSError, ValueError):
                held = json.loads(file.read_text())
                if isinstance(held, type(empty)):
                    self.value = held

    def save(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        made, written = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "w") as file:
                json.dump(self.value, file, sort_keys=True)
            os.replace(written, self._file)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise


def state_dir(storage: Path | None) -> Path | None:
    return storage / "state" if storage is not None else None


def file_in(storage: Path | None, name: str) -> Path | None:
    held = state_dir(storage)
    return held / name if held is not None else None
