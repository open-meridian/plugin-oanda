"""`make e2e`'s clock for the order path (e2e/orders.py): a Tuesday whose
roll has a date to move to on the harness's own calendars. Started on
Wednesday 18 November 2026, the coming Tuesday, the 24th, spots on Friday
the 27th (Thanksgiving, the 26th, closes the US dollar), and so does its
roll at 17:00 New York, Wednesday's spot date: nothing to roll, and the
e2e's step 5 waited for a roll that never came (PR #13's first CI run)."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

ORDERS = Path(__file__).resolve().parent.parent / "e2e" / "orders.py"


def orders() -> Any:
    spec = importlib.util.spec_from_file_location("e2e_orders", ORDERS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def calendar(calendar_id: str, closed: list[str]) -> dict[str, Any]:
    return {
        "calendar_id": calendar_id,
        "weekends": [{"weekdays": ["WEEKDAY_SATURDAY", "WEEKDAY_SUNDAY"]}],
        "days": [{"date": day, "kind": "CALENDAR_DAY_KIND_CLOSED"} for day in closed],
    }


#: What the harness's platform answers for EUR/USD's spot-date rule
#: (meridian-harness aae501d, platform.py's SPOT_RULES and CALENDARS), its
#: closed days cut to the weeks these tests reach.
SERVED = {
    "rules": [
        {
            "spot": {
                "lag_days": 2,
                "counting_calendars": ["CAL-01JAE000000000000000EUR1"],
                "spot_calendars": [
                    "CAL-01JAE000000000000000EUR1",
                    "CAL-01JAE000000000000000USD1",
                ],
                "tenor_rule": {"convention": "BUSINESS_DAY_CONVENTION_MODIFIED_FOLLOWING"},
            }
        }
    ],
    "calendars": [
        calendar("CAL-01JAE000000000000000EUR1", ["2026-12-25", "2027-01-01"]),
        calendar(
            "CAL-01JAE000000000000000USD1",
            ["2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"],
        ),
    ],
}

NEW_YORK = ZoneInfo("America/New_York")


def test_a_week_whose_tuesday_and_wednesday_spot_together_is_passed_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = orders()
    monkeypatch.setattr(held, "spot_rule", lambda: SERVED, raising=False)
    start = datetime(2026, 11, 18, 9, 0, tzinfo=NEW_YORK)
    # Tuesday 1 December, 12:00 New York, not the 24th.
    assert held.controlled_clock(start) == "2026-12-01T17:00:00Z"
    assert held.spot_date(date(2026, 11, 24), SERVED) == date(2026, 11, 27)
    assert held.spot_date(date(2026, 11, 25), SERVED) == date(2026, 11, 27)
    assert held.spot_date(date(2026, 12, 1), SERVED) == date(2026, 12, 3)
    assert held.spot_date(date(2026, 12, 2), SERVED) == date(2026, 12, 4)


def test_an_ordinary_week_keeps_its_coming_tuesday(monkeypatch: pytest.MonkeyPatch) -> None:
    held = orders()
    monkeypatch.setattr(held, "spot_rule", lambda: SERVED, raising=False)
    start = datetime(2026, 11, 2, 9, 0, tzinfo=NEW_YORK)
    assert held.controlled_clock(start) == "2026-11-03T17:00:00Z"
