from __future__ import annotations

from app.tasks.celery_app import celery_app
from app.tasks.jobs import ping


def test_ping_task_returns_pong() -> None:
    assert ping() == "pong"


def test_task_is_registered_and_scheduled() -> None:
    assert "app.tasks.jobs.ping" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule
    assert schedule["heartbeat"]["task"] == "app.tasks.jobs.ping"


def test_celery_only_accepts_json() -> None:
    assert celery_app.conf.task_serializer == "json"
    assert list(celery_app.conf.accept_content) == ["json"]


def test_connection_jobs_are_registered_and_snapshots_are_purged_on_a_schedule() -> None:
    assert "app.tasks.jobs.sync_connection" in celery_app.tasks
    assert "app.tasks.jobs.purge_snapshots" in celery_app.tasks
    purge = celery_app.conf.beat_schedule["purge-snapshots"]
    assert purge["task"] == "app.tasks.jobs.purge_snapshots"
    assert purge["schedule"] <= 3600  # far below SNAPSHOT_MAX_AGE_HOURS


def test_the_api_queues_jobs_by_name(monkeypatch: object) -> None:
    import uuid

    from app.tasks.queue import SYNC_CONNECTION, CeleryJobQueue

    sent: list[tuple[str, dict[str, object]]] = []

    def send_task(name: str, kwargs: dict[str, object]) -> None:
        sent.append((name, kwargs))

    import pytest

    patch = pytest.MonkeyPatch()
    patch.setattr(celery_app, "send_task", send_task)
    try:
        org, conn = uuid.uuid4(), uuid.uuid4()
        CeleryJobQueue().sync_connection(org_id=org, connection_id=conn)
    finally:
        patch.undo()
    assert sent == [
        (
            SYNC_CONNECTION,
            {
                "org_id": str(org),
                "connection_id": str(conn),
                "force": False,
                "file_version_id": None,
                "accept_changes": False,
            },
        )
    ]


def test_questions_have_their_own_queue_and_everything_else_the_default() -> None:
    """M19 (D122): a long task or the morning's briefings never make a question wait behind them."""
    from app.tasks.celery_app import DEFAULT_QUEUE, QUESTIONS_QUEUE
    from app.tasks.queue import ANSWER_QUESTION, RUN_TASK, SEND_BRIEFING, SYNC_CONNECTION

    router = celery_app.amqp.router

    def queue_of(name: str) -> str:
        route = router.route({}, name, args=(), kwargs={})
        queue = route["queue"]
        return str(getattr(queue, "name", queue))

    assert queue_of(ANSWER_QUESTION) == QUESTIONS_QUEUE == "questions"
    for name in (RUN_TASK, SEND_BRIEFING, SYNC_CONNECTION):
        assert queue_of(name) == DEFAULT_QUEUE == "celery"


def test_the_local_worker_reads_both_queues() -> None:
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text()
    assert "worker --loglevel=INFO -Q questions,celery" in compose
