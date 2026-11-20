"""A placement sent at most once across a takeover, and the plugin's storage
read once registered (open-meridian 0.37.0): the SDK's markers
(`connectivity.Sent`, `state/sent/<placement>`), the `state/sent.json` an
earlier release wrote, and a shard's own folder. SIMULATED ONLY."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from meridian import edge
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from oanda.__main__ import Oanda

from rigs import TradeRig, deliver, placement


def test_a_takeover_mid_send_sends_the_placement_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pod A, deposed and not knowing it yet, is mid-send; pod B took its
    shard, read its files before A noted the placement, and reads the book,
    which still holds the placement queued: B never sends it again."""
    recorder = Recorder("oanda")
    sends: list[Any] = []

    async def work() -> None:
        rig = TradeRig(recorder)
        await rig.started()
        holder = rig.trader
        # The pod taking the shard over, on the same shard's folder: its
        # files read before the holder noted the placement.
        taking = rig.make()
        await taking.start()
        held = placement()
        rig.holds(held)
        sending, through = asyncio.Event(), asyncio.Event()
        place = rig.venue.place

        async def slow(account_id: str, body: Any) -> Any:
            sends.append(body)
            if len(sends) == 1:
                # The holder's send, mid-flight.
                sending.set()
                await through.wait()
            return await place(account_id, body)

        monkeypatch.setattr(rig.venue, "place", slow)
        rig.trader = holder
        mid_send = asyncio.create_task(rig.hear(held))
        await sending.wait()
        await deliver(
            recorder,
            taking,
            "PlacementUpdated",
            [ops.PlacementUpdatedEvent(placement=held)],
        )
        through.set()
        await mid_send
        await taking.poll()
        assert held.placement_id in taking.working, "asked about by its client ID"

    asyncio.run(work())
    assert len(sends) == 1, "sent once, by the pod that noted it"


def test_the_sent_json_an_earlier_release_wrote_is_honoured(tmp_path: Path) -> None:
    """0.9.1 kept the placements sent whole in `state/sent.json`: after the
    upgrade a placement it names, still queued in the book, is asked about,
    never sent."""
    recorder = Recorder("oanda")
    held = placement()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "sent.json").write_text(json.dumps([held.placement_id]))

    async def work() -> None:
        rig = TradeRig(recorder, storage=tmp_path)
        await rig.started()
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()
        assert rig.venue.client_ids().count(held.placement_id) == 0, "never sent"
        assert rig.trader.working[held.placement_id].unanswered

    asyncio.run(work())
    # The file is read, never written again; a new placement is a marker.
    assert json.loads((tmp_path / "state" / "sent.json").read_text()) == [held.placement_id]


def test_a_new_placement_is_noted_as_a_marker_of_its_own(tmp_path: Path) -> None:
    recorder = Recorder("oanda")
    held = placement()

    async def work() -> None:
        rig = TradeRig(recorder, storage=tmp_path)
        await rig.started()
        rig.holds(held)
        await rig.hear(held)

    asyncio.run(work())
    assert (tmp_path / "state" / "sent" / held.placement_id).is_file()
    assert not (tmp_path / "state" / "sent.json").exists()


def test_the_plugin_reads_its_storage_once_registered_and_afresh_when_registered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On an instance run as shards, storage read before Register raises:
    the plugin is made reading none, and once registered reads its shard's
    own folder -- another shard's when registered again as another."""
    monkeypatch.setenv(edge.STORAGE_DIR, str(tmp_path))
    monkeypatch.setenv("MERIDIAN_PLUGIN_SHARDS", "2")
    monkeypatch.setattr(edge, "_serving", None)
    for shard, sent in ((0, "PLC-shard-0"), (1, "PLC-shard-1")):
        folder = tmp_path / f"shard-{shard}" / "state"
        folder.mkdir(parents=True)
        (folder / "sent.json").write_text(json.dumps([sent]))
    shared = Oanda()

    edge._registered_as(0, 2)
    shared.open_storage()
    assert "PLC-shard-0" in shared.trading._sent
    assert "PLC-shard-1" not in shared.trading._sent
    assert shared.raw.storage == tmp_path / "shard-0"

    # Deposed, then admitted again as the other shard.
    shared.stopped()
    edge._registered_as(1, 2)
    shared.open_storage()
    assert "PLC-shard-1" in shared.trading._sent
    assert "PLC-shard-0" not in shared.trading._sent
    assert shared.raw.storage == tmp_path / "shard-1"
