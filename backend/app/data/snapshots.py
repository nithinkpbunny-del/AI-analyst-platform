"""Parquet snapshots of datasets in object storage (BUILD_SPEC 8.1, 6.6).

A snapshot is a short-lived copy of one dataset as read from its source. It is reused for ``CACHE_TTL_SECONDS``
(or for as long as the source says nothing changed), and always deleted after ``SNAPSHOT_MAX_AGE_HOURS``
(``purge_expired``), so the platform never keeps customer data longer than the cache needs.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.data.objects import ObjectStore
from app.models import Snapshot

logger = logging.getLogger(__name__)


def to_parquet(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, engine="pyarrow", index=False, compression="zstd")
    return buffer.getvalue()


def from_parquet(data: bytes) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(data), engine="pyarrow")


def delete_snapshots(db: DbSession, objects: ObjectStore, snapshots: list[Snapshot]) -> None:
    """Delete stored objects first, then rows: a row never points at a deleted object for long."""
    for snapshot in snapshots:
        try:
            objects.delete(snapshot.storage_key)
        # A storage hiccup must not keep the row; the object stays under the workspace's prefix.
        except Exception:
            logger.warning("could not delete snapshot object %s", snapshot.id)
        db.delete(snapshot)
    db.flush()


def purge_expired(factory: sessionmaker[DbSession], objects: ObjectStore, now: datetime) -> int:
    """Delete every expired snapshot in every workspace (beat job; runs unscoped as the table owner)."""
    with factory() as db:
        expired = list(db.scalars(select(Snapshot).where(Snapshot.expires_at <= now).limit(5000)))
        delete_snapshots(db, objects, expired)
        db.commit()
    if expired:
        logger.info("purged %d expired snapshots", len(expired))
    return len(expired)
