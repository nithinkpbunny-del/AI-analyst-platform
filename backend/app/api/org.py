"""``/org``: the current workspace's profile (name, time zone). Changing it needs ``org.manage`` (Owners).

The AI plan, BYOK key and usage arrive in M5, impact thresholds and privacy actions in later milestones.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.models import Organization
from app.schemas.team import OrganizationSettingsOut, OrganizationSettingsRequest

router = APIRouter(prefix="/org", tags=["workspace"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Only Owners can change workspace settings"},
    422: {"description": "Validation error"},
}


def _out(org: Organization) -> OrganizationSettingsOut:
    return OrganizationSettingsOut(
        id=org.id, name=org.name, timezone=org.timezone, currency=org.currency, plan=org.plan
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
    changes: dict[str, dict[str, str]] = {}
    for field in ("name", "timezone"):
        value = getattr(body, field)
        if value is not None and value != getattr(org, field):
            changes[field] = {"from": getattr(org, field), "to": value}
            setattr(org, field, value)
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
