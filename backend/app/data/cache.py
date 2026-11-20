"""Live data with a short reuse cache (BUILD_SPEC R1, 8.1).

``DatasetCache.get(dataset_id)`` returns the dataset's current rows, deciding in this order:

1. **Reuse**: the newest snapshot was read or confirmed within ``CACHE_TTL_SECONDS`` -> use it.
2. **Unchanged**: the source reports the same version as the snapshot (a file version, a Drive revision) ->
   use the snapshot and record that it was confirmed now (no download).
3. **Incremental**: the source can fetch only new or changed rows since the snapshot's cursor -> fetch those
   and merge them into the snapshot (later rows replace earlier ones with the same key).
4. **Full**: read everything.

A Redis lock per dataset stops two requests (or a request and a background job) from reading the same source at
once; whoever waits re-checks the cache first and usually finds fresh data. Every new snapshot runs the
data-quality checks (``app.data.quality``) and stores the findings on the dataset. Database transactions are
never held open while a source is being read.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.connectors.base import Connector, ConnectorError, DatasetInfo, FetchResult
from app.connectors.registry import ConnectorRuntime, build_connector, close_connector
from app.data import quality, snapshots
from app.data.locks import RedisLock
from app.data.objects import ObjectNotFound, snapshot_key
from app.db.ids import uuid7
from app.db.tenant import scope_to_org
from app.models import Connection, Dataset, Organization, Snapshot

logger = logging.getLogger(__name__)

Source = Literal["cache", "unchanged", "incremental", "full"]


class DatasetMissing(Exception):
    """The dataset does not exist in this workspace (or was removed meanwhile)."""


@dataclass(frozen=True)
class CachedFrame:
    frame: pd.DataFrame
    dataset_id: uuid.UUID
    snapshot_id: uuid.UUID
    fetched_at: datetime  # when the rows were read from the source
    checked_at: datetime  # when the source last confirmed them
    row_count: int
    quality: list[dict[str, Any]]
    source: Source


@dataclass(frozen=True)
class _Context:
    dataset: DatasetInfo
    connection_id: uuid.UUID
    timezone: str
    snapshot: Snapshot | None


def merge_incremental(old: pd.DataFrame, new: pd.DataFrame, key: str | None) -> pd.DataFrame:
    """Rows new or changed since the cursor replace their earlier copies (same ``key``), others are added."""
    if new.empty:
        return old
    combined = pd.concat([old, new], ignore_index=True)
    if key and key in combined:
        combined = combined.drop_duplicates(subset=[key], keep="last")
    return combined.reset_index(drop=True)


class DatasetCache:
    def __init__(self, runtime: ConnectorRuntime, org_id: uuid.UUID) -> None:
        self._rt = runtime
        self._org_id = org_id
        settings = runtime.settings
        self._ttl = timedelta(seconds=settings.cache_ttl_seconds)
        self._max_age = timedelta(hours=settings.snapshot_max_age_hours)
        # Held at most this long (then it expires), and waited for at most as long.
        self._lock_seconds = max(30, settings.source_query_timeout_long_task_seconds * 2)
        self._lock_wait_seconds: float = self._lock_seconds

    # ------------------------------------------------------------------------------------- sessions
    def _session(self) -> DbSession:
        db = self._rt.session_factory()
        scope_to_org(db, self._org_id)
        return db

    @staticmethod
    def _newest(db: DbSession, dataset_id: uuid.UUID, now: datetime) -> Snapshot | None:
        return db.scalar(
            select(Snapshot)
            .where(Snapshot.dataset_id == dataset_id, Snapshot.expires_at > now)
            .order_by(Snapshot.fetched_at.desc())
            .limit(1)
        )

    def _read(self, snapshot: Snapshot) -> pd.DataFrame | None:
        try:
            return snapshots.from_parquet(self._rt.objects.get(snapshot.storage_key))
        except ObjectNotFound:
            logger.warning(
                "snapshot %s has no stored object; reading the source again", snapshot.id
            )
            return None

    def _cached(self, snapshot: Snapshot, frame: pd.DataFrame, source: Source) -> CachedFrame:
        return CachedFrame(
            frame=frame,
            dataset_id=snapshot.dataset_id,
            snapshot_id=snapshot.id,
            fetched_at=snapshot.fetched_at,
            checked_at=snapshot.checked_at,
            row_count=int(snapshot.row_count),
            quality=list(snapshot.quality_json),
            source=source,
        )

    # ---------------------------------------------------------------------------------------- public
    def get(
        self, dataset_id: uuid.UUID, *, force: bool = False, connector: Connector | None = None
    ) -> CachedFrame:
        """The dataset's rows. ``force`` skips steps 1-3 (a manual "Refresh"). ``connector`` may be shared by
        a caller reading several datasets of one connection (one sign-in to the source)."""
        if not force and (hit := self._fresh(dataset_id)) is not None:
            return hit
        lock = RedisLock(
            self._rt.redis,
            f"lock:dataset-fetch:{dataset_id}",
            timeout_seconds=self._lock_seconds,
            wait_seconds=self._lock_wait_seconds,
        )
        if not lock.acquire():
            raise ConnectorError("busy", "This data is being read already. Try again in a moment.")
        try:
            if not force and (hit := self._fresh(dataset_id)) is not None:
                return hit
            return self._fetch(dataset_id, force=force, connector=connector)
        finally:
            lock.release()

    def latest(self, dataset_id: uuid.UUID) -> CachedFrame | None:
        """The newest stored copy, whatever its age (not expired), without reading the source. For work that
        needs the table's shape, not fresh numbers (profiling a dataset for the business dictionary)."""
        now = self._rt.clock()
        with self._session() as db:
            snapshot = self._newest(db, dataset_id, now)
            db.commit()
        if snapshot is None:
            return None
        frame = self._read(snapshot)
        return None if frame is None else self._cached(snapshot, frame, "cache")

    def store(self, dataset_id: uuid.UUID, result: FetchResult) -> CachedFrame:
        """Write rows already read and checked by the caller (a new file version) as the dataset's snapshot."""
        now = self._rt.clock()
        with self._session() as db:
            ctx, _ = self._context(db, dataset_id, now)
            db.commit()
        return self._store(dataset_id, ctx, result, result.frame, "full")

    def _fresh(self, dataset_id: uuid.UUID) -> CachedFrame | None:
        now = self._rt.clock()
        with self._session() as db:
            snapshot = self._newest(db, dataset_id, now)
            db.commit()
        if snapshot is None or snapshot.checked_at < now - self._ttl:
            return None
        frame = self._read(snapshot)
        return None if frame is None else self._cached(snapshot, frame, "cache")

    def _context(
        self, db: DbSession, dataset_id: uuid.UUID, now: datetime
    ) -> tuple[_Context, Connection]:
        dataset = db.scalar(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == self._org_id)
        )
        if dataset is None:
            raise DatasetMissing(str(dataset_id))
        connection = db.get(Connection, dataset.connection_id)
        org = db.get(Organization, self._org_id)
        if connection is None or org is None:  # pragma: no cover - foreign keys guarantee both
            raise DatasetMissing(str(dataset_id))
        context = _Context(
            dataset=DatasetInfo(ref=dataset.external_ref, name=dataset.name),
            connection_id=connection.id,
            timezone=org.timezone,
            snapshot=self._newest(db, dataset_id, now),
        )
        return context, connection

    def _fetch(
        self, dataset_id: uuid.UUID, *, force: bool, connector: Connector | None
    ) -> CachedFrame:
        now = self._rt.clock()
        with self._session() as db:
            ctx, connection = self._context(db, dataset_id, now)
            owned = connector is None
            source_connector = connector or build_connector(
                db, connection, self._rt, timezone=ctx.timezone
            )
            db.commit()  # nothing is held open while the source is read
        try:
            snapshot = None if force else ctx.snapshot
            if snapshot is not None:
                version = source_connector.source_version(ctx.dataset)
                if version is not None and version == snapshot.source_version:
                    frame = self._read(snapshot)
                    if frame is not None:
                        # A pushed source (Tally) confirms the copy, not that the data is newer (D111).
                        pushed = bool(getattr(source_connector, "pushed", False))
                        return self._confirm(snapshot, frame, pushed=pushed)
            old: pd.DataFrame | None = None
            if snapshot is not None and snapshot.cursor and source_connector.supports_incremental():
                old = self._read(snapshot)
            if old is not None and snapshot is not None:
                result = source_connector.fetch(ctx.dataset, since_cursor=snapshot.cursor)
                frame = merge_incremental(old, result.frame, result.key_column)
                return self._store(dataset_id, ctx, result, frame, "incremental")
            result = source_connector.fetch(ctx.dataset)
            return self._store(dataset_id, ctx, result, result.frame, "full")
        except ConnectorError as error:
            if error.stale_ok and ctx.snapshot is not None:
                # The source is at its usage limit (e.g. Zoho's daily API calls): keep using the stored copy
                # rather than failing; its fetch time shows how old it is (DECISIONS D97).
                kept = self._read(ctx.snapshot)
                if kept is not None:
                    logger.info("source at its limit; using the stored copy of %s", dataset_id)
                    return self._cached(ctx.snapshot, kept, "cache")
            self._record_failure(dataset_id, error)
            raise
        finally:
            if owned:
                close_connector(source_connector)

    # -------------------------------------------------------------------------------------- writes
    def _confirm(
        self, snapshot: Snapshot, frame: pd.DataFrame, *, pushed: bool = False
    ) -> CachedFrame:
        now = self._rt.clock()
        with self._session() as db:
            row = db.get(Snapshot, snapshot.id)
            if row is not None:
                row.checked_at = now
                dataset = db.get(Dataset, row.dataset_id)
                if dataset is not None:
                    if not pushed:
                        dataset.last_fetched_at = now
                    dataset.last_error = None
                    self._mark_connection_ok(
                        db, dataset.connection_id, row.fetched_at if pushed else now
                    )
                db.commit()
                snapshot = row
        return self._cached(snapshot, frame, "unchanged")

    def _store(
        self,
        dataset_id: uuid.UUID,
        ctx: _Context,
        result: FetchResult,
        frame: pd.DataFrame,
        source: Source,
    ) -> CachedFrame:
        now = self._rt.clock()
        # When the rows left the source: now, or for a pushed source the time of its last push (D111).
        as_of = min(result.data_as_of, now) if result.data_as_of is not None else now
        max_rows = self._rt.settings.max_rows_per_dataset
        truncated = result.truncated or len(frame) > max_rows
        frame = frame.head(max_rows)
        findings = quality.check(
            frame,
            result.columns,
            now=now,
            timezone=ctx.timezone,
            truncated=truncated,
            total_row_dropped=result.total_row_dropped,
            max_rows=max_rows,
        )
        found = [f.as_json() for f in findings]
        data = snapshots.to_parquet(frame)
        snapshot_id = uuid7()
        key = snapshot_key(self._org_id, dataset_id, snapshot_id)
        self._rt.objects.put(key, data, content_type="application/vnd.apache.parquet")
        schema = {
            "columns": [
                {k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(c).items()}
                for c in result.columns
            ],
            "row_count": len(frame),
            "truncated": truncated,
            "total_row_dropped": result.total_row_dropped,
        }
        with self._session() as db:
            dataset = db.get(Dataset, dataset_id)
            if dataset is None:  # removed while we were reading
                db.commit()
                self._rt.objects.delete(key)
                raise DatasetMissing(str(dataset_id))
            row = Snapshot(
                id=snapshot_id,
                org_id=self._org_id,
                dataset_id=dataset_id,
                storage_key=key,
                fetched_at=as_of,
                checked_at=now,
                cursor=result.cursor,
                source_version=result.source_version,
                row_count=len(frame),
                byte_size=len(data),
                quality_json=found,
                expires_at=now + self._max_age,
            )
            db.add(row)
            older = list(
                db.scalars(
                    select(Snapshot).where(
                        Snapshot.dataset_id == dataset_id, Snapshot.id != snapshot_id
                    )
                )
            )
            snapshots.delete_snapshots(db, self._rt.objects, older)
            dataset.schema_json = schema
            dataset.row_count_estimate = len(frame)
            dataset.last_fetched_at = as_of
            dataset.quality_json = found
            dataset.last_error = None
            self._mark_connection_ok(db, ctx.connection_id, as_of)
            db.commit()
        return self._cached(row, frame, source)

    @staticmethod
    def _mark_connection_ok(db: DbSession, connection_id: uuid.UUID, now: datetime) -> None:
        connection = db.get(Connection, connection_id)
        if connection is None:
            return
        if connection.last_success_at is None or now > connection.last_success_at:
            connection.last_success_at = now
        if connection.status in ("error", "needs_reauth"):
            connection.status = "active"
            connection.last_error = None

    def _record_failure(self, dataset_id: uuid.UUID, error: ConnectorError) -> None:
        logger.warning("reading dataset %s failed: %s (%s)", dataset_id, error.kind, error.detail)
        with self._session() as db:
            dataset = db.get(Dataset, dataset_id)
            if dataset is not None:
                dataset.last_error = error.message
                connection = db.get(Connection, dataset.connection_id)
                if connection is not None and error.kind == "needs_reauth":
                    connection.status = "needs_reauth"
                    connection.last_error = error.message
            db.commit()
