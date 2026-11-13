"""Briefings (BUILD_SPEC R10, 13, 11 "Briefings"; M10): my settings, my past briefings, and "send me one now".

Everyone has their own briefing (``shared.view``): on by default for Owners at 06:00 in the workspace's time zone,
opt-in for Analysts and Viewers. Changing the settings is recorded in the activity log (BUILD_SPEC 6.7).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.chat import _jobs
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.briefings import schedule
from app.briefings import service as briefings
from app.db.ids import uuid7
from app.models import BriefingRun, BriefingSetting, Report
from app.schemas.reports import (
    BriefingRunList,
    BriefingRunOut,
    BriefingSettingsOut,
    BriefingSettingsRequest,
)

router = APIRouter(prefix="/briefings", tags=["briefings"])

Mine = Annotated[Tenant, requires(Permission.SHARED_VIEW)]
SEND_NOW_PER_HOUR = 3
LIST_LIMIT = 60

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Owners must turn on two-step verification first"},
    409: {"description": "The workspace is being deleted"},
    422: {"description": "Validation error"},
    429: {"description": "Too many briefings asked for this hour"},
}


def _out(tenant: Tenant, effective: schedule.Effective) -> BriefingSettingsOut:
    return BriefingSettingsOut(
        enabled=effective.enabled,
        local_time=effective.local_time.strftime("%H:%M"),
        frequency=effective.frequency,
        weekday=effective.weekday,
        language=effective.language,
        timezone=effective.timezone,
        default=effective.default,
        next_at=schedule.next_slot(tenant.service.clock(), effective),
    )


@router.get(
    "/settings",
    response_model=BriefingSettingsOut,
    responses=_ERRORS,
    summary="My briefing settings",
)
def get_settings(tenant: Mine) -> BriefingSettingsOut:
    return _out(tenant, briefings.settings_for(tenant.db, tenant.org_id, tenant.auth.user.id))


@router.put(
    "/settings", response_model=BriefingSettingsOut, responses=_ERRORS, summary="Change my briefing"
)
def update_settings(body: BriefingSettingsRequest, tenant: Mine) -> BriefingSettingsOut:
    current = briefings.settings_for(tenant.db, tenant.org_id, tenant.auth.user.id)
    row = tenant.db.scalar(
        select(BriefingSetting)
        .where(
            BriefingSetting.org_id == tenant.org_id, BriefingSetting.user_id == tenant.auth.user.id
        )
        .with_for_update()
    )
    if row is None:
        row = BriefingSetting(
            org_id=tenant.org_id,
            user_id=tenant.auth.user.id,
            enabled=current.enabled,
            local_time=current.local_time,
            frequency=current.frequency,
            weekday=current.weekday,
            language=current.language,
            timezone=None,
        )
        tenant.db.add(row)
    changes: dict[str, Any] = {}
    for field in ("enabled", "local_time", "frequency", "weekday", "language", "timezone"):
        value = getattr(body, field)
        if value is not None and value != getattr(row, field):
            changes[field] = str(value)
            setattr(row, field, value)
    if body.enabled and row.frequency == "off":
        row.frequency = "daily"
        changes["frequency"] = "daily"
    if changes:
        audit.write(
            tenant.db,
            action="briefing.settings_changed",
            at=tenant.service.clock(),
            meta=tenant.service.meta,
            org_id=tenant.org_id,
            user_id=tenant.auth.user.id,
            target_type="briefing_settings",
            target_id=row.id,
            details=changes,
        )
    tenant.db.commit()
    return _out(tenant, briefings.settings_for(tenant.db, tenant.org_id, tenant.auth.user.id))


def _run_out(run: BriefingRun, headline: str | None) -> BriefingRunOut:
    return BriefingRunOut(
        id=run.id,
        scheduled_for=run.scheduled_for,
        kind=run.kind,
        status=run.status,
        sent_at=run.sent_at,
        report_id=run.report_id,
        headline=headline,
        error_code=run.error_code,
    )


@router.get(
    "", response_model=BriefingRunList, responses=_ERRORS, summary="My past briefings, newest first"
)
def list_runs(tenant: Mine) -> BriefingRunList:
    runs = list(
        tenant.db.scalars(
            select(BriefingRun)
            .where(BriefingRun.org_id == tenant.org_id, BriefingRun.user_id == tenant.auth.user.id)
            .order_by(BriefingRun.scheduled_for.desc())
            .limit(LIST_LIMIT)
        )
    )
    report_ids = [r.report_id for r in runs if r.report_id]
    headlines = {
        r.id: str(dict(r.body).get("headline") or "")
        for r in tenant.db.scalars(select(Report).where(Report.id.in_(report_ids)))
    }
    return BriefingRunList(runs=[_run_out(r, headlines.get(r.report_id)) for r in runs])  # type: ignore[arg-type]


@router.post(
    "/send-now",
    response_model=BriefingRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Make and email my briefing now (uses one small AI call)",
)
def send_now(request: Request, tenant: Mine) -> BriefingRunOut:
    if tenant.auth.organization.deletion_requested_at is not None:
        raise ApiError(409, "workspace_deleting", "This workspace is being deleted.")
    now = tenant.service.clock()
    limiter = FixedWindowLimiter(
        request.app.state.redis,
        prefix="briefing-now",
        limit=SEND_NOW_PER_HOUR,
        window=timedelta(hours=1),
    )
    if not limiter.hit(str(tenant.auth.user.id), now):
        raise ApiError(
            429, "too_many_briefings", "You asked for several briefings this hour. Try again later."
        )
    run = BriefingRun(
        id=uuid7(),
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        scheduled_for=now,
        kind="test",
        status="queued",
        findings=[],
    )
    tenant.db.add(run)
    tenant.db.commit()
    _jobs(request).send_briefing(org_id=tenant.org_id, run_id=run.id)
    tenant.db.expire_all()
    found = tenant.db.get(BriefingRun, run.id)
    assert found is not None
    report = tenant.db.get(Report, found.report_id) if found.report_id else None
    return _run_out(found, str(dict(report.body).get("headline") or "") if report else None)
