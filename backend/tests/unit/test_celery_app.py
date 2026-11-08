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
