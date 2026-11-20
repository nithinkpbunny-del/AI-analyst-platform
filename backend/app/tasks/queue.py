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

from app.agent import service as agent_service
from app.connectors import service
from app.connectors.registry import ConnectorRuntime
from app.dictionary import service as dictionary_service
from app.llm.base import LLMFactory

SYNC_CONNECTION = "app.tasks.jobs.sync_connection"
DRAFT_DICTIONARY = "app.tasks.jobs.draft_dictionary"
ANSWER_QUESTION = "app.tasks.jobs.answer_question"
RUN_TASK = "app.tasks.jobs.run_task"
SEND_BRIEFING = "app.tasks.jobs.send_briefing"
REFRESH_GOALS = "app.tasks.jobs.refresh_goals"
REFRESH_TILES = "app.tasks.jobs.refresh_tiles"


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

    def draft_dictionary(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        force: bool = False,
        fresh_ai: bool = False,
    ) -> None:
        """Draft or update the workspace's business dictionary (the caller reserved the slot with
        ``dictionary.service.mark_queued``)."""
        ...

    def answer_question(self, asked: agent_service.Asked) -> None:
        """Answer a question the API stored as a ``queued`` message (M8)."""
        ...

    def run_task(self, *, org_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """Run a long task the API stored as ``queued`` (M10)."""
        ...

    def send_briefing(self, *, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        """Compose and send one briefing run (M10)."""
        ...

    def refresh_goals(self, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Compute one person's goal progress (M15; the caller reserved it with ``goals.service.reserve``)."""
        ...

    def refresh_tiles(
        self, *, org_id: uuid.UUID, user_id: uuid.UUID, tile_ids: list[uuid.UUID]
    ) -> None:
        """Re-run dashboard tiles for one person (M15; reserved with ``dashboard.service.reserve``)."""
        ...


def _answer_kwargs(asked: agent_service.Asked) -> dict[str, Any]:
    return {
        "org_id": str(asked.org_id),
        "message_id": str(asked.message_id),
        "ip": asked.ip,
        "user_agent": asked.user_agent,
    }


def _draft_kwargs(
    org_id: uuid.UUID, user_id: uuid.UUID | None, force: bool, fresh_ai: bool
) -> dict[str, Any]:
    return {
        "org_id": str(org_id),
        "user_id": str(user_id) if user_id else None,
        "force": force,
        "fresh_ai": fresh_ai,
    }


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


def _refresh_kwargs(
    org_id: uuid.UUID, user_id: uuid.UUID, tile_ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"org_id": str(org_id), "user_id": str(user_id)}
    if tile_ids is not None:
        out["tile_ids"] = [str(t) for t in tile_ids]
    return out


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

    def draft_dictionary(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        force: bool = False,
        fresh_ai: bool = False,
    ) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(
            DRAFT_DICTIONARY, kwargs=_draft_kwargs(org_id, user_id, force, fresh_ai)
        )

    def answer_question(self, asked: agent_service.Asked) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(ANSWER_QUESTION, kwargs=_answer_kwargs(asked))

    def run_task(self, *, org_id: uuid.UUID, task_id: uuid.UUID) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(RUN_TASK, kwargs={"org_id": str(org_id), "task_id": str(task_id)})

    def send_briefing(self, *, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(SEND_BRIEFING, kwargs={"org_id": str(org_id), "run_id": str(run_id)})

    def refresh_goals(self, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(REFRESH_GOALS, kwargs=_refresh_kwargs(org_id, user_id))

    def refresh_tiles(
        self, *, org_id: uuid.UUID, user_id: uuid.UUID, tile_ids: list[uuid.UUID]
    ) -> None:
        from app.tasks.celery_app import celery_app

        celery_app.send_task(REFRESH_TILES, kwargs=_refresh_kwargs(org_id, user_id, tile_ids))


class InlineJobQueue:
    """Runs jobs at once, in this process. Like the worker, a finished sync leads to a dictionary update."""

    def __init__(self, runtime: ConnectorRuntime, llm_factory: LLMFactory | None = None) -> None:
        self._runtime = runtime
        self._llm_factory = llm_factory

    def sync_connection(
        self,
        *,
        org_id: uuid.UUID,
        connection_id: uuid.UUID,
        force: bool = False,
        file_version_id: uuid.UUID | None = None,
        accept_changes: bool = False,
    ) -> None:
        outcome = service.run_sync_job(
            self._runtime,
            org_id=org_id,
            connection_id=connection_id,
            force=force,
            file_version_id=file_version_id,
            accept_changes=accept_changes,
        )
        if outcome.status == "active" and dictionary_service.mark_queued(self._runtime, org_id):
            self.draft_dictionary(org_id=org_id)

    def draft_dictionary(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        force: bool = False,
        fresh_ai: bool = False,
    ) -> None:
        if self._llm_factory is None:
            dictionary_service.release(self._runtime, org_id)
            return
        dictionary_service.run_draft(
            self._runtime,
            self._llm_factory,
            org_id=org_id,
            user_id=user_id,
            force=force,
            fresh_ai=fresh_ai,
        )

    def answer_question(self, asked: agent_service.Asked) -> None:
        if self._llm_factory is None:
            return
        agent_service.answer_question(self._runtime, self._llm_factory, asked)

    def run_task(self, *, org_id: uuid.UUID, task_id: uuid.UUID) -> None:
        if self._llm_factory is None:
            return
        from app.tasks import long_tasks

        long_tasks.run_task(self._runtime, self._llm_factory, org_id, task_id)

    def send_briefing(self, *, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        if self._llm_factory is None:
            return
        from app.briefings import service as briefings

        briefings.send_briefing(self._runtime, self._llm_factory, org_id, run_id)

    # No AI is involved: these run even without an AI factory.
    def refresh_goals(self, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        from app.goals import service as goal_service

        goal_service.refresh(self._runtime, org_id, user_id)

    def refresh_tiles(
        self, *, org_id: uuid.UUID, user_id: uuid.UUID, tile_ids: list[uuid.UUID]
    ) -> None:
        from app.dashboard import service as dashboard

        dashboard.refresh(self._runtime, org_id, user_id, tile_ids)


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

    def draft_dictionary(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        force: bool = False,
        fresh_ai: bool = False,
    ) -> None:
        self.jobs.append((DRAFT_DICTIONARY, _draft_kwargs(org_id, user_id, force, fresh_ai)))

    def answer_question(self, asked: agent_service.Asked) -> None:
        self.jobs.append((ANSWER_QUESTION, _answer_kwargs(asked)))

    def run_task(self, *, org_id: uuid.UUID, task_id: uuid.UUID) -> None:
        self.jobs.append((RUN_TASK, {"org_id": str(org_id), "task_id": str(task_id)}))

    def send_briefing(self, *, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.jobs.append((SEND_BRIEFING, {"org_id": str(org_id), "run_id": str(run_id)}))

    def refresh_goals(self, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.jobs.append((REFRESH_GOALS, _refresh_kwargs(org_id, user_id)))

    def refresh_tiles(
        self, *, org_id: uuid.UUID, user_id: uuid.UUID, tile_ids: list[uuid.UUID]
    ) -> None:
        self.jobs.append((REFRESH_TILES, _refresh_kwargs(org_id, user_id, tile_ids)))
