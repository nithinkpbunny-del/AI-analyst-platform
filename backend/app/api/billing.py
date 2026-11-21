"""``/org/billing``: plans, usage against the limit, the plan change flow and invoice-ready summaries (M18).

**Hooks only: there is no payment, no price and no gateway** (DECISIONS D117-D121). Owners only (``org.manage``),
like the AI plan and usage (BUILD_SPEC 6.2). A plan change is previewed, then confirmed; both use M5's rules.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.api.ai import change_plan
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.billing import limits, plans, summary
from app.billing import provider as billing_provider
from app.billing import service as billing_service
from app.config import Settings
from app.llm.usage import current_month, month_of
from app.models import BillingEvent, Organization
from app.schemas.billing import (
    BillingEventOut,
    BillingOut,
    LimitOut,
    PlanChangeOut,
    PlanChangeRequest,
    PlanOut,
    ProviderOut,
    SummaryLineOut,
    SummaryOut,
)

router = APIRouter(prefix="/org/billing", tags=["billing"])
ManageOrg = Annotated[Tenant, requires(Permission.ORG_MANAGE)]
RECENT_EVENTS = 10
MONTH = Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Only Owners can see and change billing"},
    409: {"description": "This plan change is not possible (see the blockers in the preview)"},
}

_BLOCKER_MESSAGES = {
    "same_plan": "The workspace is already on this plan.",
    "key_missing": "Add your own AI key first, then switch to it.",
    "managed_unavailable": "Managed AI is not set up on this server yet. Add your own AI key instead.",
    "unknown_plan": "That plan does not exist.",
}


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _org(tenant: Tenant, *, lock: bool = False) -> Organization:
    query = select(Organization).where(Organization.id == tenant.org_id)
    org = tenant.db.scalar(query.with_for_update() if lock else query)
    if org is None:  # pragma: no cover - the caller's own workspace always exists
        raise ApiError(404, "not_found", "Not found.")
    return org


def limit_out(current: limits.LimitStatus) -> LimitOut:
    return LimitOut(
        plan=current.plan,
        month=current.month.label,
        used=current.used,
        limit=current.limit,
        percent=current.percent,
        level=current.level,
        resets_on=current.resets_on,
    )


@router.get(
    "",
    response_model=BillingOut,
    responses=_ERRORS,
    summary="Plans, usage against the limit, and what billing has recorded (nothing is charged)",
)
def get_billing(request: Request, tenant: ManageOrg) -> BillingOut:
    org = _org(tenant)
    provider = billing_provider.get_provider(_settings(request))
    events = tenant.db.scalars(
        select(BillingEvent)
        .where(BillingEvent.org_id == org.id)
        .order_by(BillingEvent.created_at.desc())
        .limit(RECENT_EVENTS)
    )
    return BillingOut(
        plan=org.plan,
        plans=[PlanOut(**vars(p)) for p in plans.catalog(org)],
        limit=limit_out(limits.status(tenant.db, org, tenant.service.clock())),
        provider=ProviderOut(
            name=provider.name,
            live=provider.live,
            note="Billing is not switched on: no payment is taken and no price is set yet.",
        ),
        events=[
            BillingEventOut(
                id=str(e.id),
                kind=e.kind,
                period=e.period,
                created_at=e.created_at,
                provider=e.provider,
                delivery=e.delivery,
                details={k: v for k, v in e.details.items() if k != "lines"},
            )
            for e in events
        ],
    )


@router.post(
    "/plan-change/preview",
    response_model=PlanChangeOut,
    responses=_ERRORS,
    summary="What switching plan would do. Changes nothing.",
)
def preview_plan_change(
    body: PlanChangeRequest, request: Request, tenant: ManageOrg
) -> PlanChangeOut:
    org = _org(tenant)
    result = billing_service.preview(
        tenant.db, org, body.plan, settings=_settings(request), now=tenant.service.clock()
    )
    return PlanChangeOut(
        current=result.current,
        target=result.target,
        allowed=result.allowed,
        blockers=result.blockers,
        effects=result.effects,
    )


@router.put(
    "/plan",
    response_model=BillingOut,
    responses=_ERRORS,
    summary="Confirm a plan change (the same rules as the preview). Charges nothing.",
)
def confirm_plan_change(body: PlanChangeRequest, request: Request, tenant: ManageOrg) -> BillingOut:
    org = _org(tenant, lock=True)
    result = billing_service.preview(
        tenant.db, org, body.plan, settings=_settings(request), now=tenant.service.clock()
    )
    if not result.allowed:
        blocker = result.blockers[0]
        raise ApiError(409, blocker, _BLOCKER_MESSAGES.get(blocker, "This change is not possible."))
    change_plan(tenant, org, body.plan, settings=_settings(request), via="billing")
    tenant.db.commit()
    return get_billing(request, tenant)


def _month(org: Organization, tenant: Tenant, value: str | None) -> Any:
    now_month = current_month(tenant.service.clock(), org.timezone)
    if value is None:
        return now_month
    year, number = (int(part) for part in value.split("-"))
    chosen = month_of(date(year, number, 1), org.timezone)
    if chosen.first_day > now_month.first_day:
        raise ApiError(422, "future_month", "That month has not started yet.")
    return chosen


@router.get(
    "/summary",
    response_model=SummaryOut,
    responses=_ERRORS,
    summary="Invoice-ready usage summary for a month (default: this month). Not an invoice.",
)
def get_summary(tenant: ManageOrg, month: MONTH = None) -> SummaryOut:
    org = _org(tenant)
    chosen = _month(org, tenant, month)
    body, frozen_at = summary.for_month(tenant.db, org, chosen)
    return SummaryOut(
        **{**body, "lines": [SummaryLineOut(**line) for line in body["lines"]]},
        frozen_at=frozen_at,
        in_progress=tenant.service.clock() < chosen.end,
    )


@router.get(
    "/summary.csv",
    responses={**_ERRORS, 200: {"content": {"text/csv": {}}}},
    summary="The same summary as a CSV file",
)
def get_summary_csv(tenant: ManageOrg, month: MONTH = None) -> Response:
    org = _org(tenant)
    chosen = _month(org, tenant, month)
    body, _ = summary.for_month(tenant.db, org, chosen)
    audit.write(
        tenant.db,
        action="billing.summary_exported",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="organization",
        target_id=org.id,
        details={"month": chosen.label},
    )
    tenant.db.commit()
    return Response(
        summary.to_csv(body),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="usage-{chosen.label}.csv"'},
    )
