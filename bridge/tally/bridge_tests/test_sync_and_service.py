"""The sync engine and the loop against a fake platform: batching, resuming, 413 splitting, refusals, company
changes, backoff, and the loop's reaction to "read again" and to being unpaired."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tally_bridge.dev.stand_in import StandInTally, StandInTallyTransport, build_company
from tally_bridge.platform_client import (
    PlatformRefused,
    PlatformUnavailable,
    PollAnswer,
    Settings,
    Unauthorized,
)
from tally_bridge.service import MAX_BACKOFF_SECONDS, Bridge, backoff_seconds
from tally_bridge.store import Store
from tally_bridge.sync import SyncAborted, SyncEngine
from tally_bridge.tally_client import TallyClient


@dataclass
class FakePlatform:
    """Stores batches like the platform: by (sync, dataset, seq), duplicates ignored."""

    batches: dict[tuple[str, str, int], list[dict[str, Any]]] = field(default_factory=dict)
    completed: list[dict[str, Any]] = field(default_factory=list)
    fail: list[Exception] = field(default_factory=list)
    max_rows: int | None = None
    polls: list[PollAnswer] = field(default_factory=list)

    def send_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        if self.fail:
            raise self.fail.pop(0)
        if self.max_rows is not None and len(batch["records"]) > self.max_rows:
            raise PlatformRefused(413, "batch_too_large", "too large", {})
        key = (batch["sync_id"], batch["dataset"], batch["seq"])
        duplicate = key in self.batches
        self.batches.setdefault(key, batch["records"])
        return {"accepted": len(batch["records"]), "duplicate": duplicate}

    def complete(self, sync_id: str, body: dict[str, Any]) -> dict[str, Any]:
        for name, d in body["datasets"].items():
            got = {seq for (s, ds, seq) in self.batches if s == sync_id and ds == name}
            assert got == set(range(d["batches"])), name
        self.completed.append(body)
        return {"status": "complete", "removed": 0}

    def poll(self, status: dict[str, Any], *, wait_seconds: int) -> PollAnswer:
        return self.polls.pop(0) if self.polls else PollAnswer(False, False, Settings())

    def keys(self, name: str) -> list[str]:
        return [r["key"] for (_, ds, _), rows in self.batches.items() if ds == name for r in rows]


NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def tally() -> StandInTally:
    return StandInTally(build_company(None))


def engine(tally: StandInTally, platform: FakePlatform, home: Path, **kwargs: Any) -> SyncEngine:
    return SyncEngine(
        TallyClient(transport=StandInTallyTransport(tally)),
        platform,  # type: ignore[arg-type]
        Store(home),
        settings=kwargs.pop("settings", Settings(max_batch_rows=50)),
        clock=kwargs.pop("clock", lambda: NOW),
        sleep=lambda _: None,
        jitter=lambda: 0.5,
        **kwargs,
    )


def test_a_full_sync_sends_everything_once_in_small_batches(
    tally: StandInTally, tmp_path: Path
) -> None:
    platform = FakePlatform()
    report = engine(tally, platform, tmp_path).sync()
    assert report.mode == "full"
    vouchers = platform.keys("vouchers")
    assert len(vouchers) == len(set(vouchers)) == len(tally.company.vouchers)
    assert all(len(rows) <= 50 for rows in platform.batches.values())
    assert platform.completed[0]["datasets"]["vouchers"]["whole"] is True
    ledgers = platform.keys("ledgers")
    assert len(ledgers) == len(tally.company.ledgers)
    state = Store(tmp_path).state()
    assert state.pending is None and state.last_alter_id == max(
        v.alter_id for v in tally.company.vouchers.values()
    )


def test_the_next_sync_is_incremental_and_whole_only_for_masters(
    tally: StandInTally, tmp_path: Path
) -> None:
    platform = FakePlatform()
    engine(tally, platform, tmp_path).sync()
    tally.company.add_sale(NOW.date(), "West", "Face Serum", "Beauty", 500.0)
    report = engine(tally, platform, tmp_path, clock=lambda: NOW + timedelta(minutes=15)).sync()
    assert report.mode == "incremental"
    assert report.records["vouchers"] == 1
    last = platform.completed[-1]["datasets"]
    assert last["vouchers"]["whole"] is False and last["ledgers"]["whole"] is True


def test_a_full_read_is_due_after_a_day(tally: StandInTally, tmp_path: Path) -> None:
    platform = FakePlatform()
    engine(tally, platform, tmp_path).sync()
    report = engine(tally, platform, tmp_path, clock=lambda: NOW + timedelta(hours=25)).sync()
    assert report.mode == "full"


def test_a_batch_that_is_too_large_is_split(tally: StandInTally, tmp_path: Path) -> None:
    platform = FakePlatform(max_rows=20)
    engine(tally, platform, tmp_path).sync()
    assert len(platform.keys("vouchers")) == len(tally.company.vouchers)


def test_a_refusal_aborts_and_the_next_sync_starts_over(
    tally: StandInTally, tmp_path: Path
) -> None:
    platform = FakePlatform(fail=[PlatformRefused(409, "company_mismatch", "another company", {})])
    with pytest.raises(SyncAborted) as caught:
        engine(tally, platform, tmp_path).sync()
    assert caught.value.code == "company_mismatch"
    store = Store(tmp_path)
    assert store.state().pending is None and store.outbox() is None
    assert engine(tally, platform, tmp_path).sync().resumed is False


def test_an_outbox_from_another_sync_is_dropped(tally: StandInTally, tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_outbox({"sync_id": "old", "seq": 0, "dataset": "ledgers", "records": [{"key": "x"}]})
    platform = FakePlatform()
    engine(tally, platform, tmp_path).sync()
    assert "x" not in platform.keys("ledgers")


def test_another_company_in_tally_starts_a_fresh_full_read(
    tally: StandInTally, tmp_path: Path
) -> None:
    platform = FakePlatform()
    engine(tally, platform, tmp_path).sync()
    tally.company.guid = "another-company-guid-000000000000"
    report = engine(tally, platform, tmp_path, clock=lambda: NOW + timedelta(minutes=15)).sync()
    assert report.mode == "full"


def test_no_company_open(tally: StandInTally, tmp_path: Path) -> None:
    tally.no_company = True
    with pytest.raises(Exception):  # noqa: B017 - Tally's own error answer
        engine(tally, FakePlatform(), tmp_path).sync()


def test_a_chosen_company_must_be_open(tally: StandInTally, tmp_path: Path) -> None:
    with pytest.raises(SyncAborted) as caught:
        engine(tally, FakePlatform(), tmp_path, company="Someone Else").sync()
    assert caught.value.code == "company_not_open"


def test_backoff_grows_and_stops_at_fifteen_minutes() -> None:
    waits = [backoff_seconds(n, 0.5) for n in range(1, 12)]
    assert waits[0] == 30.0 and waits[1] == 60.0
    assert max(waits) == MAX_BACKOFF_SECONDS
    assert backoff_seconds(20, 1.0) <= MAX_BACKOFF_SECONDS * 1.5


def make_bridge(
    tally: StandInTally, platform: FakePlatform, home: Path, clock: list[datetime]
) -> Bridge:
    return Bridge(
        TallyClient(transport=StandInTallyTransport(tally)),
        platform,  # type: ignore[arg-type]
        Store(home),
        settings=Settings(max_batch_rows=500, poll_wait_seconds=0),
        clock=lambda: clock[0],
        jitter=lambda: 0.5,
    )


def test_tally_closed_is_reported_and_retried(tally: StandInTally, tmp_path: Path) -> None:
    clock = [NOW]
    bridge = make_bridge(tally, FakePlatform(), tmp_path, clock)
    tally.offline = True
    assert bridge.sync_once(sleep=lambda _: None) is False
    assert bridge.status.tally == "unreachable"
    assert bridge.next_sync_at == NOW + timedelta(minutes=5)
    tally.offline = False
    assert bridge.sync_once(sleep=lambda _: None) is True
    assert bridge.status.as_json(bridge.next_sync_at)["tally"] == "ok"


def test_the_platform_down_keeps_progress_and_backs_off(
    tally: StandInTally, tmp_path: Path
) -> None:
    clock = [NOW]
    platform = FakePlatform(fail=[PlatformUnavailable("x")] * 6)
    bridge = make_bridge(tally, platform, tmp_path, clock)
    assert bridge.sync_once(sleep=lambda _: None) is False
    assert bridge.status.last_error == "platform_unreachable"
    assert Store(tmp_path).state().pending is not None
    assert bridge.sync_once(sleep=lambda _: None) is True


def test_the_loop_syncs_reads_again_when_asked_and_stops_when_unpaired(
    tally: StandInTally, tmp_path: Path
) -> None:
    clock = [NOW]
    platform = FakePlatform()
    platform.polls = [PollAnswer(True, False, Settings(poll_wait_seconds=0))]
    bridge = make_bridge(tally, platform, tmp_path, clock)
    calls = {"poll": 0}
    original = platform.poll

    def poll(status: dict[str, Any], *, wait_seconds: int) -> PollAnswer:
        calls["poll"] += 1
        if calls["poll"] > 2:
            raise Unauthorized("unpaired")
        return original(status, wait_seconds=wait_seconds)

    platform.poll = poll  # type: ignore[method-assign]
    bridge.run(threading.Event())
    assert bridge.unpaired
    assert len(platform.completed) == 2  # the first sync, then the one the platform asked for
