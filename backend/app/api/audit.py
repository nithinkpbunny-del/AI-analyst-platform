"""``/audit``: the activity log of the current workspace (BUILD_SPEC 6.7) with filters and CSV export.

Owners see everyone's entries (``audit.view_all``), Analysts only their own (``audit.view_own``), Viewers get
403 (BUILD_SPEC 6.2). Exports are themselves logged.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.api.deps import Tenant, requires
from app.audit import writer as audit
from app.audit.query import AuditFilters, export_csv, list_entries
from app.auth.devices import describe_user_agent
from app.auth.permissions import Permission, allowed
from app.schemas.team import AuditEntryOut, AuditPage, AuditQuery

router = APIRouter(prefix="/audit", tags=["activity log"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Viewers cannot see the activity log"},
    422: {"description": "Validation error"},
}

ViewAudit = Annotated[Tenant, requires(Permission.AUDIT_VIEW_OWN)]


def _scope(tenant: Tenant) -> tuple[str, Any]:
    if allowed(tenant.auth, Permission.AUDIT_VIEW_ALL):
        return "workspace", None
    return "own", tenant.auth.user.id


def _filters(query: AuditQuery) -> AuditFilters:
    return AuditFilters(
        user_id=query.user_id,
        action=query.action,
        date_from=query.date_from,
        date_to=query.date_to,
    )


@router.get(
    "", response_model=AuditPage, responses=_ERRORS, summary="Activity log entries, newest first"
)
def entries(tenant: ViewAudit, query: Annotated[AuditQuery, Query()]) -> AuditPage:
    scope, only_user = _scope(tenant)
    rows, next_cursor = list_entries(
        tenant.db,
        org_id=tenant.org_id,
        timezone=tenant.auth.organization.timezone,
        filters=_filters(query),
        only_user_id=only_user,
        cursor=query.cursor,
        limit=query.limit,
    )
    return AuditPage(
        entries=[
            AuditEntryOut(
                id=row.entry.id,
                at=row.entry.at,
                action=row.entry.action,
                user_id=row.entry.user_id,
                user_name=row.user_name,
                user_email=row.user_email,
                target_type=row.entry.target_type,
                target_id=row.entry.target_id,
                ip=row.entry.ip,
                browser=describe_user_agent(row.entry.user_agent) if row.entry.user_agent else None,
                details=row.entry.details,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
        scope=scope,
    )


@router.get(
    "/export.csv",
    responses={**_ERRORS, 200: {"content": {"text/csv": {}}, "description": "CSV file"}},
    summary="Download the filtered activity log as CSV",
)
def export(tenant: ViewAudit, query: Annotated[AuditQuery, Query()]) -> Response:
    scope, only_user = _scope(tenant)
    filters = _filters(query)
    body, count, truncated = export_csv(
        tenant.db,
        org_id=tenant.org_id,
        timezone=tenant.auth.organization.timezone,
        filters=filters,
        only_user_id=only_user,
    )
    audit.write(
        tenant.db,
        action="audit.exported",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        details={
            "rows": count,
            "truncated": truncated,
            "scope": scope,
            "filters": {k: str(v) for k, v in filters.__dict__.items() if v is not None},
        },
    )
    tenant.db.commit()
    day = tenant.service.clock().date().isoformat()
    return Response(
        content="\ufeff" + body,  # BOM so Excel reads UTF-8 names correctly
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="activity-{day}.csv"',
            "Cache-Control": "no-store",
            "X-Rows-Truncated": "true" if truncated else "false",
        },
    )
