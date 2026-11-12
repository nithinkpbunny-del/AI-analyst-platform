"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.api import (
    ai,
    audit,
    auth,
    chat,
    connections,
    dictionary,
    google,
    health,
    invitations,
    org,
    team,
    two_factor,
    whatif,
)
from app.api.errors import install_error_handlers
from app.auth.google_oidc import AuthlibGoogleProvider, GoogleProvider, StandInGoogleProvider
from app.auth.service import Clock
from app.config import Settings, get_settings
from app.connectors.google_sheets import GoogleSheetsSetup, default_setup
from app.connectors.registry import ConnectorRuntime
from app.data.objects import ObjectStore, S3ObjectStore
from app.db.session import make_engine, make_session_factory
from app.email.sender import EmailSender, SmtpEmailSender
from app.llm.base import LLMFactory
from app.llm.keys import default_factory
from app.logging_config import configure_logging, request_id_var
from app.security.secrets import SecretStore, key_wrapper
from app.tasks.queue import CeleryJobQueue, InlineJobQueue, JobQueue

logger = logging.getLogger("app.request")

_MAX_REQUEST_ID_LENGTH = 64


def _utc_now() -> datetime:
    return datetime.now(UTC)


def default_google_provider(
    settings: Settings, redis_client: redis.Redis[bytes]
) -> GoogleProvider | None:
    """Real Google when a client id is configured; the local stand-in in development; otherwise none."""
    if settings.google_oauth_client_id:
        return AuthlibGoogleProvider(
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret.get_secret_value(),
            http=httpx.Client(),
        )
    if settings.app_env == "development":
        return StandInGoogleProvider(
            redis_client, api_base_url=settings.api_base_url, app_env=settings.app_env
        )
    return None


def create_app(
    settings: Settings | None = None,
    readiness_checks: dict[str, health.ReadinessCheck] | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    redis_client: redis.Redis[bytes] | None = None,
    email_sender: EmailSender | None = None,
    clock: Clock | None = None,
    google_provider: GoogleProvider | None = None,
    llm_factory: LLMFactory | None = None,
    secret_store: SecretStore | None = None,
    object_store: ObjectStore | None = None,
    google_sheets: GoogleSheetsSetup | None = None,
    job_queue: JobQueue | None = None,
    inline_jobs: bool = False,
) -> FastAPI:
    """Build the app. Every dependency is injectable so tests never touch real services.

    Defaults: engine and Redis client from settings (both connect lazily, on first use), SMTP for email,
    the system clock, ``default_google_provider``, the real LLM providers, secrets sealed with ``MASTER_KEY``
    (or KMS when ``KMS_KEY_ID`` is set), the S3 bucket, Google Sheets from ``default_setup`` and background jobs
    sent to Celery (``inline_jobs=True`` runs them in this process instead, for tests).
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
    app.state.google_provider = google_provider or default_google_provider(
        settings, app.state.redis
    )
    app.state.llm_factory = llm_factory or default_factory(settings)
    app.state.secret_store = secret_store or SecretStore(key_wrapper(settings))
    app.state.connector_runtime = ConnectorRuntime(
        settings=settings,
        session_factory=app.state.session_factory,
        objects=object_store or S3ObjectStore(settings),
        redis=app.state.redis,
        secrets=app.state.secret_store,
        google=google_sheets or default_setup(settings),
        clock=app.state.clock,
    )
    app.state.job_queue = job_queue or (
        InlineJobQueue(app.state.connector_runtime, app.state.llm_factory)
        if inline_jobs
        else CeleryJobQueue()
    )
    install_error_handlers(app)

    # The SPA runs on another origin in development; credentials are needed for the refresh cookie.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_base_url, settings.site_base_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID", "Content-Disposition", "X-Rows-Truncated"],
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
    app.include_router(two_factor.router)
    app.include_router(google.router)
    app.include_router(invitations.router)
    app.include_router(team.router)
    app.include_router(org.router)
    app.include_router(ai.router)
    app.include_router(connections.router)
    app.include_router(dictionary.router)
    app.include_router(chat.router)
    app.include_router(whatif.router)
    app.include_router(audit.router)
    return app


# Run with: ``uvicorn app.main:create_app --factory``. There is deliberately no module-level ``app``:
# importing this module (e.g. from tests) must never read the developer's real ``.env``.
