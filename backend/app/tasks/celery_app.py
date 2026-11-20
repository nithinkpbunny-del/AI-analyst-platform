"""Celery application (worker + beat): connection syncs and snapshot purging (M6), questions (M8), long tasks,
briefings and workspace deletion (M10)."""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.config import get_settings
from app.logging_config import configure_logging

_settings = get_settings()

celery_app = Celery(
    "analyst",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["app.tasks.jobs"],
)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    beat_schedule={
        "heartbeat": {"task": "app.tasks.jobs.ping", "schedule": 300.0},
        # Cached copies of customer data never outlive SNAPSHOT_MAX_AGE_HOURS (BUILD_SPEC 6.6).
        "purge-snapshots": {"task": "app.tasks.jobs.purge_snapshots", "schedule": 900.0},
        # Briefings due in the next window, per person and time zone (BUILD_SPEC 13.1).
        "schedule-briefings": {"task": "app.tasks.jobs.schedule_briefings", "schedule": 300.0},
        # Workspaces past their 7-day deletion grace period (BUILD_SPEC 6.6).
        "purge-deleted-orgs": {"task": "app.tasks.jobs.purge_deleted_orgs", "schedule": 3600.0},
        # Last month's invoice-ready usage summary, frozen once per workspace (M18; nothing is charged).
        "close-billing-months": {
            "task": "app.tasks.jobs.close_billing_months",
            "schedule": 21600.0,
        },
    },
)


@setup_logging.connect
def _configure_celery_logging(**_: Any) -> None:
    """Replace Celery's logging so worker output passes through the same redacting handler as the API."""
    configure_logging(_settings)
