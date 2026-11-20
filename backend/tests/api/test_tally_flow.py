"""The whole Tally path (M16): an Owner adds Tally and gets a code, the **real** bridge pairs, reads the Tally
stand-in (export requests only), sends batches, completes the sync, and the connection's tables appear, typed, in
rupees, with the freshness of the last push. Then incremental reads, alterations, deletions, resuming after a
crash and the Connections screen's status."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession
from tally_bridge.dev.stand_in import StandInTally, build_company
from tally_bridge.platform_client import PlatformUnavailable, Settings
from tally_bridge.sync import SyncInterrupted

from app.connectors.registry import ConnectorRuntime
from app.data.cache import DatasetCache
from app.models import Dataset, TallyRecord
from tests.conftest import FakeClock
from tests.tallybridge import PairedBridge, pair
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Any, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def paired(client: TestClient, world: World, tmp_path: Path) -> PairedBridge:
    return pair(client, world, tmp_path / "bridge")


def detail(client: TestClient, world: World, paired: PairedBridge) -> dict[str, Any]:
    response = client.get(f"/connections/{paired.connection_id}", headers=world.headers("owner_a"))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def rows(db: DbSession, paired: PairedBridge, dataset: str) -> int:
    db.expire_all()
    return int(
        db.scalar(
            select(func.count())
            .select_from(TallyRecord)
            .where(
                TallyRecord.connection_id == paired.connection_id, TallyRecord.dataset == dataset
            )
        )
        or 0
    )


def frame(
    runtime: ConnectorRuntime, world: World, db: DbSession, paired: PairedBridge, table: str
) -> Any:
    db.expire_all()
    dataset = db.scalar(
        select(Dataset).where(
            Dataset.connection_id == paired.connection_id, Dataset.external_ref == table
        )
    )
    assert dataset is not None, table
    return DatasetCache(runtime, world.org_a.id).get(dataset.id)


def test_before_the_first_sync_the_pc_is_paired_and_waiting(
    client: TestClient, world: World, paired: PairedBridge
) -> None:
    body = detail(client, world, paired)
    assert body["type"] == "tally"
    assert body["tally"]["state"] == "first_sync"
    assert body["tally"]["device_name"] == "ACCOUNTS-PC"
    assert body["datasets"] == []


def test_a_full_sync_lands_as_typed_tables(
    client: TestClient,
    world: World,
    paired: PairedBridge,
    db: DbSession,
    runtime: ConnectorRuntime,
    clock: FakeClock,
) -> None:
    report = paired.engine().sync()
    assert report.mode == "full"
    assert paired.tally.refused == []  # never anything but exports
    body = detail(client, world, paired)
    assert body["status"] == "active"
    assert body["tally"]["state"] == "connected"
    tables = {d["name"]: d for d in body["datasets"]}
    assert set(tables) == {
        "groups",
        "ledgers",
        "stock_items",
        "vouchers",
        "voucher_ledger_entries",
        "voucher_inventory_entries",
        "trial_balance",
    }
    company = paired.tally.company
    in_books = [v for v in company.vouchers.values() if not v.cancelled and not v.optional]
    assert tables["vouchers"]["row_count"] == len(in_books)
    # Money is in rupees (declared, never divided) and dates are dates.
    columns = {c["name"]: c for c in tables["vouchers"]["columns"]}
    assert columns["amount"]["unit"] == "INR"
    assert columns["date"]["type"] == "date"
    vouchers = frame(runtime, world, db, paired, "vouchers").frame
    sales = vouchers[vouchers["voucher_class"] == "sales"]
    expected = sum(i.amount for v in in_books for i in v.items if v.type_name == "Online Sales")
    assert sales["amount"].sum() == pytest.approx(expected, abs=0.05)
    assert set(vouchers["voucher_type"]) >= {"Online Sales", "Receipt", "Purchase", "Payment"}
    # Freshness is the push, not the moment the table was built.
    cached = frame(runtime, world, db, paired, "vouchers")
    assert cached.fetched_at == clock.now


def test_debits_equal_credits_and_the_trial_balance_balances(
    client: TestClient, world: World, paired: PairedBridge, db: DbSession, runtime: ConnectorRuntime
) -> None:
    paired.engine().sync()
    entries = frame(runtime, world, db, paired, "voucher_ledger_entries").frame
    assert entries["debit"].sum() == pytest.approx(entries["credit"].sum(), abs=0.05)
    by_voucher = entries.groupby("voucher_guid")[["debit", "credit"]].sum()
    assert (by_voucher["debit"] - by_voucher["credit"]).abs().max() < 0.01
    trial = frame(runtime, world, db, paired, "trial_balance").frame
    assert trial["closing_debit"].sum() == pytest.approx(trial["closing_credit"].sum(), abs=0.05)
    assert set(entries["nature"].dropna()) >= {"Income", "Assets"}


def test_cancelled_and_optional_vouchers_stay_out(
    client: TestClient, world: World, paired: PairedBridge, db: DbSession, runtime: ConnectorRuntime
) -> None:
    paired.engine().sync()
    entries = frame(runtime, world, db, paired, "voucher_ledger_entries").frame
    assert 99_999.0 not in set(entries["debit"])
    assert 55_555.0 not in set(entries["debit"])
    assert rows(db, paired, "vouchers") == len(
        paired.tally.company.vouchers
    )  # kept raw, left out of tables


def test_incremental_sync_sends_only_changes_and_resending_never_duplicates(
    client: TestClient, world: World, paired: PairedBridge, db: DbSession, clock: FakeClock
) -> None:
    paired.engine().sync()
    before = rows(db, paired, "vouchers")
    company = paired.tally.company
    first = next(iter(company.vouchers.values()))
    company.alter(first.guid, factor=2.0)
    company.add_sale(date(2026, 9, 30), "South", "Face Serum", "Beauty", 1234.0)
    clock.advance(minutes=15)
    report = paired.engine().sync()
    assert report.mode == "incremental"
    assert report.records["vouchers"] == 2
    assert rows(db, paired, "vouchers") == before + 1
    # The altered voucher's lines were replaced, not added.
    lines = db.scalars(
        select(TallyRecord).where(
            TallyRecord.connection_id == paired.connection_id,
            TallyRecord.parent_key == first.guid,
            TallyRecord.dataset == "voucher_ledger_entries",
        )
    ).all()
    assert len(lines) == len(first.lines) + len(first.items)


def test_a_full_read_removes_vouchers_deleted_in_tally_an_incremental_one_does_not(
    client: TestClient, world: World, paired: PairedBridge, db: DbSession, clock: FakeClock
) -> None:
    paired.engine().sync()
    company = paired.tally.company
    gone = next(v for v in company.vouchers.values() if v.items)
    company.delete(gone.guid)
    clock.advance(minutes=15)
    paired.engine().sync()  # incremental: Tally's alter ids cannot show a deletion
    assert rows(db, paired, "vouchers") == len(company.vouchers) + 1
    clock.advance(minutes=15)
    paired.engine().sync(full=True)
    assert rows(db, paired, "vouchers") == len(company.vouchers)
    leftovers = db.scalar(
        select(func.count())
        .select_from(TallyRecord)
        .where(
            TallyRecord.connection_id == paired.connection_id, TallyRecord.parent_key == gone.guid
        )
    )
    assert leftovers == 0


def test_a_sync_cut_off_by_the_network_resumes_without_duplicates(
    client: TestClient, world: World, paired: PairedBridge, db: DbSession
) -> None:
    calls = {"n": 0}
    real_send = paired.platform.send_batch

    def flaky(batch: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 4:
            real_send(batch)  # the platform stored it, but the answer was lost
            raise PlatformUnavailable("ConnectError")
        if calls["n"] in (5, 6, 7, 8, 9, 10):
            raise PlatformUnavailable("ConnectError")
        return real_send(batch)

    paired.platform.send_batch = flaky  # type: ignore[method-assign]
    with pytest.raises(SyncInterrupted):
        paired.engine(attempts=3).sync()
    state = paired.store.state()
    assert state.pending is not None
    assert paired.store.outbox() is not None  # the unanswered batch waits to be re-sent unchanged
    report = paired.engine().sync()  # e.g. after a restart
    assert report.resumed
    assert paired.store.outbox() is None
    assert rows(db, paired, "vouchers") == len(paired.tally.company.vouchers)
    body = detail(client, world, paired)
    assert body["tally"]["state"] == "connected"


def test_the_status_follows_the_pc(
    client: TestClient, world: World, paired: PairedBridge, clock: FakeClock
) -> None:
    paired.engine().sync()
    bridge = paired.bridge(clock=clock)
    bridge.probe_tally()
    bridge.poll(0)
    assert detail(client, world, paired)["tally"]["state"] == "connected"
    paired.tally.offline = True  # Tally closed on the PC
    bridge.probe_tally()
    bridge.poll(0)
    assert detail(client, world, paired)["tally"]["state"] == "tally_closed"
    clock.advance(minutes=6)  # the PC went to sleep
    body = detail(client, world, paired)
    assert body["tally"]["state"] == "offline"
    assert body["tally"]["last_seen_at"] is not None


def test_refresh_now_asks_the_pc_which_reads_again(
    client: TestClient, world: World, paired: PairedBridge, clock: FakeClock
) -> None:
    paired.engine().sync()
    response = client.post(
        f"/connections/{paired.connection_id}/refresh", headers=world.headers("owner_a")
    )
    assert response.status_code == 200
    assert response.json()["tally"]["refresh_pending"] is True
    bridge = paired.bridge(clock=clock)
    refresh, full = bridge.poll(0)
    assert (refresh, full) == (True, False)
    assert bridge.poll(0) == (False, False)  # asked once, answered once
    clock.advance(minutes=1)
    assert bridge.sync_once(full=full, sleep=lambda _: None)
    assert detail(client, world, paired)["tally"]["refresh_pending"] is False


def test_the_demo_company_shows_the_planted_south_drop(
    client: TestClient,
    world: World,
    db: DbSession,
    runtime: ConnectorRuntime,
    tmp_path: Path,
    demo_fixtures: Path,
) -> None:
    stand_in = StandInTally(build_company(demo_fixtures))
    paired = pair(client, world, tmp_path / "demo", stand_in=stand_in)
    paired.engine(settings=Settings()).sync()
    entries = frame(runtime, world, db, paired, "voucher_ledger_entries").frame
    sales = entries[(entries["voucher_class"] == "sales") & (entries["nature"] == "Income")].copy()
    sales["quarter"] = sales["date"].dt.to_period("Q").astype(str)
    sales["region"] = sales["party_ledger"].str.replace("Customers - ", "", regex=False)
    by = sales.groupby(["region", "quarter"])["credit"].sum()
    change = {
        r: by[(r, "2026Q3")] / by[(r, "2026Q2")] - 1 for r in ("North", "South", "East", "West")
    }
    assert change["South"] < -0.12  # planted finding 1: South falls in Q3 2026 ...
    assert all(
        change[r] > 0 for r in ("North", "East", "West")
    )  # ... while every other region grows
    assert stand_in.refused == []


@pytest.fixture(scope="module")
def demo_fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import generate_fixtures as gf

    directory = tmp_path_factory.mktemp("tally-demo")
    gf.generate(directory)
    return directory


def test_history_window_starts_at_the_books(paired: PairedBridge, clock: FakeClock) -> None:
    assert paired.engine()._history_start("20250101") == date(2025, 1, 1)
    assert paired.engine()._history_start("") == date(2023, 11, 1)
    assert paired.engine()._year_start("20250401") == date(2026, 4, 1)
