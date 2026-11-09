"""Signed-in security settings (BUILD_SPEC 6.1, R15): two-step verification and your own sessions.

``/auth/2fa/*``: set up or change the method, confirm it, new recovery codes, turn off (not for Owners).
Changes to an existing setup need a code from the current method ("step-up"); for the email method, ask for one
with ``/auth/2fa/send-code``.

``/auth/sessions``: the browsers you are signed in on; sign out one of them or all the others. Sessions belong to
a person, so another person's session id answers 404, exactly like one that does not exist.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.api.auth import send_queued, setup_out
from app.auth.deps import AuthServiceDep, CurrentAuth
from app.auth.devices import describe_user_agent
from app.schemas.auth import (
    Accepted,
    CodeRequest,
    Done,
    OptionalCodeRequest,
    RecoveryCodesOut,
    Revoked,
    SessionItem,
    SessionList,
    SetupOut,
    SetupRequest,
)

router = APIRouter(prefix="/auth", tags=["security settings"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Wrong or missing code"},
    401: {"description": "Not signed in"},
    403: {"description": "Not allowed (e.g. Owners cannot turn 2FA off)"},
    422: {"description": "Validation error"},
    429: {"description": "Too many attempts"},
}


@router.post(
    "/2fa/setup", response_model=SetupOut, responses=_ERRORS, summary="Start setting up 2FA"
)
def setup(
    body: SetupRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    auth: CurrentAuth,
    service: AuthServiceDep,
) -> SetupOut:
    started = service.begin_two_factor_setup(auth, body.method, body.code)
    send_queued(request, background, service)
    response.headers["Cache-Control"] = "no-store"
    return setup_out(started)


@router.post(
    "/2fa/setup/resend-code",
    response_model=Accepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Email a new set-up code",
)
def setup_resend(
    request: Request, background: BackgroundTasks, auth: CurrentAuth, service: AuthServiceDep
) -> Accepted:
    service.resend_two_factor_setup_code(auth)
    send_queued(request, background, service)
    return Accepted(message="We sent a new code.")


@router.post(
    "/2fa/setup/confirm",
    response_model=RecoveryCodesOut,
    responses=_ERRORS,
    summary="Confirm the new method and get recovery codes",
)
def setup_confirm(
    body: CodeRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    auth: CurrentAuth,
    service: AuthServiceDep,
) -> RecoveryCodesOut:
    codes = service.confirm_two_factor_setup(auth, body.code)
    send_queued(request, background, service)
    response.headers["Cache-Control"] = "no-store"
    return RecoveryCodesOut(recovery_codes=codes)


@router.post(
    "/2fa/send-code",
    response_model=Accepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Email a code to confirm a security change",
)
def send_code(
    request: Request, background: BackgroundTasks, auth: CurrentAuth, service: AuthServiceDep
) -> Accepted:
    service.send_step_up_code(auth)
    send_queued(request, background, service)
    return Accepted(message="We sent a code to your email.")


@router.post(
    "/2fa/recovery-codes",
    response_model=RecoveryCodesOut,
    responses=_ERRORS,
    summary="Replace the recovery codes",
)
def recovery_codes(
    body: OptionalCodeRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    auth: CurrentAuth,
    service: AuthServiceDep,
) -> RecoveryCodesOut:
    codes = service.regenerate_recovery_codes(auth, body.code)
    send_queued(request, background, service)
    response.headers["Cache-Control"] = "no-store"
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/2fa/disable", response_model=Done, responses=_ERRORS, summary="Turn 2FA off")
def disable(
    body: OptionalCodeRequest,
    request: Request,
    background: BackgroundTasks,
    auth: CurrentAuth,
    service: AuthServiceDep,
) -> Done:
    service.disable_two_factor(auth, body.code)
    send_queued(request, background, service)
    return Done(message="Two-step verification is off.")


@router.get(
    "/sessions", response_model=SessionList, responses=_ERRORS, summary="Your signed-in devices"
)
def sessions(auth: CurrentAuth, service: AuthServiceDep) -> SessionList:
    return SessionList(
        sessions=[
            SessionItem(
                id=s.id,
                current=s.id == auth.session.id,
                browser=describe_user_agent(s.user_agent),
                ip=s.ip,
                created_at=s.created_at,
                last_seen_at=s.last_seen_at,
            )
            for s in service.list_sessions(auth)
        ]
    )


@router.post(
    "/sessions/revoke-others",
    response_model=Revoked,
    responses=_ERRORS,
    summary="Sign out every other device",
)
def revoke_others(auth: CurrentAuth, service: AuthServiceDep) -> Revoked:
    return Revoked(sessions_revoked=service.revoke_other_sessions(auth))


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_ERRORS, 404: {"description": "No such session (or not yours)"}},
    summary="Sign out one device",
)
def revoke(session_id: uuid.UUID, auth: CurrentAuth, service: AuthServiceDep) -> Response:
    service.revoke_own_session(auth, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
