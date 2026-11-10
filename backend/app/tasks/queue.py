"""Handing work to the background queue (BUILD_SPEC 3: Celery + Redis).

The API talks to a ``JobQueue``, never to Celery directly:

* ``CeleryJobQueue`` (default): sends the job by name to the Celery worker.
* ``InlineJobQueue``: runs the job at once in this process (tests, scripts).
* ``RecordingJobQueue``: only remembers what was queued (tests of "queued" states).

Callers commit their database changes before queueing, so the worker always finds the rows it is told about.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.connectors import service
from app.connectors.registry import ConnectorRuntime

SYNC_CONNECTION = "app.tasks.jobs.sync_connection"


class JobQueue(Protocol):
    def sync_connection(
        self,
        *,
        org_id: uuid.UUID,
        connection_id: uuid.UUID,
        force: bool = False,
        file_version_id: uuid.UUID | None = None,
        accept_changes: bool = False,
    ) -> None: ...


def _sync_kwargs(
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    force: bool,
    file_version_id: uuid.UUID | None,
    accept_changes: bool = False,
) -> dict[str, Any]:
    return {
        "org_id": str(org_id),
        "connection_id": str(connection_id),
        "force": force,
        "file_version_id": str(file_version_id) if file_version_id else None,
        "accept_changes": accept_changes,
    }


class CeleryJobQueue:
    def sync_connection(
        self,
        *,
        org_id: uuid.UUID,
        connection_id: uuid.UUID,
        force: bool = False,
        file_version_id: uuid.UUID | None = None,
        accept_changes: bool = False,
    ) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(
            SYNC_CONNECTION,
            kwargs=_sync_kwargs(org_id, connection_id, force, file_version_id, accept_changes),
        )


class InlineJobQueue:
    def __init__(self, runtime: ConnectorRuntime) -> None:
        self._runtime = runtime

    def sync_connection(
        self,
        *,
        org_id: uuid.UUID,
        connection_id: uuid.UUID,
        force: bool = False,
        file_version_id: uuid.UUID | None = None,
        accept_changes: bool = False,
    ) -> None:
        service.run_sync_job(
            self._runtime,
            org_id=org_id,
            connection_id=connection_id,
            force=force,
            file_version_id=file_version_id,
            accept_changes=accept_changes,
        )


@dataclass
class RecordingJobQueue:
    jobs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def sync_connection(
        self,
        *,
        org_id: uuid.UUID,
        connection_id: uuid.UUID,
        force: bool = False,
        file_version_id: uuid.UUID | None = None,
        accept_changes: bool = False,
    ) -> None:
        self.jobs.append(
            (
                SYNC_CONNECTION,
                _sync_kwargs(org_id, connection_id, force, file_version_id, accept_changes),
            )
        )
