"""Usage metering and the Managed plan's monthly limit (R7, BUILD_SPEC 7.8, DECISIONS D42).

* Every LLM call writes one ``usage_events`` row: tokens, estimated cost, provider, model, key source.
  ``MeteredLLM`` does this automatically around any client.
* A **run** is one question, long task, briefing or connection check; all of its calls share a ``run_id``.
* The monthly limit counts **question and long-task runs made with the platform's key** in the workspace's
  calendar month (in its own time zone). Briefings and checks are recorded but never count. BYOK workspaces have
  no platform limit (rate limits still apply).
* ``ensure_within_limit`` is called before a question starts (BUILD_SPEC 7.8). Two questions started at the same
  moment can both pass the check, so a workspace may go over by a question or two; that is accepted.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session as DbSession

from app.db.ids import uuid7
from app.llm.base import LLMClient, LLMResponse, Message, ToolSpec, Usage
from app.llm.pricing import estimate_cost_usd
from app.models import Organization, UsageEvent, User
from app.models.ai import LIMITED_KINDS

HISTORY_MONTHS = 6


@dataclass(frozen=True)
class Month:
    """A calendar month in the workspace's time zone, with its UTC bounds."""

    first_day: date
    start: datetime
    end: datetime

    @property
    def label(self) -> str:
        return self.first_day.strftime("%Y-%m")

    @property
    def next_first_day(self) -> date:
        return _add_months(self.first_day, 1)


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def month_of(first_day: date, timezone: str) -> Month:
    zone = ZoneInfo(timezone)
    start = datetime(first_day.year, first_day.month, 1, tzinfo=zone)
    nxt = _add_months(first_day, 1)
    end = datetime(nxt.year, nxt.month, 1, tzinfo=zone)
    return Month(
        first_day=first_day.replace(day=1), start=start.astimezone(UTC), end=end.astimezone(UTC)
    )


def current_month(now: datetime, timezone: str) -> Month:
    local = now.astimezone(ZoneInfo(timezone))
    return month_of(date(local.year, local.month, 1), timezone)


class UsageLimitReached(Exception):
    def __init__(self, used: int, limit: int, resets_on: date) -> None:
        super().__init__(f"monthly question limit reached ({used}/{limit})")
        self.used = used
        self.limit = limit
        self.resets_on = resets_on


def questions_used(db: DbSession, org_id: uuid.UUID, month: Month) -> int:
    query = select(func.count(distinct(UsageEvent.run_id))).where(
        UsageEvent.org_id == org_id,
        UsageEvent.kind.in_(LIMITED_KINDS),
        UsageEvent.key_source == "managed",
        UsageEvent.created_at >= month.start,
        UsageEvent.created_at < month.end,
    )
    return int(db.scalar(query) or 0)


def ensure_within_limit(db: DbSession, org: Organization, now: datetime) -> None:
    """Raise ``UsageLimitReached`` when a Managed workspace has used its questions for this month."""
    if org.plan != "managed":
        return
    month = current_month(now, org.timezone)
    used = questions_used(db, org.id, month)
    if used >= org.monthly_question_limit:
        raise UsageLimitReached(used, org.monthly_question_limit, month.next_first_day)


def record(
    db: DbSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    run_id: uuid.UUID,
    kind: str,
    provider: str,
    model: str,
    usage: Usage,
    key_source: str,
    at: datetime | None = None,
) -> UsageEvent:
    event = UsageEvent(
        org_id=org_id,
        user_id=user_id,
        run_id=run_id,
        kind=kind,
        provider=provider,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        est_cost_usd=estimate_cost_usd(model, usage, on=(at or datetime.now(UTC)).date()),
        key_source=key_source,
    )
    if at is not None:
        event.created_at = at
        event.updated_at = at
    db.add(event)
    db.flush()
    return event


class MeteredLLM:
    """Wraps a client so that every successful ``generate`` records a usage event for one run."""

    def __init__(
        self,
        client: LLMClient,
        db: DbSession,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID | None,
        kind: str,
        key_source: str,
        run_id: uuid.UUID | None = None,
        at: datetime | None = None,
    ) -> None:
        self.client = client
        self.run_id = run_id or uuid7()
        self.total = Usage()
        self._db = db
        self._org_id = org_id
        self._user_id = user_id
        self._kind = kind
        self._key_source = key_source
        self._at = at

    @property
    def provider(self) -> str:
        return self.client.provider

    @property
    def model(self) -> str:
        return self.client.model

    def list_models(self) -> list[str]:
        return self.client.list_models()

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 3000,
    ) -> LLMResponse:
        response = self.client.generate(system, messages, tools, max_tokens)
        self.total = self.total + response.usage
        record(
            self._db,
            org_id=self._org_id,
            user_id=self._user_id,
            run_id=self.run_id,
            kind=self._kind,
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            key_source=self._key_source,
            at=self._at,
        )
        return response


# ------------------------------------------------------------------------------------------------ summary
@dataclass(frozen=True)
class Totals:
    questions: int  # question + long-task runs (all keys)
    limited_questions: int  # the ones counted against the Managed limit
    tasks: int
    briefings: int
    checks: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    cost_unknown_calls: int


@dataclass(frozen=True)
class PersonUsage:
    user_id: uuid.UUID | None
    name: str | None
    email: str | None
    questions: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class MonthTotals:
    month: str
    questions: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class UsageSummary:
    month: Month
    plan: str
    limit: int | None
    totals: Totals
    people: list[PersonUsage]
    history: list[MonthTotals]


def _runs(kind: str) -> Any:
    return func.count(distinct(UsageEvent.run_id)).filter(UsageEvent.kind == kind)


def _totals(db: DbSession, org_id: uuid.UUID, month: Month) -> Totals:
    in_month = (
        UsageEvent.org_id == org_id,
        UsageEvent.created_at >= month.start,
        UsageEvent.created_at < month.end,
    )
    row = db.execute(
        select(
            func.count(distinct(UsageEvent.run_id)).filter(UsageEvent.kind.in_(LIMITED_KINDS)),
            _runs("task"),
            _runs("briefing"),
            _runs("check"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.est_cost_usd), 0),
            func.count().filter(UsageEvent.est_cost_usd.is_(None)),
        ).where(*in_month)
    ).one()
    return Totals(
        questions=int(row[0]),
        limited_questions=questions_used(db, org_id, month),
        tasks=int(row[1]),
        briefings=int(row[2]),
        checks=int(row[3]),
        input_tokens=int(row[4]),
        output_tokens=int(row[5]),
        cost_usd=Decimal(row[6]),
        cost_unknown_calls=int(row[7]),
    )


def _people(db: DbSession, org_id: uuid.UUID, month: Month) -> list[PersonUsage]:
    rows = db.execute(
        select(
            UsageEvent.user_id,
            User.name,
            User.email,
            func.count(distinct(UsageEvent.run_id)).filter(UsageEvent.kind.in_(LIMITED_KINDS)),
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.coalesce(func.sum(UsageEvent.est_cost_usd), 0),
        )
        .outerjoin(User, User.id == UsageEvent.user_id)
        .where(
            UsageEvent.org_id == org_id,
            UsageEvent.created_at >= month.start,
            UsageEvent.created_at < month.end,
        )
        .group_by(UsageEvent.user_id, User.name, User.email)
    ).all()
    people = [
        PersonUsage(
            user_id=r[0],
            name=r[1],
            email=r[2],
            questions=int(r[3]),
            input_tokens=int(r[4] or 0),
            output_tokens=int(r[5] or 0),
            cost_usd=Decimal(r[6] or 0),
        )
        for r in rows
    ]
    return sorted(people, key=lambda p: (-p.questions, -(p.input_tokens + p.output_tokens)))


def _history(db: DbSession, org: Organization, month: Month) -> list[MonthTotals]:
    history = []
    for back in range(HISTORY_MONTHS - 1, -1, -1):
        past = month_of(_add_months(month.first_day, -back), org.timezone)
        totals = _totals(db, org.id, past)
        history.append(
            MonthTotals(
                month=past.label,
                questions=totals.questions,
                input_tokens=totals.input_tokens,
                output_tokens=totals.output_tokens,
                cost_usd=totals.cost_usd,
            )
        )
    return history


def summary(db: DbSession, org: Organization, month: Month) -> UsageSummary:
    return UsageSummary(
        month=month,
        plan=org.plan,
        limit=org.monthly_question_limit if org.plan == "managed" else None,
        totals=_totals(db, org.id, month),
        people=_people(db, org.id, month),
        history=_history(db, org, month),
    )
