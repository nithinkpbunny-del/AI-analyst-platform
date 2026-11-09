"""``/team``: members, roles and invitations of the current workspace (Owners; BUILD_SPEC 6.2, R15).

Every route needs ``members.manage`` (403 otherwise) and runs tenant-scoped, so a member or invitation of another
workspace answers 404, exactly like one that does not exist.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.api.auth import send_queued
from app.api.deps import Tenant, requires
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.orgs.team import INVITES_PER_HOUR, InvitationRow, MemberRow, TeamService
from app.schemas.team import (
    InvitationList,
    InvitationOut,
    InviteRequest,
    MemberList,
    MemberOut,
    RoleChangeRequest,
)

router = APIRouter(prefix="/team", tags=["team"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Only Owners can manage the team"},
    404: {"description": "Not found (or in another workspace)"},
    409: {"description": "Not allowed in this state (e.g. the last Owner)"},
    422: {"description": "Validation error"},
}

ManageTeam = Annotated[Tenant, requires(Permission.MEMBERS_MANAGE)]


def _service(request: Request, tenant: Tenant) -> TeamService:
    return TeamService(
        tenant=tenant,
        invite_limiter=FixedWindowLimiter(
            request.app.state.redis,
            prefix="invites",
            limit=INVITES_PER_HOUR,
            window=timedelta(hours=1),
        ),
    )


def _member_out(row: MemberRow, tenant: Tenant) -> MemberOut:
    return MemberOut(
        id=row.membership.id,
        user_id=row.user.id,
        name=row.user.name,
        email=row.user.email,
        role=row.membership.role,
        two_factor_enabled=row.two_factor_enabled,
        joined_at=row.membership.created_at,
        is_you=row.user.id == tenant.auth.user.id,
    )


def _invitation_out(row: InvitationRow, tenant: Tenant) -> InvitationOut:
    now = tenant.service.clock()
    return InvitationOut(
        id=row.invitation.id,
        email=row.invitation.email,
        role=row.invitation.role,
        invited_by_name=row.invited_by_name,
        created_at=row.invitation.created_at,
        expires_at=row.invitation.expires_at,
        expired=row.invitation.expires_at <= now,
    )


@router.get(
    "/members", response_model=MemberList, responses=_ERRORS, summary="People in this workspace"
)
def members(request: Request, tenant: ManageTeam) -> MemberList:
    rows = _service(request, tenant).members()
    return MemberList(members=[_member_out(row, tenant) for row in rows])


@router.patch(
    "/members/{membership_id}",
    response_model=MemberOut,
    responses=_ERRORS,
    summary="Change someone's role",
)
def change_role(
    membership_id: uuid.UUID,
    body: RoleChangeRequest,
    request: Request,
    background: BackgroundTasks,
    tenant: ManageTeam,
) -> MemberOut:
    row = _service(request, tenant).change_role(membership_id, body.role)
    send_queued(request, background, tenant.service)
    return _member_out(row, tenant)


@router.delete(
    "/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Remove someone from this workspace (signs them out of it at once)",
)
def remove_member(
    membership_id: uuid.UUID, request: Request, background: BackgroundTasks, tenant: ManageTeam
) -> Response:
    _service(request, tenant).remove(membership_id)
    send_queued(request, background, tenant.service)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/invitations",
    response_model=InvitationList,
    responses=_ERRORS,
    summary="Invitations not accepted yet",
)
def invitations(request: Request, tenant: ManageTeam) -> InvitationList:
    rows = _service(request, tenant).invitations()
    return InvitationList(invitations=[_invitation_out(row, tenant) for row in rows])


@router.post(
    "/invitations",
    response_model=InvitationOut,
    status_code=status.HTTP_201_CREATED,
    responses={**_ERRORS, 429: {"description": "Too many invitations this hour"}},
    summary="Invite someone by email as an Analyst or a Viewer",
)
def invite(
    body: InviteRequest, request: Request, background: BackgroundTasks, tenant: ManageTeam
) -> InvitationOut:
    row = _service(request, tenant).invite(str(body.email), body.role)
    send_queued(request, background, tenant.service)
    return _invitation_out(row, tenant)


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=InvitationOut,
    responses={**_ERRORS, 429: {"description": "Too many invitations this hour"}},
    summary="Send the invitation again with a new link (the old link stops working)",
)
def resend_invitation(
    invitation_id: uuid.UUID, request: Request, background: BackgroundTasks, tenant: ManageTeam
) -> InvitationOut:
    row = _service(request, tenant).resend(invitation_id)
    send_queued(request, background, tenant.service)
    return _invitation_out(row, tenant)


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Cancel an invitation",
)
def revoke_invitation(invitation_id: uuid.UUID, request: Request, tenant: ManageTeam) -> Response:
    _service(request, tenant).revoke(invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
