"""Task definitions. Each task is a thin wrapper: the work itself lives in plain, tested functions."""

from __future__ import annotations

import logging
import uuid

from app.connectors import service
from app.data import snapshots
from app.tasks.celery_app import celery_app
from app.tasks.runtime import worker_runtime

logger = logging.getLogger(__name__)

# A 50 MB workbook or a large spreadsheet can take a few minutes; nothing should run longer than this.
SYNC_TIME_LIMIT_SECONDS = 900


@celery_app.task(name="app.tasks.jobs.ping")
def ping() -> str:
    """Smoke-test task: proves that the broker, worker and beat are wired together."""
    logger.info("celery ping")
    return "pong"


@celery_app.task(name="app.tasks.jobs.sync_connection", time_limit=SYNC_TIME_LIMIT_SECONDS)
def sync_connection(
    org_id: str,
    connection_id: str,
    force: bool = False,
    file_version_id: str | None = None,
    accept_changes: bool = False,
) -> str:  # pragma: no cover - the body is service.run_sync_job, tested directly
    """Read a connection's datasets (after an upload, a new Google sign-in or "Refresh"), or switch to a file
    version an Owner chose (``accept_changes``)."""
    outcome = service.run_sync_job(
        worker_runtime(),
        org_id=uuid.UUID(org_id),
        connection_id=uuid.UUID(connection_id),
        force=force,
        file_version_id=uuid.UUID(file_version_id) if file_version_id else None,
        accept_changes=accept_changes,
    )
    return outcome.status


@celery_app.task(name="app.tasks.jobs.purge_snapshots")
def purge_snapshots() -> (
    int
):  # pragma: no cover - the body is snapshots.purge_expired, tested directly
    """Delete snapshots older than SNAPSHOT_MAX_AGE_HOURS in every workspace (BUILD_SPEC 6.6)."""
    runtime = worker_runtime()
    return snapshots.purge_expired(runtime.session_factory, runtime.objects, runtime.clock())
