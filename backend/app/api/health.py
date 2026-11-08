"""Health endpoints.

* ``GET /health``        liveness: the process is up. No dependencies are touched.
* ``GET /health/ready``  readiness: database, Redis and object storage answer within a short timeout.

Failure details are logged (redacted) but never returned to the caller.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Literal

import redis
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.config import Settings
from app.db.session import make_engine
from app.storage import make_s3_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

ReadinessCheck = Callable[[], None]  # raises on failure


class LiveResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    env: str


class ReadyResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "failed"]]


def check_database(settings: Settings) -> None:
    engine = make_engine(
        settings.database_url,
        connect_timeout_seconds=max(1, int(settings.health_check_timeout_seconds)),
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def check_redis(settings: Settings) -> None:
    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.health_check_timeout_seconds,
        socket_timeout=settings.health_check_timeout_seconds,
    )
    try:
        client.ping()
    finally:
        client.close()


def check_storage(settings: Settings) -> None:
    make_s3_client(settings, timeout_seconds=settings.health_check_timeout_seconds).head_bucket(
        Bucket=settings.s3_bucket
    )


def default_checks(settings: Settings) -> dict[str, ReadinessCheck]:
    return {
        "database": lambda: check_database(settings),
        "redis": lambda: check_redis(settings),
        "storage": lambda: check_storage(settings),
    }


@router.get("/health", response_model=LiveResponse, summary="Liveness")
def live(request: Request) -> LiveResponse:
    settings: Settings = request.app.state.settings
    return LiveResponse(
        status="ok", service="analyst-api", version=__version__, env=settings.app_env
    )


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    summary="Readiness (database, Redis, storage)",
    responses={503: {"model": ReadyResponse}},
)
def ready(request: Request, response: Response) -> ReadyResponse:
    checks: Mapping[str, ReadinessCheck] = request.app.state.readiness_checks
    results: dict[str, Literal["ok", "failed"]] = {}
    for name, check in checks.items():
        try:
            check()
            results[name] = "ok"
        except Exception:
            # Exception text can contain connection strings; the log formatter redacts it, the response omits it.
            logger.warning("readiness check failed: %s", name, exc_info=True)
            results[name] = "failed"
    healthy = all(value == "ok" for value in results.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ok" if healthy else "unavailable", checks=results)
