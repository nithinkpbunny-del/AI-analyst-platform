"""Invoice-ready monthly usage summaries (BUILD_SPEC R7, M18; D120).

A summary says what a workspace used in a calendar month (its own time zone) in the lines a future invoice would
carry. It follows M5's counting exactly (``app.llm.usage``). **It is not an invoice**: prices do not exist yet, so
``amount_due_inr`` is 0 and ``status`` is ``draft_no_payment``. The month after it ended, ``close_months`` freezes it
as a ``usage_summary`` billing event, once, so a future provider can bill from a record that no later change alters.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.billing import limits, plans
from app.billing import provider as billing_provider
from app.connectors.registry import ConnectorRuntime
from app.db.tenant import scope_to_org
from app.llm import usage
from app.llm.usage import Month
from app.models import BillingEvent, Organization

logger = logging.getLogger(__name__)

# A month is frozen this long after it ends (in the workspace's time zone), so a question answered on the stroke
# of midnight is already counted (D127). The beat job runs every 15 minutes.
FREEZE_GRACE = timedelta(minutes=5)

NOTE = "Billing is not switched on: this is a usage summary, not an invoice. No payment is taken and no price is set yet."


def build(db: DbSession, org: Organization, month: Month) -> dict[str, Any]:
    """The summary of ``month`` from the usage events. Pure reading; never writes."""
    totals = usage.summary(db, org, month).totals
    own_key_runs = max(0, totals.questions - totals.limited_questions)
    changes = [
        {
            "at": e.created_at.isoformat(),
            "from": e.details.get("from"),
            "to": e.details.get("to"),
        }
        for e in db.scalars(
            select(BillingEvent)
            .where(
                BillingEvent.org_id == org.id,
                BillingEvent.kind == "plan_changed",
                BillingEvent.created_at >= month.start,
                BillingEvent.created_at < month.end,
            )
            .order_by(BillingEvent.created_at)
        )
    ]
    # The plan the workspace was on at the end of the month: the last change in it, else today's plan if the month
    # is the current one or later, else the plan it was changed *from* by the first later change.
    plan_key = org.plan
    later = db.scalars(
        select(BillingEvent)
        .where(
            BillingEvent.org_id == org.id,
            BillingEvent.kind == "plan_changed",
            BillingEvent.created_at >= month.end,
        )
        .order_by(BillingEvent.created_at)
        .limit(1)
    ).first()
    if later is not None and later.details.get("from"):
        plan_key = str(later.details["from"])
    included = org.monthly_question_limit if plan_key == "managed" else None
    lines: list[dict[str, Any]] = [
        {
            "code": "questions_included",
            "description": "Questions and long analyses included in the plan",
            "quantity": included,
            "unit": "questions",
        },
        {
            "code": "questions_used",
            "description": "Questions and long analyses on the platform's AI",
            "quantity": totals.limited_questions,
            "unit": "questions",
        },
    ]
    if included is not None:
        lines.append(
            {
                "code": "over_limit",
                "description": "Above the included questions",
                "quantity": max(0, totals.limited_questions - included),
                "unit": "questions",
            }
        )
    lines += [
        {
            "code": "own_key_runs",
            "description": "Questions and long analyses on the workspace's own AI key (billed by its provider)",
            "quantity": own_key_runs,
            "unit": "questions",
        },
        {
            "code": "briefings",
            "description": "Morning briefings (not counted against the limit)",
            "quantity": totals.briefings,
            "unit": "briefings",
        },
        {
            "code": "ai_cost_estimate",
            "description": "Estimated AI cost, for information only",
            "quantity": float(totals.cost_usd),
            "unit": "USD",
        },
    ]
    last_day = month.next_first_day - timedelta(days=1)
    return {
        "workspace": {"id": str(org.id), "name": org.name},
        "period": {
            "month": month.label,
            "start": month.first_day.isoformat(),
            "end": last_day.isoformat(),
            "timezone": org.timezone,
        },
        "plan": {"key": plan_key, "name": plans.plan_name(org, plan_key)},
        "plan_changes": changes,
        "lines": lines,
        "currency": "INR",
        "amount_due_inr": 0,
        "price_status": "not_priced",
        "status": "draft_no_payment",
        "notes": [NOTE],
    }


def frozen(db: DbSession, org_id: uuid.UUID, month: Month) -> BillingEvent | None:
    return db.scalar(
        select(BillingEvent).where(
            BillingEvent.org_id == org_id,
            BillingEvent.kind == "usage_summary",
            BillingEvent.period == month.label,
        )
    )


def for_month(
    db: DbSession, org: Organization, month: Month
) -> tuple[dict[str, Any], datetime | None]:
    """The frozen summary of a finished month if there is one (and when it was frozen), else a live one."""
    saved = frozen(db, org.id, month)
    if saved is not None:
        body = {k: v for k, v in saved.details.items() if k != "delivery_note"}
        return body, saved.created_at
    return build(db, org, month), None


# Spreadsheet apps run cells that start with these as formulas ("CSV injection"); a workspace's name is its own text.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def to_csv(summary: dict[str, Any]) -> str:
    """One row per line, with the workspace and period on every row (so a sheet or a future importer needs no header)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        ["workspace", "month", "plan", "line", "description", "quantity", "unit", "status"]
    )
    for line in summary["lines"]:
        quantity = line["quantity"]
        writer.writerow(
            [
                _cell(summary["workspace"]["name"]),
                summary["period"]["month"],
                summary["plan"]["name"],
                line["code"],
                line["description"],
                "" if quantity is None else quantity,
                line["unit"],
                summary["status"],
            ]
        )
    writer.writerow(
        [
            _cell(summary["workspace"]["name"]),
            summary["period"]["month"],
            summary["plan"]["name"],
            "amount_due",
            "Amount due (no price is set yet)",
            summary["amount_due_inr"],
            "INR",
            summary["status"],
        ]
    )
    return out.getvalue()


def previous_month(org: Organization, now: datetime) -> Month:
    current = usage.current_month(now, org.timezone)
    first = usage.month_of(
        date(
            current.first_day.year - (1 if current.first_day.month == 1 else 0),
            12 if current.first_day.month == 1 else current.first_day.month - 1,
            1,
        ),
        org.timezone,
    )
    return first


def close_months(rt: ConnectorRuntime, now: datetime) -> int:
    """Freeze last month's summary for every workspace that has none yet. Returns how many were frozen."""
    provider = billing_provider.get_provider(rt.settings)
    with rt.session_factory() as db:
        org_ids = list(db.scalars(select(Organization.id)))
        db.commit()
    frozen_now = 0
    for org_id in org_ids:
        try:
            db = rt.session_factory()
            scope_to_org(db, org_id)
            with db:
                org = db.get(Organization, org_id)
                if org is None:
                    db.commit()
                    continue
                month = previous_month(org, now)
                if (
                    now < month.end + FREEZE_GRACE
                    or org.created_at >= month.end
                    or frozen(db, org_id, month) is not None
                ):
                    db.commit()
                    continue
                body = build(db, org, month)
                event = limits.record_event(
                    db,
                    provider,
                    org_id=org_id,
                    kind="usage_summary",
                    period=month.label,
                    details=body,
                )
                db.commit()
                frozen_now += 1 if event is not None else 0
        except Exception:
            logger.exception("freezing a usage summary failed; it is tried again next time")
    return frozen_now
