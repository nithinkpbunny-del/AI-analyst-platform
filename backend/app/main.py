"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.api import auth, health
from app.api.errors import install_error_handlers
from app.auth.service import Clock
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.email.sender import EmailSender, SmtpEmailSender
from app.logging_config import configure_logging, request_id_var

logger = logging.getLogger("app.request")

_MAX_REQUEST_ID_LENGTH = 64


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    readiness_checks: dict[str, health.ReadinessCheck] | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    redis_client: redis.Redis[bytes] | None = None,
    email_sender: EmailSender | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the app. Every dependency is injectable so tests never touch real services.

    Defaults: engine and Redis client from settings (both connect lazily, on first use), SMTP for email,
    the system clock.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="AI Business Analyst Platform",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.readiness_checks = (
        readiness_checks if readiness_checks is not None else health.default_checks(settings)
    )
    app.state.session_factory = session_factory or make_session_factory(
        make_engine(settings.database_url)
    )
    app.state.redis = redis_client or redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    app.state.email_sender = email_sender or SmtpEmailSender(settings)
    app.state.clock = clock or _utc_now
    install_error_handlers(app)

    # The SPA runs on another origin in development; credentials are needed for the refresh cookie.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_base_url, settings.site_base_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = (
            incoming[:_MAX_REQUEST_ID_LENGTH]
            if incoming.isprintable() and incoming
            else uuid4().hex
        )
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("%s %s -> unhandled error", request.method, request.url.path)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            # Path only: query strings can carry tokens.
            logger.info("%s %s (%.1f ms)", request.method, request.url.path, elapsed_ms)
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(health.router)
    app.include_router(auth.router)
    return app


# Run with: ``uvicorn app.main:create_app --factory``. There is deliberately no module-level ``app``:
# importing this module (e.g. from tests) must never read the developer's real ``.env``.
