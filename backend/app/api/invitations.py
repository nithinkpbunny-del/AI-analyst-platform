"""``/invitations``: what the invited person does with the emailed link (BUILD_SPEC R15). See
``app.orgs.invitations`` for the three ways to accept.

The link looks like ``/accept-invite#token=…`` (a URL fragment, never sent to a server or logged, D17). The web app
reads it and calls these routes. ``preview``, ``accept`` and ``accept-sign-in`` are public: the token is the
proof. ``accept-signed-in`` also needs the invited person's own session.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.api.auth import login_response, send_queued, switched_out
from app.auth.deps import AuthServiceDep, CurrentAuth
from app.orgs.invitations import InvitationAcceptance
from app.schemas.auth import ChallengeOut, LoginOut, SessionOut, SwitchedOut
from app.schemas.team import (
    AcceptNewAccountRequest,
    AcceptWithPasswordRequest,
    InvitationPreviewOut,
    InvitationTokenRequest,
)

router = APIRouter(prefix="/invitations", tags=["invitations"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "The link expired, was replaced or was already used"},
    401: {"description": "Wrong password, or not signed in"},
    403: {"description": "Signed in as someone else"},
    409: {"description": "An account already exists for this address"},
    422: {"description": "Validation error"},
    429: {"description": "Too many attempts"},
}


@router.post(
    "/preview",
    response_model=InvitationPreviewOut,
    responses=_ERRORS,
    summary="Who invited you, to which workspace, and whether you already have an account",
)
def preview(
    body: InvitationTokenRequest, response: Response, service: AuthServiceDep
) -> InvitationPreviewOut:
    found = InvitationAcceptance(service).preview(body.token)
    response.headers["Cache-Control"] = "no-store"
    return InvitationPreviewOut(**found.__dict__)


@router.post(
    "/accept",
    response_model=LoginOut,
    responses=_ERRORS,
    summary="New person: create the account, join the workspace and sign in",
)
def accept_new_account(
    body: AcceptNewAccountRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SessionOut | ChallengeOut:
    outcome = InvitationAcceptance(service).accept_new_account(
        body.token, name=body.name, password=body.password
    )
    send_queued(request, background, service)
    return login_response(request, response, service, outcome)


@router.post(
    "/accept-sign-in",
    response_model=LoginOut,
    responses=_ERRORS,
    summary="Existing account: sign in with your password and join the workspace",
)
def accept_with_password(
    body: AcceptWithPasswordRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SessionOut | ChallengeOut:
    outcome = InvitationAcceptance(service).accept_with_password(body.token, password=body.password)
    send_queued(request, background, service)
    return login_response(request, response, service, outcome)


@router.post(
    "/accept-signed-in",
    response_model=SwitchedOut,
    responses=_ERRORS,
    summary="Already signed in as the invited address: join and switch to the workspace",
)
def accept_signed_in(
    body: InvitationTokenRequest,
    request: Request,
    response: Response,
    auth: CurrentAuth,
    service: AuthServiceDep,
) -> SwitchedOut:
    switched = InvitationAcceptance(service).accept_signed_in(auth, body.token)
    response.headers["Cache-Control"] = "no-store"
    return switched_out(request, service, switched)
