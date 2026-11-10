"""The reuse cache (BUILD_SPEC R1, 8.1): reuse, unchanged check, incremental merge, full fetch, the lock,
quality findings and snapshot expiry. Runs against Postgres with RLS, like the API."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.connectors.base import (
    ConnectionTestResult,
    ConnectorError,
    DatasetInfo,
    FetchResult,
)
from app.connectors.registry import ConnectorRuntime
from app.connectors.tabular import ColumnInfo
from app.data import snapshots
from app.data.cache import DatasetCache, DatasetMissing, merge_incremental
from app.data.locks import RedisLock
from app.data.objects import MemoryObjectStore
from app.models import Connection, Dataset, Organization, Snapshot
from tests.conftest import FakeClock
from tests.datasources import make_file_connection
from tests.tenancy import make_org

COLUMNS = [ColumnInfo("id", "Id", "integer"), ColumnInfo("amount", "Amount", "number")]


@dataclass
class FakeConnector:
    """A source whose rows, version and cursor support tests control."""

    type: str = "fake"
    rows: list[dict[str, Any]] = field(default_factory=lambda: [{"id": 1, "amount": 10.0}])
    version: str | None = "v1"
    incremental: bool = False
    new_rows: list[dict[str, Any]] = field(default_factory=list)
    error: ConnectorError | None = None
    fetches: list[str | None] = field(default_factory=list)
    version_checks: int = 0

    def test(self) -> ConnectionTestResult:
        return ConnectionTestResult(ok=True, message="ok")

    def list_datasets(self) -> list[DatasetInfo]:
        return [DatasetInfo("t", "T")]

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        self.fetches.append(since_cursor)
        if self.error:
            raise self.error
        rows = self.new_rows if since_cursor else self.rows
        return FetchResult(
            frame=pd.DataFrame(rows, columns=["id", "amount"]),
            columns=COLUMNS,
            source_version=self.version,
            cursor=f"c{len(self.fetches)}" if self.incremental else None,
            incremental=bool(since_cursor),
            key_column="id",
        )

    def source_version(self, dataset: DatasetInfo) -> str | None:
        self.version_checks += 1
        return self.version

    def supports_incremental(self) -> bool:
        return self.incremental

    def supports_pushdown(self) -> bool:
        return False


@pytest.fixture
def org(db: DbSession) -> Organization:
    return make_org(db, "UrbanCart", "asha@urbancart.in")


@pytest.fixture
def dataset_id(db: DbSession, org: Organization) -> uuid.UUID:
    connection = Connection(
        org_id=org.id, type="file", name="Fake", status="active", config={}, sync_state="idle"
    )
    db.add(connection)
    db.flush()
    dataset = Dataset(
        org_id=org.id,
        connection_id=connection.id,
        external_ref="t",
        name="T",
        schema_json={},
        quality_json=[],
    )
    db.add(dataset)
    db.commit()
    return dataset.id


def cache(runtime: ConnectorRuntime, org: Organization) -> DatasetCache:
    return DatasetCache(runtime, org.id)


def test_a_first_read_is_full_and_stored(
    runtime: ConnectorRuntime,
    org: Organization,
    dataset_id: uuid.UUID,
    db: DbSession,
    object_store: MemoryObjectStore,
    clock: FakeClock,
) -> None:
    source = FakeConnector()
    got = cache(runtime, org).get(dataset_id, connector=source)
    assert got.source == "full"
    assert got.frame.to_dict("records") == [{"id": 1, "amount": 10.0}]
    snapshot = db.scalars(select(Snapshot)).one()
    assert snapshot.source_version == "v1"
    assert snapshot.expires_at == clock.now + pd.Timedelta(hours=24)
    assert object_store.stored_keys() == [
        f"orgs/{org.id}/snapshots/{dataset_id}/{snapshot.id}.parquet"
    ]
    dataset = db.get(Dataset, dataset_id)
    assert dataset is not None
    assert dataset.row_count_estimate == 1
    assert [c["name"] for c in dataset.schema_json["columns"]] == ["id", "amount"]
    assert dataset.last_fetched_at == clock.now


def test_reuse_within_the_cache_time(
    runtime: ConnectorRuntime, org: Organization, dataset_id: uuid.UUID, clock: FakeClock
) -> None:
    source = FakeConnector()
    cache(runtime, org).get(dataset_id, connector=source)
    clock.advance(seconds=299)
    got = cache(runtime, org).get(dataset_id, connector=source)
    assert got.source == "cache"
    assert source.fetches == [None] and source.version_checks == 0


def test_after_the_cache_time_an_unchanged_source_is_not_downloaded(
    runtime: ConnectorRuntime,
    org: Organization,
    dataset_id: uuid.UUID,
    clock: FakeClock,
    db: DbSession,
) -> None:
    source = FakeConnector()
    first = cache(runtime, org).get(dataset_id, connector=source)
    clock.advance(minutes=10)
    got = cache(runtime, org).get(dataset_id, connector=source)
    assert got.source == "unchanged"
    assert got.snapshot_id == first.snapshot_id
    assert got.fetched_at == first.fetched_at  # honest: the rows were read earlier
    assert got.checked_at == clock.now  # ...and confirmed now
    assert source.fetches == [None]
    clock.advance(minutes=2)
    assert cache(runtime, org).get(dataset_id, connector=source).source == "cache"


def test_a_changed_source_is_read_again_and_the_old_snapshot_goes(
    runtime: ConnectorRuntime,
    org: Organization,
    dataset_id: uuid.UUID,
    clock: FakeClock,
    db: DbSession,
    object_store: MemoryObjectStore,
) -> None:
    source = FakeConnector()
    cache(runtime, org).get(dataset_id, connector=source)
    clock.advance(minutes=10)
    source.version, source.rows = "v2", [{"id": 1, "amount": 10.0}, {"id": 2, "amount": 5.0}]
    got = cache(runtime, org).get(dataset_id, connector=source)
    assert (got.source, got.row_count) == ("full", 2)
    assert len(db.scalars(select(Snapshot)).all()) == 1
    assert len(object_store.stored_keys()) == 1


def test_incremental_sources_fetch_only_new_rows_and_merge(
    runtime: ConnectorRuntime,
    org: Organization,
    dataset_id: uuid.UUID,
    clock: FakeClock,
    db: DbSession,
) -> None:
    source = FakeConnector(incremental=True, version=None)
    cache(runtime, org).get(dataset_id, connector=source)
    clock.advance(minutes=10)
    source.new_rows = [
        {"id": 1, "amount": 12.0},
        {"id": 2, "amount": 7.0},
    ]  # id 1 changed, id 2 new
    got = cache(runtime, org).get(dataset_id, connector=source)
    assert got.source == "incremental"
    assert source.fetches == [None, "c1"]
    assert got.frame.sort_values("id").to_dict("records") == [
        {"id": 1, "amount": 12.0},
        {"id": 2, "amount": 7.0},
    ]
    assert db.scalars(select(Snapshot)).one().cursor == "c2"


def test_force_skips_reuse(
    runtime: ConnectorRuntime, org: Organization, dataset_id: uuid.UUID
) -> None:
    source = FakeConnector()
    cache(runtime, org).get(dataset_id, connector=source)
    assert cache(runtime, org).get(dataset_id, force=True, connector=source).source == "full"
    assert source.version_checks == 0


def test_a_missing_stored_object_reads_the_source_again(
    runtime: ConnectorRuntime,
    org: Organization,
    dataset_id: uuid.UUID,
    object_store: MemoryObjectStore,
) -> None:
    source = FakeConnector()
    cache(runtime, org).get(dataset_id, connector=source)
    object_store.objects.clear()
    assert cache(runtime, org).get(dataset_id, connector=source).source == "full"


def test_someone_else_reading_the_same_dataset_makes_us_wait_then_give_up(
    runtime: ConnectorRuntime, org: Organization, dataset_id: uuid.UUID
) -> None:
    holder = RedisLock(
        runtime.redis, f"lock:dataset-fetch:{dataset_id}", timeout_seconds=60, wait_seconds=0
    )
    assert holder.acquire()
    busy = DatasetCache(runtime, org.id)
    busy._lock_wait_seconds = 0
    with pytest.raises(ConnectorError) as caught:
        busy.get(dataset_id, connector=FakeConnector())
    assert caught.value.kind == "busy"


def test_a_sign_in_problem_marks_the_connection(
    runtime: ConnectorRuntime, org: Organization, dataset_id: uuid.UUID, db: DbSession
) -> None:
    source = FakeConnector(error=ConnectorError("needs_reauth", "Sign in again."))
    with pytest.raises(ConnectorError):
        cache(runtime, org).get(dataset_id, connector=source)
    dataset = db.get(Dataset, dataset_id)
    assert dataset is not None
    connection = db.get(Connection, dataset.connection_id)
    assert connection is not None
    assert (connection.status, connection.last_error) == ("needs_reauth", "Sign in again.")
    assert dataset.last_error == "Sign in again."
    # The next good read clears it.
    cache(runtime, org).get(dataset_id, connector=FakeConnector())
    db.expire_all()
    assert db.get(Connection, connection.id).status == "active"  # type: ignore[union-attr]


def test_quality_findings_are_stored(
    runtime: ConnectorRuntime, org: Organization, dataset_id: uuid.UUID, db: DbSession
) -> None:
    rows = [{"id": i, "amount": None if i % 2 else 1.0} for i in range(20)]
    got = cache(runtime, org).get(dataset_id, connector=FakeConnector(rows=rows))
    assert [q["kind"] for q in got.quality] == ["nulls"]
    dataset = db.get(Dataset, dataset_id)
    assert dataset is not None and dataset.quality_json == got.quality


def test_row_cap(runtime: ConnectorRuntime, org: Organization, dataset_id: uuid.UUID) -> None:
    runtime.settings.max_rows_per_dataset = 3
    rows = [{"id": i, "amount": 1.0} for i in range(5)]
    got = cache(runtime, org).get(dataset_id, connector=FakeConnector(rows=rows))
    assert got.row_count == 3
    assert "truncated" in [q["kind"] for q in got.quality]


def test_another_workspaces_dataset_is_missing(
    runtime: ConnectorRuntime, db: DbSession, dataset_id: uuid.UUID
) -> None:
    other = make_org(db, "Chaiwala", "kabir@chaiwala.in")
    with pytest.raises(DatasetMissing):
        DatasetCache(runtime, other.id).get(dataset_id, connector=FakeConnector())


def test_expired_snapshots_are_purged(
    runtime: ConnectorRuntime,
    org: Organization,
    dataset_id: uuid.UUID,
    db: DbSession,
    clock: FakeClock,
    object_store: MemoryObjectStore,
) -> None:
    cache(runtime, org).get(dataset_id, connector=FakeConnector())
    assert snapshots.purge_expired(runtime.session_factory, object_store, clock.now) == 0
    clock.advance(hours=24)
    assert snapshots.purge_expired(runtime.session_factory, object_store, clock.now) == 1
    assert db.scalars(select(Snapshot)).all() == []
    assert object_store.stored_keys() == []


def test_the_file_connector_through_the_cache(
    runtime: ConnectorRuntime, org: Organization, db: DbSession, clock: FakeClock
) -> None:
    connection = make_file_connection(db, runtime, org.id)
    dataset = db.scalars(select(Dataset).where(Dataset.connection_id == connection.id)).one()
    clock.advance(minutes=10)
    got = DatasetCache(runtime, org.id).get(dataset.id)  # builds the connector itself
    assert got.source == "unchanged"  # same file version
    assert got.row_count == 3


def test_merge_without_a_key_appends() -> None:
    old = pd.DataFrame({"a": [1]})
    assert merge_incremental(old, pd.DataFrame({"a": [1]}), None)["a"].tolist() == [1, 1]
    assert merge_incremental(old, pd.DataFrame({"a": []}), "a")["a"].tolist() == [1]
