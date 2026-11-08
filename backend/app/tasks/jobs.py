"""Task definitions."""

from __future__ import annotations

import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.jobs.ping")
def ping() -> str:
    """Smoke-test task: proves that the broker, worker and beat are wired together."""
    logger.info("celery ping")
    return "pong"
