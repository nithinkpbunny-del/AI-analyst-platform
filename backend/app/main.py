"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.config import Settings, get_settings
from app.logging_config import configure_logging, request_id_var

logger = logging.getLogger("app.request")

_MAX_REQUEST_ID_LENGTH = 64


def create_app(
    settings: Settings | None = None,
    readiness_checks: dict[str, health.ReadinessCheck] | None = None,
) -> FastAPI:
    """Build the app. ``settings`` and ``readiness_checks`` are injectable so tests never touch real services."""
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

    # The SPA runs on another origin in development; credentials are needed for the refresh cookie (M2).
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
    return app


# Run with: ``uvicorn app.main:create_app --factory``. There is deliberately no module-level ``app``:
# importing this module (e.g. from tests) must never read the developer's real ``.env``.
