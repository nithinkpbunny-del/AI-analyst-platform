"""Task definitions. Each task is a thin wrapper: the work itself lives in plain, tested functions."""

from __future__ import annotations

import logging
import uuid

from app.agent import service as agent_service
from app.connectors import service
from app.data import snapshots
from app.dictionary import service as dictionary_service
from app.llm.keys import default_factory
from app.tasks.celery_app import celery_app
from app.tasks.runtime import worker_runtime

logger = logging.getLogger(__name__)

# Drafting may make a few AI calls of up to a few minutes each.
DRAFT_TIME_LIMIT_SECONDS = 1500

# A question: up to 12 model calls of at most a few minutes each, plus reading the sources.
ANSWER_TIME_LIMIT_SECONDS = 1500

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
    # A finished read may have changed the data's structure: update the business dictionary (M7).
    if outcome.status == "active" and dictionary_service.mark_queued(
        worker_runtime(), uuid.UUID(org_id)
    ):
        celery_app.send_task(
            "app.tasks.jobs.draft_dictionary",
            kwargs={"org_id": org_id, "user_id": None, "force": False, "fresh_ai": False},
        )
    return outcome.status


@celery_app.task(name="app.tasks.jobs.draft_dictionary", time_limit=DRAFT_TIME_LIMIT_SECONDS)
def draft_dictionary(
    org_id: str, user_id: str | None = None, force: bool = False, fresh_ai: bool = False
) -> str:  # pragma: no cover - the body is dictionary.service.run_draft, tested directly
    """Profile the workspace's data and draft (or update) its business dictionary."""
    runtime = worker_runtime()
    outcome = dictionary_service.run_draft(
        runtime,
        default_factory(runtime.settings),
        org_id=uuid.UUID(org_id),
        user_id=uuid.UUID(user_id) if user_id else None,
        force=force,
        fresh_ai=fresh_ai,
    )
    return outcome.status


@celery_app.task(name="app.tasks.jobs.answer_question", time_limit=ANSWER_TIME_LIMIT_SECONDS)
def answer_question(
    org_id: str, message_id: str, ip: str | None = None, user_agent: str | None = None
) -> str:  # pragma: no cover - the body is agent.service.answer_question, tested directly
    """Answer one question with the agent (M8)."""
    runtime = worker_runtime()
    return agent_service.answer_question(
        runtime,
        default_factory(runtime.settings),
        agent_service.Asked(uuid.UUID(org_id), uuid.UUID(message_id), ip, user_agent),
    )


@celery_app.task(name="app.tasks.jobs.purge_snapshots")
def purge_snapshots() -> (
    int
):  # pragma: no cover - the body is snapshots.purge_expired, tested directly
    """Delete snapshots older than SNAPSHOT_MAX_AGE_HOURS in every workspace (BUILD_SPEC 6.6)."""
    runtime = worker_runtime()
    return snapshots.purge_expired(runtime.session_factory, runtime.objects, runtime.clock())
