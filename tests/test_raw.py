"""The raw records (raw.py): kept by kind, found by a row's reference, and
never a credential."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path

from oanda.raw import PLACE, REDACTED, RawStore, call, redact

AT = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
ACCOUNT = "101-001-1-001"


def test_a_credential_is_redacted_wherever_it_is() -> None:
    said = redact(
        {"token": "x", "note": "Bearer s3cr3t-value", "inner": ["s3cr3t-value"]},
        ["s3cr3t-value"],
    )
    assert said == {"token": REDACTED, "note": f"Bearer {REDACTED}", "inner": [REDACTED]}


def test_each_kind_kept_and_found_by_its_reference(tmp_path: Path) -> None:
    store = RawStore(tmp_path, tmp_path)
    read = store.keep_read(ACCOUNT, AT, [call(PLACE, {"a": 1})], True, ["s3cr3t-value"])
    trading = store.keep_trading(
        ACCOUNT, AT, [call(PLACE, {"note": "s3cr3t-value"})], True, ["s3cr3t-value"]
    )
    activity = store.keep_activity(ACCOUNT, {"id": "42", "type": "DAILY_FINANCING"}, AT, True)
    assert activity == f"activity/{ACCOUNT}/42"
    found = store.find(f"{ACCOUNT}/{read}/account")
    assert found is not None and found.kind == "responses"
    kept = store.find(f"trading/{ACCOUNT}/{trading}/place")
    assert kept is not None and "s3cr3t-value" not in str(kept.document)
    assert store.find(activity) is not None
    for path in tmp_path.rglob("*.json.gz"):
        assert b"s3cr3t-value" not in gzip.decompress(path.read_bytes())
    units = {
        kind: [u.unit for u in store.units(kind)]
        for kind in ("responses", "trading", "activity")
    }
    assert units == {
        "responses": [f"responses/{ACCOUNT}/2026-10-13"],
        "trading": [f"trading/{ACCOUNT}/2026-10"],
        "activity": [f"activity/{ACCOUNT}/2026-10"],
    }


def test_an_activity_is_written_once(tmp_path: Path) -> None:
    store = RawStore(tmp_path, tmp_path)
    store.keep_activity(ACCOUNT, {"id": "7", "amount": "1"}, AT, True)
    store.keep_activity(ACCOUNT, {"id": "7", "amount": "2"}, AT, True)
    found = store.find(f"activity/{ACCOUNT}/7")
    assert found is not None and found.document["transaction"]["amount"] == "1"
