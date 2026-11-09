"""FastAPI dependencies: database session, the auth service, the current user and the CSRF check."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.api.errors import ApiError
from app.audit.writer import RequestMeta
from app.auth.ratelimit import FixedWindowLimiter, LoginThrottle
from app.auth.service import (
    EMAIL_LIMIT_PER_ADDRESS,
    EMAIL_LIMIT_PER_IP,
    AuthContext,
    AuthService,
)
from app.auth.tokens import tokens_match
from app.config import Settings

REFRESH_COOKIE = "analyst_refresh"
CSRF_COOKIE = "analyst_csrf"
CSRF_HEADER = "X-CSRF-Token"


def get_db(request: Request) -> Iterator[DbSession]:
    """A database session per request. Services commit explicitly; anything left over is rolled back."""
    db: DbSession = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def request_meta(request: Request) -> RequestMeta:
    # Behind a proxy this is the proxy's address; trusted forwarding headers are configured at deploy (M19).
    ip = request.client.host if request.client else None
    return RequestMeta(ip=ip, user_agent=request.headers.get("user-agent"))


def get_auth_service(request: Request, db: Annotated[DbSession, Depends(get_db)]) -> AuthService:
    settings: Settings = request.app.state.settings
    redis_client = request.app.state.redis
    return AuthService(
        db=db,
        settings=settings,
        clock=request.app.state.clock,
        login_throttle=LoginThrottle(
            redis_client,
            max_failures=settings.login_max_failures,
            window=timedelta(minutes=settings.login_window_minutes),
        ),
        email_limiter=FixedWindowLimiter(
            redis_client,
            prefix="email-to",
            limit=EMAIL_LIMIT_PER_ADDRESS,
            window=timedelta(hours=1),
        ),
        ip_limiter=FixedWindowLimiter(
            redis_client, prefix="email-ip", limit=EMAIL_LIMIT_PER_IP, window=timedelta(hours=1)
        ),
        meta=request_meta(request),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def current_auth(request: Request, service: AuthServiceDep) -> AuthContext:
    """The signed-in caller, from ``Authorization: Bearer <access token>``. 401 otherwise."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(
            401, "not_signed_in", "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"}
        )
    return service.authenticate(token.strip())


CurrentAuth = Annotated[AuthContext, Depends(current_auth)]


def require_csrf(request: Request) -> None:
    """Double-submit check for cookie-authenticated endpoints (BUILD_SPEC 6.1)."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not tokens_match(cookie, header):
        raise ApiError(
            403, "csrf_failed", "This request could not be verified. Reload the page and try again."
        )
