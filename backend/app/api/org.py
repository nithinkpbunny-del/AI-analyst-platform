"""``/org``: the current workspace's profile (name, time zone, industry). Changing it needs ``org.manage`` (Owners).

Privacy (BUILD_SPEC 6.6, M10), Owners only: ``GET /org/export.zip`` (the workspace's data), ``POST /org/deletion``
(delete it after a 7-day grace period) and ``DELETE /org/deletion`` (cancel that).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from sqlalchemy import select

from app import templates_industry
from app.api.auth import send_queued
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.email import templates
from app.models import Membership, Organization, User
from app.privacy import deletion
from app.privacy import export as data_export
from app.schemas.team import (
    DeletionRequest,
    IndustryTemplateOut,
    OrganizationSettingsOut,
    OrganizationSettingsRequest,
    TemplateKpiOut,
)

router = APIRouter(prefix="/org", tags=["workspace"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Only Owners can change workspace settings"},
    422: {"description": "Validation error"},
}


def _out(org: Organization) -> OrganizationSettingsOut:
    return OrganizationSettingsOut(
        id=org.id,
        name=org.name,
        timezone=org.timezone,
        currency=org.currency,
        plan=org.plan,
        industry=org.industry_template,
        deletion_requested_at=org.deletion_requested_at,
        deletion_due_at=deletion.due_at(org),
    )


def _org(tenant: Tenant, *, lock: bool = False) -> Organization:
    query = select(Organization).where(Organization.id == tenant.org_id)
    org = tenant.db.scalar(query.with_for_update() if lock else query)
    if org is None:  # pragma: no cover - the caller's own workspace always exists
        raise ApiError(404, "not_found", "Not found.")
    return org


@router.get(
    "",
    response_model=OrganizationSettingsOut,
    responses=_ERRORS,
    summary="This workspace's profile",
)
def get_org(tenant: Annotated[Tenant, requires(Permission.SHARED_VIEW)]) -> OrganizationSettingsOut:
    return _out(_org(tenant))


@router.get(
    "/templates",
    response_model=list[IndustryTemplateOut],
    responses=_ERRORS,
    summary="The industry templates a workspace can choose from",
)
def list_templates(
    tenant: Annotated[Tenant, requires(Permission.SHARED_VIEW)],
) -> list[IndustryTemplateOut]:
    """Reference data, the same for everyone (BUILD_SPEC X9). Needs a signed-in member; no workspace data is read."""
    return [
        IndustryTemplateOut(
            key=t.key,
            name=t.name,
            summary=t.summary,
            words=t.words,
            kpis=[TemplateKpiOut(**k.model_dump()) for k in t.kpis],
            starter_questions=list(t.starter_questions),
        )
        for t in templates_industry.all_templates()
    ]


@router.patch(
    "",
    response_model=OrganizationSettingsOut,
    responses=_ERRORS,
    summary="Rename the workspace or change its time zone",
)
def update_org(
    body: OrganizationSettingsRequest,
    tenant: Annotated[Tenant, requires(Permission.ORG_MANAGE)],
) -> OrganizationSettingsOut:
    org = _org(tenant, lock=True)
    changes: dict[str, dict[str, str | None]] = {}
    for field, column in (
        ("name", "name"),
        ("timezone", "timezone"),
        ("industry", "industry_template"),
    ):
        value = getattr(body, field)
        if value is not None and value != getattr(org, column):
            changes[field] = {"from": getattr(org, column), "to": value}
            setattr(org, column, value)
    if changes:
        audit.write(
            tenant.db,
            action="org.settings_changed",
            at=tenant.service.clock(),
            meta=tenant.service.meta,
            org_id=tenant.org_id,
            user_id=tenant.auth.user.id,
            target_type="organization",
            target_id=org.id,
            details=changes,
        )
    tenant.db.commit()
    return _out(org)


# ------------------------------------------------------------------------------------------------- privacy
Manage = Annotated[Tenant, requires(Permission.ORG_MANAGE)]
EXPORTS_PER_HOUR = 5


def _audit(tenant: Tenant, action: str, details: dict[str, Any] | None = None) -> None:
    audit.write(
        tenant.db,
        action=action,
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="organization",
        target_id=tenant.org_id,
        details=details or {},
    )


def _owners(tenant: Tenant) -> list[User]:
    return list(
        tenant.db.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == tenant.org_id,
                Membership.role == "owner",
                Membership.status == "active",
            )
        )
    )


@router.get(
    "/export.zip",
    responses={**_ERRORS, 429: {"description": "Too many exports in the last hour"}},
    response_class=Response,
    summary="The workspace's data as a ZIP (DPDP right to access)",
)
def export_data(request: Request, tenant: Manage) -> Response:
    now = tenant.service.clock()
    limiter = FixedWindowLimiter(
        request.app.state.redis,
        prefix="org-export",
        limit=EXPORTS_PER_HOUR,
        window=timedelta(hours=1),
    )
    if not limiter.hit(str(tenant.org_id), now):
        raise ApiError(
            429,
            "too_many_exports",
            "You exported this workspace several times this hour. Try later.",
        )
    data = data_export.build(tenant.db, tenant.org_id, now)
    _audit(tenant, "org.data_exported", {"bytes": len(data)})
    tenant.db.commit()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="workspace-data-{now:%Y-%m-%d}.zip"'
        },
    )


@router.post(
    "/deletion",
    response_model=OrganizationSettingsOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**_ERRORS, 409: {"description": "Deletion already requested"}},
    summary="Delete the workspace after a 7-day grace period",
)
def request_deletion(
    body: DeletionRequest, request: Request, background: BackgroundTasks, tenant: Manage
) -> OrganizationSettingsOut:
    org = _org(tenant, lock=True)
    if " ".join(body.confirm_name.split()).casefold() != org.name.casefold():
        raise ApiError(
            422,
            "validation_error",
            "Type the workspace's name exactly to confirm.",
            fields={"confirm_name": "Type the workspace's name exactly to confirm."},
        )
    if org.deletion_requested_at is not None:
        raise ApiError(409, "deletion_requested", "Deletion was already requested.")
    org.deletion_requested_at = tenant.service.clock()
    due = deletion.due_at(org)
    assert due is not None
    _audit(tenant, "org.deletion_requested", {"due_at": due.isoformat()})
    when = f"{due.astimezone(_zone(org.timezone)):%d %b %Y}"
    requester = tenant.auth.user.name or tenant.auth.user.email
    link = f"{tenant.service.settings.app_base_url}/settings"
    for owner in _owners(tenant):
        tenant.service.outbox.append(
            templates.render(
                "deletion_requested",
                to=owner.email,
                org_name=org.name,
                when=when,
                requester=requester,
                link=link,
            )
        )
    tenant.db.commit()
    send_queued(request, background, tenant.service)
    return _out(org)


@router.delete(
    "/deletion",
    response_model=OrganizationSettingsOut,
    responses={**_ERRORS, 409: {"description": "No deletion was requested"}},
    summary="Cancel a requested deletion",
)
def cancel_deletion(
    request: Request, background: BackgroundTasks, tenant: Manage
) -> OrganizationSettingsOut:
    org = _org(tenant, lock=True)
    if org.deletion_requested_at is None:
        raise ApiError(409, "no_deletion", "This workspace is not being deleted.")
    org.deletion_requested_at = None
    _audit(tenant, "org.deletion_cancelled")
    requester = tenant.auth.user.name or tenant.auth.user.email
    for owner in _owners(tenant):
        tenant.service.outbox.append(
            templates.render(
                "deletion_cancelled", to=owner.email, org_name=org.name, requester=requester
            )
        )
    tenant.db.commit()
    send_queued(request, background, tenant.service)
    return _out(org)


def _zone(name: str) -> Any:
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)
