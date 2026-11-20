"""Usage against the Managed plan's monthly limit, and the warnings at 80% and 100% (R7, M18; D119).

The numbers are M5's (``app.llm.usage``): a Managed workspace's question and long-task runs on the platform's key in
its calendar month (its own time zone). BYOK has no platform limit, so no level and no warning. Briefings, checks and
dictionary drafts never count.

``after_limited_run`` is called once after each question or long task has finished. It records **one** event per
workspace per month for "80% used" and one for "limit reached" (a unique key makes this safe when two runs finish at
once), offers it to the billing provider (the stand-in sends nothing) and emails the Owners once. It never raises:
billing must not be able to break an answer.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from app.billing import provider as billing_provider
from app.connectors.registry import ConnectorRuntime
from app.db.ids import uuid7
from app.db.tenant import scope_to_org
from app.email import templates
from app.llm.usage import Month, current_month, questions_used
from app.models import BillingEvent, Membership, Organization, User

logger = logging.getLogger(__name__)

WARN_AT = 0.8  # BUILD_SPEC 14 M18: warnings at 80% and 100%
Level = Literal["none", "ok", "warning", "reached"]


@dataclass(frozen=True)
class LimitStatus:
    plan: str
    month: Month
    used: int
    limit: int | None  # None: no platform limit (BYOK)
    level: Level

    @property
    def percent(self) -> float | None:
        if self.limit is None:
            return None
        return 100.0 if self.limit <= 0 else round(100.0 * self.used / self.limit, 1)

    @property
    def resets_on(self) -> date:
        return self.month.next_first_day


def level_for(used: int, limit: int) -> Level:
    if used >= limit:
        return "reached"
    return "warning" if used >= limit * WARN_AT else "ok"


def status(db: DbSession, org: Organization, now: datetime) -> LimitStatus:
    month = current_month(now, org.timezone)
    if org.plan != "managed":
        return LimitStatus(org.plan, month, 0, None, "none")
    used = questions_used(db, org.id, month)
    return LimitStatus(
        org.plan,
        month,
        used,
        org.monthly_question_limit,
        level_for(used, org.monthly_question_limit),
    )


def record_event(
    db: DbSession,
    provider: billing_provider.BillingProvider,
    *,
    org_id: uuid.UUID,
    kind: str,
    period: str | None,
    details: dict[str, Any],
    user_id: uuid.UUID | None = None,
) -> BillingEvent | None:
    """Record one billing event and offer it to the provider. ``None`` when this month's event already exists.

    The row is added after the offer so its ``delivery`` is final (workspace requests may add rows, not change them)."""
    if period is not None and db.scalar(
        select(BillingEvent.id).where(
            BillingEvent.org_id == org_id, BillingEvent.kind == kind, BillingEvent.period == period
        )
    ):
        return None
    delivery = provider.deliver(billing_provider.Offered(kind, str(org_id), period, details))
    event_id = uuid7()
    inserted = db.execute(
        insert(BillingEvent)
        .values(
            id=event_id,
            org_id=org_id,
            kind=kind,
            period=period,
            details={**details, "delivery_note": delivery.note},
            provider=provider.name,
            delivery=delivery.status,
            created_by=user_id,
        )
        .on_conflict_do_nothing(index_elements=["org_id", "kind", "period"])
        .returning(BillingEvent.id)
    ).scalar()
    if inserted is None:  # another run recorded it at the same moment
        return None
    return db.get(BillingEvent, inserted)


def _session(rt: ConnectorRuntime, org_id: uuid.UUID) -> DbSession:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    return db


def _resets(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def after_limited_run(rt: ConnectorRuntime, org_id: uuid.UUID) -> None:
    """Called after a question or long task finished (any outcome). Never raises."""
    try:
        _after_limited_run(rt, org_id)
    except Exception:
        logger.exception("recording a usage warning failed; the answer is not affected")


def _after_limited_run(rt: ConnectorRuntime, org_id: uuid.UUID) -> None:
    provider = billing_provider.get_provider(rt.settings)
    with _session(rt, org_id) as db:
        org = db.get(Organization, org_id)
        if org is None:
            db.commit()
            return
        now = rt.clock()
        current = status(db, org, now)
        if current.level not in ("warning", "reached"):
            db.commit()
            return
        kind = "usage_warning" if current.level == "warning" else "usage_limit_reached"
        event = record_event(
            db,
            provider,
            org_id=org_id,
            kind=kind,
            period=current.month.label,
            details={
                "used": current.used,
                "limit": current.limit,
                "percent": current.percent,
                "resets_on": current.resets_on.isoformat(),
            },
        )
        recipients = (
            [
                (u.email, u.name)
                for u in db.scalars(
                    select(User)
                    .join(Membership, Membership.user_id == User.id)
                    .where(
                        Membership.org_id == org_id,
                        Membership.role == "owner",
                        Membership.status == "active",
                    )
                )
            ]
            if event is not None
            else []
        )
        org_name = org.name
        db.commit()
    if event is None or rt.email is None:
        return
    for email, _name in recipients:
        try:
            rt.email.send(
                usage_warning_email(
                    email,
                    org_name=org_name,
                    level=current.level,
                    used=current.used,
                    limit=current.limit or 0,
                    resets=_resets(current.resets_on),
                    link=f"{rt.settings.app_base_url}/settings",
                )
            )
        except Exception:
            logger.exception("sending a usage warning email failed")


def usage_warning_email(
    to: str, *, org_name: str, level: str, used: int, limit: int, resets: str, link: str
) -> Any:
    reached = level == "reached"
    subject = (
        f"{org_name}: the monthly question limit is reached"
        if reached
        else f"{org_name}: 80% of the monthly questions are used"
    )
    return templates.render(
        "usage_warning",
        to=to,
        subject=subject,
        org_name=org_name,
        reached=reached,
        used=used,
        limit=limit,
        resets=resets,
        link=link,
    )
