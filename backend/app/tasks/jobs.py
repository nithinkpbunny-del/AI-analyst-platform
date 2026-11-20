"""Task definitions. Each task is a thin wrapper: the work itself lives in plain, tested functions."""

from __future__ import annotations

import logging
import uuid

from app.agent import service as agent_service
from app.billing import summary as billing_summary
from app.briefings import schedule as briefing_schedule
from app.briefings import service as briefings
from app.connectors import service
from app.data import snapshots
from app.dictionary import service as dictionary_service
from app.llm.keys import default_factory
from app.privacy import deletion
from app.tasks import long_tasks
from app.tasks.celery_app import celery_app
from app.tasks.runtime import worker_runtime

logger = logging.getLogger(__name__)

# Drafting may make a few AI calls of up to a few minutes each.
DRAFT_TIME_LIMIT_SECONDS = 1500

# A long task: up to 40 model calls (BUILD_SPEC 6.5) with the long source timeout.
TASK_TIME_LIMIT_SECONDS = 3600

# A briefing: a few analytics queries and one small AI call.
BRIEFING_TIME_LIMIT_SECONDS = 600

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


@celery_app.task(name="app.tasks.jobs.run_task", time_limit=TASK_TIME_LIMIT_SECONDS)
def run_task(
    org_id: str, task_id: str
) -> str:  # pragma: no cover - the body is long_tasks.run_task
    """Run a long task and email its report (M10)."""
    runtime = worker_runtime()
    return long_tasks.run_task(
        runtime, default_factory(runtime.settings), uuid.UUID(org_id), uuid.UUID(task_id)
    )


@celery_app.task(name="app.tasks.jobs.send_briefing", time_limit=BRIEFING_TIME_LIMIT_SECONDS)
def send_briefing(
    org_id: str, run_id: str
) -> str:  # pragma: no cover - the body is briefings.send_briefing
    """Compose and send one briefing (M10)."""
    runtime = worker_runtime()
    return briefings.send_briefing(
        runtime, default_factory(runtime.settings), uuid.UUID(org_id), uuid.UUID(run_id)
    )


@celery_app.task(name="app.tasks.jobs.close_billing_months")
def close_billing_months() -> (
    int
):  # pragma: no cover - the body is billing.summary.close_months, tested directly
    """Freeze last month's invoice-ready usage summary for every workspace (M18). Nothing is charged."""
    runtime = worker_runtime()
    return billing_summary.close_months(runtime, runtime.clock())


# Goal progress and dashboard tiles: analytics over live data, no AI call (M15).
REFRESH_TIME_LIMIT_SECONDS = 600


@celery_app.task(name="app.tasks.jobs.refresh_goals", time_limit=REFRESH_TIME_LIMIT_SECONDS)
def refresh_goals(
    org_id: str, user_id: str
) -> str:  # pragma: no cover - body: goals.service.refresh
    """Compute one person's goal progress with their permissions (M15)."""
    from app.goals import service as goal_service

    return goal_service.refresh(worker_runtime(), uuid.UUID(org_id), uuid.UUID(user_id))


@celery_app.task(name="app.tasks.jobs.refresh_tiles", time_limit=REFRESH_TIME_LIMIT_SECONDS)
def refresh_tiles(
    org_id: str, user_id: str, tile_ids: list[str]
) -> int:  # pragma: no cover - body: dashboard.service.refresh
    """Re-run dashboard tiles for one person with their permissions (M15)."""
    from app.dashboard import service as dashboard

    return dashboard.refresh(
        worker_runtime(), uuid.UUID(org_id), uuid.UUID(user_id), [uuid.UUID(t) for t in tile_ids]
    )


@celery_app.task(name="app.tasks.jobs.schedule_briefings")
def schedule_briefings() -> (
    int
):  # pragma: no cover - the body is briefing_schedule.plan, tested directly
    """Every 5 minutes: add the briefing runs that are due and queue them (BUILD_SPEC 13.1)."""
    runtime = worker_runtime()
    with runtime.session_factory() as db:
        created = briefing_schedule.plan(db, runtime.clock())
    for org_id, run_id in created:
        celery_app.send_task(
            "app.tasks.jobs.send_briefing", kwargs={"org_id": str(org_id), "run_id": str(run_id)}
        )
    return len(created)


@celery_app.task(name="app.tasks.jobs.purge_deleted_orgs")
def purge_deleted_orgs() -> (
    int
):  # pragma: no cover - the body is deletion.purge_due, tested directly
    """Hourly: delete workspaces whose 7-day grace period has ended (BUILD_SPEC 6.6)."""
    runtime = worker_runtime()
    return deletion.purge_due(runtime, runtime.clock())
