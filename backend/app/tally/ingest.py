"""Receiving what the Tally bridge sends (DECISIONS D110).

* **A batch** is stored once: ``tally_syncs.batches_json`` remembers each batch number per dataset, and a batch
  number already received is answered "duplicate" without touching anything. Rows are upserted by
  ``(connection, dataset, Tally GUID)``, so even a re-read of the same vouchers can never duplicate a row. A
  voucher's lines are replaced with the voucher (a line removed in Tally disappears).
* **Only known fields** of known datasets are kept (``RAW_FIELDS``); anything else a bridge sends is dropped.
* **Complete** checks that every batch number from 0 to n-1 arrived, then, for datasets the bridge read whole,
  removes rows this sync did not send (vouchers deleted in Tally). Vouchers are only removed after a **full** read.
* **One company per connection:** the first company sent is remembered; another company is refused (409), so a
  PC that opens a different company in Tally can never mix two sets of books.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from app.api.errors import ApiError
from app.connectors.tally import RAW_FIELDS, VOUCHER_LINES
from app.models import Connection, TallyDevice, TallyRecord, TallySync
from app.schemas.tally import BatchIn, CompleteIn, RecordIn

UPSERT_CHUNK = 1000
KEEP_SYNCS = timedelta(days=7)


def tally_config(connection: Connection) -> dict[str, Any]:
    return dict(connection.config.get("tally") or {})


def set_tally_config(connection: Connection, **changes: Any) -> None:
    connection.config = {**connection.config, "tally": {**tally_config(connection), **changes}}


def check_company(connection: Connection, guid: str, name: str) -> None:
    config = tally_config(connection)
    known = config.get("company_guid")
    if known and known != guid:
        raise ApiError(
            409,
            "company_mismatch",
            f"This PC is sending another Tally company ({name[:80]}). This connection reads "
            f"{str(config.get('company') or 'another company')[:80]}. Open that company in Tally, or add "
            "another Tally connection for this one.",
        )
    if not known:
        set_tally_config(connection, company_guid=guid, company=name)


def _sync(
    db: DbSession,
    device: TallyDevice,
    connection: Connection,
    sync_ref: uuid.UUID,
    *,
    mode: str,
    company_guid: str,
    create: bool,
) -> TallySync | None:
    row = db.scalar(
        select(TallySync)
        .where(TallySync.device_id == device.id, TallySync.sync_ref == sync_ref)
        .with_for_update()
    )
    if row is None and create:
        row = TallySync(
            org_id=connection.org_id,
            connection_id=connection.id,
            device_id=device.id,
            sync_ref=sync_ref,
            mode=mode,
            status="receiving",
            company_guid=company_guid,
            batches_json={},
            records_json={},
        )
        db.add(row)
        db.flush()
    if row is not None and row.mode != mode:
        raise ApiError(409, "sync_mode_changed", "This sync was started as another kind of read.")
    return row


def _keep(fields: dict[str, str], dataset: str) -> dict[str, str]:
    allowed = RAW_FIELDS[dataset]
    return {name: value for name, value in fields.items() if name in allowed}


def _upsert(db: DbSession, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), UPSERT_CHUNK):
        chunk = rows[start : start + UPSERT_CHUNK]
        statement = insert(TallyRecord).values(chunk)
        statement = statement.on_conflict_do_update(
            index_elements=[TallyRecord.connection_id, TallyRecord.dataset, TallyRecord.key],
            set_={
                "fields": statement.excluded.fields,
                "parent_key": statement.excluded.parent_key,
                "sync_id": statement.excluded.sync_id,
                "received_at": statement.excluded.received_at,
            },
        )
        db.execute(statement)


def _apply(
    db: DbSession,
    connection: Connection,
    sync: TallySync,
    dataset: str,
    records: list[RecordIn],
    now: datetime,
) -> int:
    latest: dict[str, RecordIn] = {}
    for record in records:
        latest[record.key] = record  # the same key twice in one batch: the later one wins

    def row(ds: str, key: str, fields: dict[str, str], parent: str | None) -> dict[str, Any]:
        return {
            "connection_id": connection.id,
            "dataset": ds,
            "key": key,
            "org_id": connection.org_id,
            "parent_key": parent,
            "fields": _keep(fields, ds),
            "sync_id": sync.id,
            "received_at": now,
        }

    _upsert(db, [row(dataset, key, r.fields, None) for key, r in latest.items()])
    if dataset == "vouchers" and latest:
        keys = list(latest)
        for start in range(0, len(keys), UPSERT_CHUNK):
            db.execute(
                delete(TallyRecord).where(
                    TallyRecord.connection_id == connection.id,
                    TallyRecord.dataset.in_(VOUCHER_LINES),
                    TallyRecord.parent_key.in_(keys[start : start + UPSERT_CHUNK]),
                )
            )
        lines: list[dict[str, Any]] = []
        for key, record in latest.items():
            lines += [
                row("voucher_ledger_entries", f"{key}#L{i}", line, key)
                for i, line in enumerate(record.ledger_entries)
            ]
            lines += [
                row("voucher_inventory_entries", f"{key}#I{i}", line, key)
                for i, line in enumerate(record.inventory_entries)
            ]
        _upsert(db, lines)
    return len(latest)


def receive_batch(
    db: DbSession,
    device: TallyDevice,
    connection: Connection,
    batch: BatchIn,
    *,
    now: datetime,
    max_rows: int,
) -> tuple[int, bool]:
    """Store one batch. Returns (rows accepted, whether it was a batch already received)."""
    if connection.status == "disabled":
        raise ApiError(
            409, "connection_paused", "This Tally connection is paused in the workspace."
        )
    if len(batch.records) > max_rows:
        raise ApiError(413, "batch_too_large", f"A batch may have at most {max_rows} rows.")
    check_company(connection, batch.company_guid, batch.company)
    sync = _sync(
        db,
        device,
        connection,
        batch.sync_id,
        mode=batch.mode,
        company_guid=batch.company_guid,
        create=True,
    )
    assert sync is not None
    if sync.status == "complete":
        raise ApiError(409, "sync_closed", "This sync is already complete. Start a new one.")
    received = [int(s) for s in sync.batches_json.get(batch.dataset, [])]
    if batch.seq in received:
        return 0, True
    accepted = _apply(db, connection, sync, batch.dataset, batch.records, now)
    sync.batches_json = {**sync.batches_json, batch.dataset: [*received, batch.seq]}
    sync.records_json = {
        **sync.records_json,
        batch.dataset: int(sync.records_json.get(batch.dataset, 0)) + accepted,
    }
    device.last_seen_at = now
    return accepted, False


def counts(db: DbSession, connection_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(TallyRecord.dataset, func.count())
        .where(TallyRecord.connection_id == connection_id)
        .group_by(TallyRecord.dataset)
    )
    return {str(dataset): int(n) for dataset, n in rows}


def complete_sync(
    db: DbSession,
    device: TallyDevice,
    connection: Connection,
    sync_ref: uuid.UUID,
    body: CompleteIn,
    *,
    now: datetime,
) -> tuple[str, int]:
    """Close a sync. Returns ("complete" | "already_complete", rows removed)."""
    check_company(connection, body.company_guid, body.company)
    sync = _sync(
        db,
        device,
        connection,
        sync_ref,
        mode=body.mode,
        company_guid=body.company_guid,
        create=all(d.batches == 0 for d in body.datasets.values()),
    )
    if sync is None:
        raise ApiError(409, "sync_unknown", "No batches of this sync arrived. Start a new one.")
    if sync.status == "complete":
        return "already_complete", 0
    missing = {
        dataset: sorted(
            set(range(d.batches)) - {int(s) for s in sync.batches_json.get(dataset, [])}
        )
        for dataset, d in body.datasets.items()
    }
    missing = {k: v for k, v in missing.items() if v}
    if missing:
        raise ApiError(
            409,
            "batches_missing",
            "Some batches of this sync did not arrive. Read Tally again.",
            fields={k: ",".join(str(s) for s in v[:50]) for k, v in missing.items()},
        )
    removed = 0
    for dataset, d in body.datasets.items():
        if not d.whole or (dataset == "vouchers" and sync.mode != "full"):
            continue  # only what was read whole may lose rows; vouchers only after a full read
        targets = (dataset, *VOUCHER_LINES) if dataset == "vouchers" else (dataset,)
        result = db.execute(
            delete(TallyRecord).where(
                TallyRecord.connection_id == connection.id,
                TallyRecord.dataset.in_(targets),
                TallyRecord.sync_id != sync.id,
            )
        )
        removed += int(getattr(result, "rowcount", 0) or 0)
    sync.status = "complete"
    sync.completed_at = now
    device.last_seen_at = now
    device.last_sync_at = now
    if sync.mode == "full":
        device.last_full_sync_at = now
    config = tally_config(connection)
    set_tally_config(
        connection,
        company=body.company,
        company_guid=body.company_guid,
        last_sync_id=str(sync.id),
        last_sync_at=now.isoformat(),
        last_sync_mode=sync.mode,
        last_full_sync_at=now.isoformat()
        if sync.mode == "full"
        else config.get("last_full_sync_at"),
        rows=counts(db, connection.id),
        refresh_requested_at=None,
    )
    if connection.status != "disabled":
        connection.status = "active"
        connection.last_error = None
    db.execute(
        delete(TallySync).where(
            TallySync.connection_id == connection.id,
            TallySync.id != sync.id,
            TallySync.created_at < now - KEEP_SYNCS,
        )
    )
    return "complete", removed
