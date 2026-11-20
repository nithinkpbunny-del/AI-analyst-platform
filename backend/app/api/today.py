"""``/today``: the home screen (BUILD_SPEC 11a.8 "Engagement"; M10).

Everything here is read from what already exists, so opening Today costs no AI call and reads no data source:
the latest briefing (its top items and KPIs), three suggested questions written by code from the person's own data
(the briefing's findings and the dictionary they may see), the onboarding checklist (Company details, Connect data,
Confirm dictionary, Ask first question), my latest long tasks and what others shared with me, and goal progress
(M15: as the Goals screen last computed it for me, else as my latest briefing did).
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from app import templates_industry
from app.agent.access import NoAccess, access_context, visible_dictionary
from app.api.deps import Tenant, requires
from app.api.reports import _report_out, list_shared
from app.api.tasks import task_out
from app.auth.permissions import Permission
from app.billing import limits as billing_limits
from app.dictionary import service as dictionary_service
from app.goals import service as goal_service
from app.models import Connection, DictionaryVersion, Message, Report, Task
from app.schemas.reports import ChecklistOut, TodayOut

router = APIRouter(tags=["today"])

Mine = Annotated[Tenant, requires(Permission.SHARED_VIEW)]

HEALTH_CHECK = (
    "Give me a health check of the business for the last complete month: the main numbers, what changed and why, "
    "and what is working well."
)
_DIMENSION_WORDS = (
    "region",
    "category",
    "product_type",
    "channel",
    "city",
    "state",
    "outlet",
    "store",
)

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Owners must turn on two-step verification first"},
}


def _label(name: str) -> str:
    return {"aov": "average order value"}.get(name, name.replace("_", " "))


def suggestions(tenant: Tenant, briefing: Report | None) -> list[str]:
    """Three questions from the person's own data: the latest briefing's findings first, then the dictionary."""
    out: list[str] = []
    body = dict(briefing.body) if briefing else {}
    for item in body.get("growth") or []:
        out.append(f"Why is {item.get('segment')} growing, and can we repeat it elsewhere?")
        break
    for item in body.get("unusual") or []:
        where = "" if item.get("series") in (None, "", "all", "Total") else f" in {item['series']}"
        out.append(
            f"What happened to {_label(str(item.get('metric')))}{where} on {item.get('bucket')}?"
        )
        break
    metrics: list[str] = []
    dimensions: list[str] = []
    with contextlib.suppress(NoAccess):
        access = access_context(tenant.db, tenant.org_id, tenant.auth.user.id)
        active = dictionary_service.active(tenant.db, tenant.org_id)
        if active.dictionary is not None:
            visible = visible_dictionary(active.dictionary, access).dictionary
            metrics = [m for m in ("revenue", "orders") if m in visible.metrics] or list(
                visible.metrics
            )[:2]
            for entry in visible.datasets.values():
                for name, column in entry.columns.items():
                    if column.role == "dimension" and any(
                        w in name.lower() for w in _DIMENSION_WORDS
                    ):
                        dimensions.append(column.label or name)
    if metrics:
        first = _label(metrics[0])
        out.append(f"How did {first} change last month, and why?")
        if dimensions:
            out.append(f"Which {dimensions[0].lower()} grew {first} the most last quarter?")
        if metrics[0] == "revenue":
            out.append("What if we raise prices by 5%?")
    else:
        out.append("What can you tell me about my data?")
    # M17: a business of a known kind gets its template's questions to fill what its own data did not suggest.
    template = templates_industry.get(tenant.auth.organization.industry_template)
    if not template.generic:
        out.extend(template.starter_questions)
    return list(dict.fromkeys(out))[:3]


def checklist(tenant: Tenant) -> ChecklistOut:
    org = tenant.auth.organization
    company = org.industry_template is not None
    connect = (
        tenant.db.scalar(select(Connection.id).where(Connection.org_id == tenant.org_id).limit(1))
        is not None
    )
    dictionary = (
        tenant.db.scalar(
            select(DictionaryVersion.id)
            .where(
                DictionaryVersion.org_id == tenant.org_id,
                DictionaryVersion.status.in_(("confirmed", "superseded")),
            )
            .limit(1)
        )
        is not None
    )
    ask = (
        tenant.db.scalar(
            select(Message.id)
            .where(
                Message.org_id == tenant.org_id,
                Message.user_id == tenant.auth.user.id,
                Message.role == "user",
            )
            .limit(1)
        )
        is not None
    )
    return ChecklistOut(
        company=company,
        connect=connect,
        dictionary=dictionary,
        ask=ask,
        done=company and connect and dictionary and ask,
    )


GOAL_FIELDS = (
    "status",
    "actual",
    "target",
    "projection",
    "projected_pct_of_target",
    "unit",
    "direction",
    "period_start",
    "period_end",
    "as_of",
)
MAX_GOALS = 4
_GOAL_ORDER = {"behind": 0, "at_risk": 1, "on_track": 2, "achieved": 3, "missed": 4}


def goal_items(request: Request, tenant: Tenant, briefing: Report | None) -> list[dict[str, Any]]:
    """Running goals the person may see, off-track first; numbers as last computed for them (no source read)."""
    seen = None
    with contextlib.suppress(NoAccess):
        access = access_context(tenant.db, tenant.org_id, tenant.auth.user.id)
        active = dictionary_service.active(tenant.db, tenant.org_id)
        if active.dictionary is not None:
            seen = visible_dictionary(active.dictionary, access)
    if seen is None:
        return []
    today = tenant.service.clock().date()
    goals = [
        g
        for g in goal_service.all_goals(tenant.db, tenant.org_id)
        if goal_service.visible(seen.dictionary, g) and g.period_start <= today <= g.period_end
    ]
    if not goals:
        return []
    payload = goal_service.read(
        request.app.state.connector_runtime, tenant.org_id, tenant.auth.user.id
    )
    rows: dict[str, Any] = dict((payload or {}).get("goals") or {})
    if not rows and briefing is not None:
        rows = {str(g.get("goal_id")): g for g in dict(briefing.body).get("goals") or []}
    out = []
    for goal in goals:
        row = rows.get(str(goal.id)) or {}
        out.append(
            {
                "id": str(goal.id),
                "label": goal.name or f"{goal_service.metric_label(goal.metric_key)} goal",
                **{k: row.get(k) for k in GOAL_FIELDS},
                "target": goal.target_value,
                "period_start": goal.period_start.isoformat(),
                "period_end": goal.period_end.isoformat(),
                "direction": goal.direction,
            }
        )
    out.sort(key=lambda g: _GOAL_ORDER.get(str(g.get("status")), 9))
    return out[:MAX_GOALS]


def usage_warning(tenant: Tenant) -> dict[str, Any] | None:
    """Owners see when the Managed plan's monthly questions are at 80% or used up (M18, D119); nobody else does."""
    if tenant.auth.role != "owner":
        return None
    current = billing_limits.status(tenant.db, tenant.auth.organization, tenant.service.clock())
    if current.level not in ("warning", "reached"):
        return None
    return {
        "level": current.level,
        "used": current.used,
        "limit": current.limit,
        "percent": current.percent,
        "resets_on": current.resets_on.isoformat(),
    }


@router.get("/today", response_model=TodayOut, responses=_ERRORS, summary="The home screen")
def today(request: Request, tenant: Mine) -> TodayOut:
    me = tenant.auth.user.id
    briefing = tenant.db.scalar(
        select(Report)
        .where(Report.org_id == tenant.org_id, Report.user_id == me, Report.source == "briefing")
        .order_by(Report.created_at.desc())
        .limit(1)
    )
    tasks = list(
        tenant.db.scalars(
            select(Task)
            .where(Task.org_id == tenant.org_id, Task.user_id == me)
            .order_by(Task.created_at.desc())
            .limit(3)
        )
    )
    return TodayOut(
        name=tenant.auth.user.name,
        briefing=_report_out(briefing, me) if briefing else None,
        suggestions=suggestions(tenant, briefing),
        health_check=HEALTH_CHECK,
        checklist=checklist(tenant),
        tasks=[task_out(t) for t in tasks],
        shared=list_shared(tenant).items[:3],
        goals=goal_items(request, tenant, briefing),
        usage_warning=usage_warning(tenant),
    )
