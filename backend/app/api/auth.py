"""``/auth`` routes (BUILD_SPEC 6.1, R15).

Cookies:
* ``analyst_refresh``: the opaque refresh token. HttpOnly, SameSite=Strict, Secure (see ``COOKIE_SECURE``),
  path ``/auth`` so it is only sent to these routes. Rotated on every refresh.
* ``analyst_csrf``: random value, readable by the web app. Cookie-authenticated routes (refresh, logout)
  require it echoed in the ``X-CSRF-Token`` header (double submit).

Everything else uses the short-lived access token in ``Authorization: Bearer``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.errors import ApiError
from app.auth.deps import (
    CSRF_COOKIE,
    REFRESH_COOKIE,
    AuthServiceDep,
    CurrentAuth,
    require_csrf,
)
from app.auth.service import AuthContext, AuthService, IssuedSession
from app.auth.tokens import new_opaque_token
from app.config import Settings
from app.email.sender import EmailSender
from app.models import Membership, Organization
from app.schemas.auth import (
    Accepted,
    CsrfOut,
    Done,
    EmailRequest,
    LoginRequest,
    Me,
    MembershipOut,
    OrganizationOut,
    ResetPasswordRequest,
    SessionOut,
    SignupRequest,
    TokenRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in, or the session ended"},
    403: {"description": "Forbidden (e.g. CSRF check failed, email not confirmed)"},
    422: {"description": "Validation error"},
    429: {"description": "Too many attempts"},
}


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _send_queued(request: Request, background: BackgroundTasks, service: AuthService) -> None:
    sender: EmailSender = request.app.state.email_sender
    for message in service.outbox:
        background.add_task(sender.send, message)


def _csrf_value(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or new_opaque_token()


def _set_auth_cookies(
    response: Response, request: Request, issued: IssuedSession, csrf: str, now: datetime
) -> None:
    settings = _settings(request)
    remaining = max(1, int((issued.session.expires_at - now).total_seconds()))
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=remaining,
        path="/auth",
        httponly=True,
        secure=settings.cookies_secure,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=remaining,
        path="/",
        httponly=False,
        secure=settings.cookies_secure,
        samesite="strict",
    )


def _clear_auth_cookies(response: Response, request: Request) -> None:
    secure = _settings(request).cookies_secure
    response.delete_cookie(
        REFRESH_COOKIE, path="/auth", secure=secure, httponly=True, samesite="strict"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="strict")


def _memberships(rows: list[tuple[Membership, Organization]]) -> list[MembershipOut]:
    return [
        MembershipOut(organization_id=o.id, organization_name=o.name, role=m.role) for m, o in rows
    ]


def _me(
    request: Request,
    *,
    user: Any,
    organization: Organization,
    role: str,
    session_expires_at: datetime,
    memberships: list[tuple[Membership, Organization]],
) -> dict[str, Any]:
    return {
        "user": UserOut(
            id=user.id, email=user.email, name=user.name, preferred_language=user.preferred_language
        ),
        "organization": OrganizationOut(
            id=organization.id,
            name=organization.name,
            timezone=organization.timezone,
            currency=organization.currency,
            plan=organization.plan,
        ),
        "role": role,
        "memberships": _memberships(memberships),
        "session_expires_at": session_expires_at.isoformat(),
        "idle_timeout_minutes": _settings(request).session_idle_minutes,
    }


def _session_out(request: Request, issued: IssuedSession, csrf: str, now: datetime) -> SessionOut:
    return SessionOut(
        **_me(
            request,
            user=issued.user,
            organization=issued.organization,
            role=issued.role,
            session_expires_at=issued.session.expires_at,
            memberships=issued.memberships,
        ),
        access_token=issued.access_token,
        expires_in=max(0, int((issued.access_expires_at - now).total_seconds())),
        csrf_token=csrf,
    )


@router.get("/csrf", response_model=CsrfOut, summary="Get (or start) the CSRF token")
def csrf(request: Request, response: Response) -> CsrfOut:
    value = _csrf_value(request)
    settings = _settings(request)
    response.set_cookie(
        CSRF_COOKIE,
        value,
        max_age=settings.session_max_hours * 3600,
        path="/",
        httponly=False,
        secure=settings.cookies_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return CsrfOut(csrf_token=value)


@router.post(
    "/signup",
    response_model=Accepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Create an organisation with the caller as Owner",
)
def signup(
    body: SignupRequest, request: Request, background: BackgroundTasks, service: AuthServiceDep
) -> Accepted:
    service.signup(
        name=body.name,
        email=str(body.email),
        password=body.password,
        organization_name=body.organization_name,
    )
    _send_queued(request, background, service)
    # Same answer whether or not the email already had an account (no account enumeration).
    return Accepted(message="Check your email for a link to confirm your address.")


@router.post(
    "/verify-email", response_model=Done, responses=_ERRORS, summary="Confirm an email address"
)
def verify_email(body: TokenRequest, service: AuthServiceDep) -> Done:
    service.verify_email(body.token)
    return Done(message="Your email address is confirmed. You can sign in now.")


@router.post(
    "/resend-verification",
    response_model=Accepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Send the confirmation email again",
)
def resend_verification(
    body: EmailRequest, request: Request, background: BackgroundTasks, service: AuthServiceDep
) -> Accepted:
    service.resend_verification(str(body.email))
    _send_queued(request, background, service)
    return Accepted(
        message="If this address has an account that is not confirmed yet, we sent a new link."
    )


@router.post(
    "/login",
    response_model=SessionOut,
    responses=_ERRORS,
    summary="Sign in with email and password",
)
def login(
    body: LoginRequest, request: Request, response: Response, service: AuthServiceDep
) -> SessionOut:
    issued = service.login(
        email=str(body.email), password=body.password, organization_id=body.organization_id
    )
    now = request.app.state.clock()
    csrf_token = _csrf_value(request)
    _set_auth_cookies(response, request, issued, csrf_token, now)
    response.headers["Cache-Control"] = "no-store"
    return _session_out(request, issued, csrf_token, now)


@router.post(
    "/refresh",
    response_model=SessionOut,
    responses=_ERRORS,
    dependencies=[Depends(require_csrf)],
    summary="Rotate the refresh token and get a new access token",
)
def refresh(
    request: Request, response: Response, service: AuthServiceDep
) -> SessionOut | JSONResponse:
    try:
        issued = service.refresh(request.cookies.get(REFRESH_COOKIE))
    except ApiError as error:
        # The refresh cookie no longer works: tell the browser to drop it.
        failed = JSONResponse(
            {"error": {"code": error.code, "message": error.message}},
            status_code=error.status_code,
        )
        _clear_auth_cookies(failed, request)
        return failed
    now = request.app.state.clock()
    csrf_token = request.cookies.get(CSRF_COOKIE, "")
    _set_auth_cookies(response, request, issued, csrf_token, now)
    response.headers["Cache-Control"] = "no-store"
    return _session_out(request, issued, csrf_token, now)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    dependencies=[Depends(require_csrf)],
    summary="Sign out this browser",
)
def logout(request: Request, service: AuthServiceDep) -> Response:
    service.logout(request.cookies.get(REFRESH_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_auth_cookies(response, request)
    return response


@router.post(
    "/forgot-password",
    response_model=Accepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Email a password-reset link",
)
def forgot_password(
    body: EmailRequest, request: Request, background: BackgroundTasks, service: AuthServiceDep
) -> Accepted:
    service.forgot_password(str(body.email))
    _send_queued(request, background, service)
    return Accepted(message="If this address has an account, we sent a link to reset the password.")


@router.post(
    "/reset-password", response_model=Done, responses=_ERRORS, summary="Set a new password"
)
def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> Done:
    service.reset_password(body.token, body.password)
    _send_queued(request, background, service)
    return Done(message="Your password is changed. Sign in with the new password.")


@router.get("/me", response_model=Me, responses=_ERRORS, summary="Who is signed in")
def me(request: Request, auth: CurrentAuth) -> Me:
    return Me(**_me_from_auth(request, auth))


def _me_from_auth(request: Request, auth: AuthContext) -> dict[str, Any]:
    return _me(
        request,
        user=auth.user,
        organization=auth.organization,
        role=auth.role,
        session_expires_at=auth.session.expires_at,
        memberships=auth.memberships,
    )
