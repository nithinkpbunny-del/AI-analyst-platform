"""Reading a whole connection in the background, and removing one (BUILD_SPEC 8.1, 6.6).

``sync_connection`` is the body of the Celery job (``app.tasks.jobs.sync_connection``): it lists the source's
datasets (new sheets or tabs appear, removed ones go), reads each one through the cache (so snapshots, schemas
and quality findings are up to date) and then sets the connection's status.

A new file version (DECISIONS D58) is read completely **before** anything changes, then:

* no data rows at all (e.g. a one-line text file) -> ``failed``; the version in use stays;
* sheets or columns gone, a sheet emptied, or a column's type changed -> ``needs_confirmation`` with the
  differences; the version in use stays until an Owner chooses the new one ("Use this version");
* otherwise (same structure, or only new sheets/columns) -> it becomes the version in use.

Choosing a version (a reviewed upload, or switching back to an earlier one) runs the same job with
``accept_changes=True``.

``remove_connection`` deletes everything a connection owns at once: snapshots and uploaded files in storage,
its secret, the Google or Zoho token at the source (best effort), and the rows (DPDP: "disconnecting deletes its snapshots
and secrets immediately").
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.connectors import zoho_books
from app.connectors.base import Connector, ConnectorError, DatasetInfo, FetchResult
from app.connectors.google_sheets import revoke
from app.connectors.registry import (
    SECRET_PURPOSES,
    ConnectorRuntime,
    build_connector,
    close_connector,
    file_in_use,
)
from app.data import snapshots
from app.data.cache import DatasetCache, DatasetMissing
from app.db.tenant import scope_to_org
from app.models import Connection, Dataset, FileVersion, Organization, Snapshot

logger = logging.getLogger(__name__)


@dataclass
class SyncOutcome:
    status: str
    datasets: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)


def _session(rt: ConnectorRuntime, org_id: uuid.UUID) -> DbSession:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    return db


def _set_datasets(
    db: DbSession, rt: ConnectorRuntime, connection: Connection, infos: list[DatasetInfo]
) -> list[uuid.UUID]:
    existing = {
        d.external_ref: d
        for d in db.scalars(select(Dataset).where(Dataset.connection_id == connection.id))
    }
    wanted = {info.ref: info for info in infos}
    for ref, dataset in existing.items():
        if ref not in wanted:
            gone = list(db.scalars(select(Snapshot).where(Snapshot.dataset_id == dataset.id)))
            snapshots.delete_snapshots(db, rt.objects, gone)
            db.delete(dataset)
    ids = []
    for position, info in enumerate(infos):
        found = existing.get(info.ref)
        if found is None:
            dataset = Dataset(
                org_id=connection.org_id,
                connection_id=connection.id,
                external_ref=info.ref,
                name=info.name[:200],
                position=position,
                schema_json={},
                quality_json=[],
            )
            db.add(dataset)
        else:
            dataset = found
            dataset.name = info.name[:200]
            dataset.position = position
        db.flush()
        ids.append(dataset.id)
    return ids


def _finish(
    rt: ConnectorRuntime,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    version_id: uuid.UUID | None,
    outcome: SyncOutcome,
    error: ConnectorError | None = None,
) -> SyncOutcome:
    with _session(rt, org_id) as db:
        connection = db.get(Connection, connection_id)
        if connection is None:
            db.commit()
            return outcome
        connection.sync_state = "idle"
        all_failed = outcome.datasets > 0 and outcome.failed == outcome.datasets
        version = db.get(FileVersion, version_id) if version_id else None
        if version is not None:
            upload_failed = error is not None or all_failed
            version.status = "failed" if upload_failed else "ready"
            version.error = None
            version.review_json = []
            if upload_failed:
                version.error = error.message if error else outcome.messages[0]
                db.flush()
                if file_in_use(db, connection) is not None:
                    # A failed upload never replaces a working file: the previous version stays in use.
                    outcome.status = connection.status
                    db.commit()
                    return outcome
            else:
                connection.config = {**connection.config, "file_version_id": str(version.id)}
        if error is not None:
            connection.status = "needs_reauth" if error.kind == "needs_reauth" else "error"
            connection.last_error = error.message
        elif all_failed:
            connection.status = "error"
            connection.last_error = outcome.messages[0]
        elif connection.status != "disabled":
            connection.status = "active"
            connection.last_error = None
        outcome.status = connection.status
        db.commit()
    return outcome


NO_ROWS = "This file has no data rows (at most a header row was found), so it was not used."
_TYPE_FAMILY = {"integer": "number", "number": "number", "date": "date", "datetime": "date"}


def _family(type_name: object) -> str:
    return _TYPE_FAMILY.get(str(type_name), str(type_name))


def version_changes(
    datasets: list[Dataset], results: list[tuple[DatasetInfo, FetchResult]]
) -> list[dict[str, object]]:
    """How a new file version differs from the version in use, in ways that would break questions or the
    business dictionary. New sheets and new columns are not listed: adding is safe."""
    new = {info.ref: result for info, result in results}
    changes: list[dict[str, object]] = []
    for dataset in sorted(datasets, key=lambda d: d.position):
        old_columns = {c["name"]: c for c in dataset.schema_json.get("columns", [])}
        if not old_columns:
            continue  # never read: nothing to compare with
        result = new.get(dataset.external_ref)
        if result is None:
            changes.append({"kind": "sheet_missing", "sheet": dataset.name})
            continue
        if (dataset.row_count_estimate or 0) > 0 and len(result.frame) == 0:
            changes.append(
                {
                    "kind": "sheet_empty",
                    "sheet": dataset.name,
                    "rows_before": dataset.row_count_estimate,
                }
            )
        new_columns = {c.name: c for c in result.columns}
        missing = [
            str(c.get("label") or name)
            for name, c in old_columns.items()
            if name not in new_columns
        ]
        if missing:
            changes.append(
                {
                    "kind": "columns_missing",
                    "sheet": dataset.name,
                    "columns": missing[:10],
                    "count": len(missing),
                }
            )
        for name, old in old_columns.items():
            column = new_columns.get(name)
            if column is not None and _family(old.get("type")) != _family(column.type):
                changes.append(
                    {
                        "kind": "type_changed",
                        "sheet": dataset.name,
                        "column": str(old.get("label") or name),
                        "from": str(old.get("type")),
                        "to": column.type,
                    }
                )
    return changes


def _sync_file_version(
    rt: ConnectorRuntime,
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    version_id: uuid.UUID,
    connector: Connector,
    accept_changes: bool,
) -> SyncOutcome:
    try:
        infos = connector.list_datasets()
        results = [(info, connector.fetch(info)) for info in infos]
    except ConnectorError as error:
        return _finish(
            rt,
            org_id,
            connection_id,
            version_id=version_id,
            outcome=SyncOutcome("error"),
            error=error,
        )
    if sum(len(result.frame) for _, result in results) == 0:
        return _finish(
            rt,
            org_id,
            connection_id,
            version_id=version_id,
            outcome=SyncOutcome("error"),
            error=ConnectorError("invalid", NO_ROWS),
        )
    with _session(rt, org_id) as db:
        connection = db.get(Connection, connection_id)
        version = db.get(FileVersion, version_id)
        if connection is None or version is None:
            db.commit()
            return SyncOutcome(status="missing")
        current = file_in_use(db, connection)
        changes: list[dict[str, object]] = []
        if current is not None and current.id != version_id and not accept_changes:
            datasets = list(
                db.scalars(select(Dataset).where(Dataset.connection_id == connection_id))
            )
            changes = version_changes(datasets, results)
        if changes:
            version.status = "needs_confirmation"
            version.review_json = changes
            version.error = None
            connection.sync_state = "idle"
            db.commit()
            return SyncOutcome(status="needs_confirmation", datasets=len(results))
        ids = _set_datasets(db, rt, connection, [info for info, _ in results])
        db.commit()
    cache = DatasetCache(rt, org_id)
    for dataset_id, (_, result) in zip(ids, results, strict=True):
        cache.store(dataset_id, result)
    return _finish(
        rt,
        org_id,
        connection_id,
        version_id=version_id,
        outcome=SyncOutcome("active", datasets=len(ids)),
    )


def sync_connection(
    rt: ConnectorRuntime,
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    force: bool = False,
    file_version_id: uuid.UUID | None = None,
    accept_changes: bool = False,
) -> SyncOutcome:
    with _session(rt, org_id) as db:
        connection = db.scalar(
            select(Connection).where(Connection.id == connection_id, Connection.org_id == org_id)
        )
        if connection is None:
            db.commit()
            return SyncOutcome(status="missing")
        if connection.status == "disabled" and file_version_id is None:
            connection.sync_state = "idle"
            db.commit()
            return SyncOutcome(status="disabled")
        connection.sync_state = "running"
        org = db.get(Organization, org_id)
        timezone = org.timezone if org else "Asia/Kolkata"
        version = db.get(FileVersion, file_version_id) if file_version_id else None
        try:
            connector: Connector = build_connector(
                db, connection, rt, timezone=timezone, file_version=version
            )
        except ConnectorError as error:
            db.commit()
            return _finish(
                rt,
                org_id,
                connection_id,
                version_id=file_version_id,
                outcome=SyncOutcome("error"),
                error=error,
            )
        db.commit()
        is_file_version = connection.type == "file" and file_version_id is not None

    if is_file_version:
        assert file_version_id is not None
        try:
            return _sync_file_version(
                rt,
                org_id=org_id,
                connection_id=connection_id,
                version_id=file_version_id,
                connector=connector,
                accept_changes=accept_changes,
            )
        finally:
            close_connector(connector)

    outcome = SyncOutcome(status="active")
    try:
        try:
            infos = connector.list_datasets()
        except ConnectorError as error:
            logger.warning("listing datasets failed: %s (%s)", error.kind, error.detail)
            return _finish(
                rt, org_id, connection_id, version_id=file_version_id, outcome=outcome, error=error
            )
        with _session(rt, org_id) as db:
            current = db.get(Connection, connection_id)
            if current is None:
                db.commit()
                return SyncOutcome(status="missing")
            ids = _set_datasets(db, rt, current, infos)
            db.commit()
        cache = DatasetCache(rt, org_id)
        outcome.datasets = len(ids)
        for dataset_id in ids:
            try:
                cache.get(dataset_id, force=force, connector=connector)
            except DatasetMissing:
                outcome.datasets -= 1
            except ConnectorError as error:
                outcome.failed += 1
                outcome.messages.append(error.message)
                if error.kind == "needs_reauth":
                    return _finish(
                        rt,
                        org_id,
                        connection_id,
                        version_id=file_version_id,
                        outcome=outcome,
                        error=error,
                    )
    finally:
        close_connector(connector)
    return _finish(rt, org_id, connection_id, version_id=file_version_id, outcome=outcome)


UNEXPECTED_FAILURE = "Reading the data failed unexpectedly. Try again in a few minutes."


def run_sync_job(
    rt: ConnectorRuntime,
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    force: bool = False,
    file_version_id: uuid.UUID | None = None,
    accept_changes: bool = False,
) -> SyncOutcome:
    """``sync_connection`` for the job queue: an unexpected crash still leaves the connection in a clear state
    (not stuck on "reading"), then the error is raised for the worker log."""
    try:
        return sync_connection(
            rt,
            org_id=org_id,
            connection_id=connection_id,
            force=force,
            file_version_id=file_version_id,
            accept_changes=accept_changes,
        )
    except Exception:
        logger.exception("syncing a connection failed unexpectedly")
        _finish(
            rt,
            org_id,
            connection_id,
            version_id=file_version_id,
            outcome=SyncOutcome(status="error"),
            error=ConnectorError("unavailable", UNEXPECTED_FAILURE),
        )
        raise


def remove_connection(db: DbSession, rt: ConnectorRuntime, connection: Connection) -> None:
    """Delete a connection and everything it owns. ``db`` is the caller's tenant-scoped session."""
    datasets = list(db.scalars(select(Dataset.id).where(Dataset.connection_id == connection.id)))
    if datasets:
        snapshots.delete_snapshots(
            db,
            rt.objects,
            list(db.scalars(select(Snapshot).where(Snapshot.dataset_id.in_(datasets)))),
        )
    for version in db.scalars(
        select(FileVersion).where(FileVersion.connection_id == connection.id)
    ):
        rt.objects.delete(version.storage_key)
    secret_ref = connection.secret_ref
    if connection.type == "google_sheets" and secret_ref is not None and rt.google.available:
        try:
            token = rt.secrets.reveal(
                db,
                org_id=connection.org_id,
                secret_id=secret_ref,
                purpose=SECRET_PURPOSES["google_sheets"],
            )
            http = rt.google.http()
            try:
                revoke(http, token)
            finally:
                http.close()
        except Exception:  # never block removal on Google
            logger.warning("could not revoke the Google token of a removed connection")
    if connection.type == "zoho_books" and secret_ref is not None and rt.zoho.available:
        region = str(connection.config.get("region") or "")
        try:
            token = rt.secrets.reveal(
                db,
                org_id=connection.org_id,
                secret_id=secret_ref,
                purpose=SECRET_PURPOSES["zoho_books"],
            )
            http = rt.zoho.http(region)
            try:
                zoho_books.revoke(rt.zoho, http, region=region, refresh_token=token)
            finally:
                http.close()
        except Exception:  # never block removal on Zoho
            logger.warning("could not revoke the Zoho token of a removed connection")
    db.delete(connection)
    db.flush()
    if secret_ref is not None:
        rt.secrets.delete(db, org_id=connection.org_id, secret_id=secret_ref)
