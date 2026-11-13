"""``/today``: the home screen (BUILD_SPEC 11a.8 "Engagement"; M10).

Everything here is read from what already exists, so opening Today costs no AI call and reads no data source:
the latest briefing (its top items and KPIs), three suggested questions written by code from the person's own data
(the briefing's findings and the dictionary they may see), the onboarding checklist (Company details, Connect data,
Confirm dictionary, Ask first question), my latest long tasks and what others shared with me.
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

from fastapi import APIRouter
from sqlalchemy import select

from app.agent.access import NoAccess, access_context, visible_dictionary
from app.api.deps import Tenant, requires
from app.api.reports import _report_out, list_shared
from app.api.tasks import task_out
from app.auth.permissions import Permission
from app.dictionary import service as dictionary_service
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


@router.get("/today", response_model=TodayOut, responses=_ERRORS, summary="The home screen")
def today(tenant: Mine) -> TodayOut:
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
    )
