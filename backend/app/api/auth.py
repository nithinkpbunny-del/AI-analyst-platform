"""``/auth`` routes (BUILD_SPEC 6.1, R15).

Cookies:
* ``analyst_refresh``: the opaque refresh token. HttpOnly, SameSite=Strict, Secure (see ``COOKIE_SECURE``),
  path ``/auth`` so it is only sent to these routes. Rotated on every refresh.
* ``analyst_csrf``: random value, readable by the web app. Cookie-authenticated routes (refresh, logout)
  require it echoed in the ``X-CSRF-Token`` header (double submit).
* ``analyst_device``: signed random device id (HttpOnly, path ``/auth``), for "New sign-in" emails.

Everything else uses the short-lived access token in ``Authorization: Bearer``. Sign-in challenges (2FA) use an
opaque token in the JSON body that the web app keeps in memory; it is useless without the second factor.
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
from app.auth.devices import DEVICE_COOKIE, DEVICE_COOKIE_MAX_AGE
from app.auth.permissions import permissions_for
from app.auth.service import (
    AuthService,
    IssuedSession,
    LoginOutcome,
    PendingSignIn,
    SwitchedSession,
)
from app.auth.tokens import new_opaque_token
from app.auth.two_factor import SetupStarted
from app.config import Settings
from app.email.sender import EmailSender
from app.models import Membership, Organization, User
from app.schemas.auth import (
    Accepted,
    ChallengeCodeRequest,
    ChallengeOut,
    ChallengeRequest,
    ChallengeSetupRequest,
    CsrfOut,
    Done,
    EmailRequest,
    LoginOut,
    LoginRequest,
    Me,
    MembershipOut,
    OrganizationOut,
    ResetPasswordRequest,
    SessionOut,
    SetupDoneOut,
    SetupOut,
    SignupRequest,
    SwitchedOut,
    SwitchOrganizationRequest,
    TokenRequest,
    TwoFactorOut,
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


def send_queued(request: Request, background: BackgroundTasks, service: AuthService) -> None:
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
    if issued.device_cookie:
        response.set_cookie(
            DEVICE_COOKIE,
            issued.device_cookie,
            max_age=DEVICE_COOKIE_MAX_AGE,
            path="/auth",
            httponly=True,
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
    service: AuthService,
    *,
    user: User,
    organization: Organization,
    role: str,
    session_expires_at: datetime,
    memberships: list[tuple[Membership, Organization]],
) -> dict[str, Any]:
    two_factor = service.factors.summary(user.id)
    return {
        "permissions": permissions_for(role, two_factor_enabled=two_factor.enabled),
        "user": UserOut(
            id=user.id,
            email=user.email,
            name=user.name,
            preferred_language=user.preferred_language,
            has_password=user.password_hash is not None,
            google_linked=user.google_sub is not None,
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
        "two_factor": TwoFactorOut(**two_factor.__dict__),
    }


def _session_fields(
    request: Request, service: AuthService, issued: IssuedSession, csrf: str, now: datetime
) -> dict[str, Any]:
    return {
        **_me(
            request,
            service,
            user=issued.user,
            organization=issued.organization,
            role=issued.role,
            session_expires_at=issued.session.expires_at,
            memberships=issued.memberships,
        ),
        "access_token": issued.access_token,
        "expires_in": max(0, int((issued.access_expires_at - now).total_seconds())),
        "csrf_token": csrf,
    }


def _session_out(
    request: Request, service: AuthService, issued: IssuedSession, csrf: str, now: datetime
) -> SessionOut:
    return SessionOut(**_session_fields(request, service, issued, csrf, now))


def _challenge_out(pending: PendingSignIn, now: datetime) -> ChallengeOut:
    return ChallengeOut(
        status="two_factor_required"
        if pending.kind == "two_factor"
        else "two_factor_setup_required",
        challenge_token=pending.challenge_token,
        method="totp" if pending.method == "totp" else "email" if pending.method else None,
        email_hint=pending.email_hint,
        code_sent=pending.code_sent,
        expires_in=max(0, int((pending.expires_at - now).total_seconds())),
    )


def signed_in(
    request: Request, response: Response, service: AuthService, issued: IssuedSession
) -> SessionOut:
    """Set the session cookies and build the response for a finished sign-in."""
    now = request.app.state.clock()
    csrf_token = _csrf_value(request)
    _set_auth_cookies(response, request, issued, csrf_token, now)
    response.headers["Cache-Control"] = "no-store"
    return _session_out(request, service, issued, csrf_token, now)


def login_response(
    request: Request, response: Response, service: AuthService, outcome: LoginOutcome
) -> SessionOut | ChallengeOut:
    if isinstance(outcome, PendingSignIn):
        response.headers["Cache-Control"] = "no-store"
        return _challenge_out(outcome, request.app.state.clock())
    return signed_in(request, response, service, outcome)


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
    send_queued(request, background, service)
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
    send_queued(request, background, service)
    return Accepted(
        message="If this address has an account that is not confirmed yet, we sent a new link."
    )


@router.post(
    "/login",
    response_model=LoginOut,
    responses=_ERRORS,
    summary="Sign in with email and password (may ask for a second step)",
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SessionOut | ChallengeOut:
    outcome = service.login(
        email=str(body.email), password=body.password, organization_id=body.organization_id
    )
    send_queued(request, background, service)
    return login_response(request, response, service, outcome)


@router.post(
    "/login/verify",
    response_model=SessionOut,
    responses=_ERRORS,
    summary="Finish signing in with a 2FA code or a recovery code",
)
def login_verify(
    body: ChallengeCodeRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SessionOut:
    issued = service.verify_login_code(body.challenge_token, body.code)
    send_queued(request, background, service)
    return signed_in(request, response, service, issued)


@router.post(
    "/login/resend-code",
    response_model=ChallengeOut,
    responses=_ERRORS,
    summary="Email a new sign-in code",
)
def login_resend_code(
    body: ChallengeRequest, request: Request, background: BackgroundTasks, service: AuthServiceDep
) -> ChallengeOut:
    pending = service.resend_login_code(body.challenge_token)
    send_queued(request, background, service)
    return _challenge_out(pending, request.app.state.clock())


@router.post(
    "/login/setup",
    response_model=SetupOut,
    responses=_ERRORS,
    summary="Owners without 2FA: start setting it up during sign-in",
)
def login_setup(
    body: ChallengeSetupRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SetupOut:
    started = service.begin_login_setup(body.challenge_token, body.method)
    send_queued(request, background, service)
    response.headers["Cache-Control"] = "no-store"
    return setup_out(started)


@router.post(
    "/login/setup/resend-code",
    response_model=Accepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Email a new set-up code during sign-in",
)
def login_setup_resend(
    body: ChallengeRequest, request: Request, background: BackgroundTasks, service: AuthServiceDep
) -> Accepted:
    service.resend_login_setup_code(body.challenge_token)
    send_queued(request, background, service)
    return Accepted(message="We sent a new code.")


@router.post(
    "/login/setup/confirm",
    response_model=SetupDoneOut,
    responses=_ERRORS,
    summary="Confirm the new 2FA method, get recovery codes and finish signing in",
)
def login_setup_confirm(
    body: ChallengeCodeRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SetupDoneOut:
    issued, recovery = service.confirm_login_setup(body.challenge_token, body.code)
    send_queued(request, background, service)
    session = signed_in(request, response, service, issued)
    return SetupDoneOut(**session.model_dump(), recovery_codes=recovery)


def setup_out(started: SetupStarted) -> SetupOut:
    if started.totp is not None:
        return SetupOut(
            method="totp",
            secret=started.totp.secret,
            otpauth_uri=started.totp.uri,
            qr=started.totp.qr,
        )
    return SetupOut(method="email", email_hint=started.email_hint)


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
    return _session_out(request, service, issued, csrf_token, now)


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
    send_queued(request, background, service)
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
    send_queued(request, background, service)
    return Done(message="Your password is changed. Sign in with the new password.")


@router.get("/me", response_model=Me, responses=_ERRORS, summary="Who is signed in")
def me(request: Request, auth: CurrentAuth, service: AuthServiceDep) -> Me:
    return Me(
        **_me(
            request,
            service,
            user=auth.user,
            organization=auth.organization,
            role=auth.role,
            session_expires_at=auth.session.expires_at,
            memberships=auth.memberships,
        )
    )


@router.post(
    "/secure-account",
    response_model=Done,
    responses=_ERRORS,
    summary='"This wasn\'t me": sign out every device',
)
def secure_account(body: TokenRequest, service: AuthServiceDep) -> Done:
    service.secure_account(body.token)
    return Done(
        message="Every device is signed out. Choose a new password now to keep your account safe."
    )


def switched_out(request: Request, service: AuthService, switched: SwitchedSession) -> SwitchedOut:
    now = request.app.state.clock()
    return SwitchedOut(
        **_me(
            request,
            service,
            user=switched.user,
            organization=switched.organization,
            role=switched.role,
            session_expires_at=switched.session.expires_at,
            memberships=switched.memberships,
        ),
        access_token=switched.access_token,
        expires_in=max(0, int((switched.access_expires_at - now).total_seconds())),
    )


@router.post(
    "/switch-organization",
    response_model=SwitchedOut,
    responses={**_ERRORS, 404: {"description": "Not a member of that workspace"}},
    summary="Work in another workspace you belong to",
)
def switch_organization(
    body: SwitchOrganizationRequest,
    request: Request,
    response: Response,
    auth: CurrentAuth,
    service: AuthServiceDep,
) -> SwitchedOut:
    switched = service.switch_organization(auth, body.organization_id)
    response.headers["Cache-Control"] = "no-store"
    return switched_out(request, service, switched)
