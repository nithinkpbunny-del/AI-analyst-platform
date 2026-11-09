"""Usage metering and the Managed plan's monthly limit (R7, BUILD_SPEC 7.8, DECISIONS D42)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.llm.base import Usage, UserMessage
from app.llm.fake import FakeLLM, FakeTurn
from app.llm.usage import (
    MeteredLLM,
    UsageLimitReached,
    current_month,
    ensure_within_limit,
    month_of,
    questions_used,
    record,
    summary,
)
from app.models import Organization, UsageEvent, User
from tests.tenancy import make_org

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def _event(
    db: DbSession,
    org: Organization,
    *,
    kind: str = "question",
    run: uuid.UUID | None = None,
    key_source: str = "managed",
    at: datetime = NOW,
    user: User | None = None,
    model: str = "gemini-2.5-flash",
    tokens: tuple[int, int] = (1000, 200),
) -> uuid.UUID:
    run = run or uuid.uuid4()
    record(
        db,
        org_id=org.id,
        user_id=user.id if user else None,
        run_id=run,
        kind=kind,
        provider="gemini",
        model=model,
        usage=Usage(*tokens),
        key_source=key_source,
        at=at,
    )
    return run


@pytest.fixture
def org(db: DbSession) -> Organization:
    return make_org(db, "UrbanCart", "asha@urbancart.in", "Asha Rao")


def test_months_follow_the_workspace_time_zone() -> None:
    month = month_of(date(2026, 11, 1), "Asia/Kolkata")
    assert month.start == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    assert month.end == datetime(2026, 11, 30, 18, 30, tzinfo=UTC)
    assert month.label == "2026-11"
    assert month.next_first_day == date(2026, 12, 1)
    # 1 December 01:00 in India is still 30 November in UTC, but already December for the workspace.
    assert (
        current_month(datetime(2026, 11, 30, 19, 30, tzinfo=UTC), "Asia/Kolkata").label == "2026-12"
    )
    assert month_of(date(2026, 12, 1), "UTC").next_first_day == date(2027, 1, 1)


def test_questions_count_runs_not_calls(db: DbSession, org: Organization) -> None:
    run = _event(db, org)
    _event(db, org, run=run)  # the same question made a second LLM call
    _event(db, org, kind="task")
    _event(db, org, kind="briefing")  # briefings never count
    _event(db, org, kind="check")  # neither do connection checks
    _event(db, org, key_source="byok")  # nor runs made with the workspace's own key
    _event(db, org, at=datetime(2026, 10, 31, 18, 29, tzinfo=UTC))  # still October in India
    db.commit()
    assert questions_used(db, org.id, current_month(NOW, org.timezone)) == 2


def test_the_limit_is_checked_before_a_question(db: DbSession, org: Organization) -> None:
    org.monthly_question_limit = 2
    _event(db, org)
    db.commit()
    ensure_within_limit(db, org, NOW)  # 1 of 2: fine
    _event(db, org)
    db.commit()
    with pytest.raises(UsageLimitReached) as caught:
        ensure_within_limit(db, org, NOW)
    assert (caught.value.used, caught.value.limit) == (2, 2)
    assert caught.value.resets_on == date(2026, 12, 1)
    # A new month starts fresh.
    ensure_within_limit(db, org, datetime(2026, 12, 1, 0, 0, tzinfo=UTC))


def test_byok_workspaces_have_no_platform_limit(db: DbSession, org: Organization) -> None:
    org.plan = "byok"
    org.monthly_question_limit = 0
    db.commit()
    ensure_within_limit(db, org, NOW)


def test_metered_calls_record_tokens_and_estimated_cost(db: DbSession, org: Organization) -> None:
    user = db.scalars(select(User)).one()
    llm = FakeLLM(
        [FakeTurn("a", usage=Usage(10_000, 2_000)), FakeTurn("b", usage=Usage(500, 100))],
        model="gemini-2.5-flash",
    )
    metered = MeteredLLM(
        llm, db, org_id=org.id, user_id=user.id, kind="question", key_source="managed", at=NOW
    )
    metered.generate("", [UserMessage("q")])
    metered.generate("", [UserMessage("q")])
    db.commit()
    events = db.scalars(select(UsageEvent).order_by(UsageEvent.input_tokens.desc())).all()
    assert [(e.input_tokens, e.output_tokens) for e in events] == [(10_000, 2_000), (500, 100)]
    assert {e.run_id for e in events} == {metered.run_id}
    assert events[0].est_cost_usd == Decimal("0.008000")
    assert events[0].user_id == user.id
    assert metered.total == Usage(10_500, 2_100)


def test_a_failed_call_records_nothing(db: DbSession, org: Organization) -> None:
    from app.llm.base import LLMError

    metered = MeteredLLM(
        FakeLLM([LLMError("quota", "gemini")]),
        db,
        org_id=org.id,
        user_id=None,
        kind="question",
        key_source="managed",
    )
    with pytest.raises(LLMError):
        metered.generate("", [UserMessage("q")])
    assert db.scalars(select(UsageEvent)).all() == []


def test_summary_per_person_and_six_months(db: DbSession, org: Organization) -> None:
    asha = db.scalars(select(User)).one()
    run = _event(db, org, user=asha, tokens=(10_000, 2_000))
    _event(db, org, run=run, user=asha, tokens=(5_000, 1_000))
    _event(db, org, kind="briefing", user=asha, tokens=(2_000, 500))
    _event(db, org, kind="task", user=None, model="unknown-model", tokens=(100, 10))
    _event(db, org, at=datetime(2026, 9, 15, tzinfo=UTC), user=asha)
    other = make_org(db, "Chaiwala", "kabir@chaiwala.in")
    _event(db, other)  # another workspace: never included
    db.commit()

    result = summary(db, org, current_month(NOW, org.timezone))
    totals = result.totals
    assert (totals.questions, totals.limited_questions, totals.tasks, totals.briefings) == (
        2,
        2,
        1,
        1,
    )
    assert (totals.input_tokens, totals.output_tokens) == (17_100, 3_510)
    assert totals.cost_unknown_calls == 1
    assert totals.cost_usd == Decimal("0.003000") + Decimal("0.005000") + Decimal(
        "0.001500"
    ) + Decimal("0.002500") + Decimal("0.000600") + Decimal("0.001250")
    assert result.limit == 500
    people = {p.email: p for p in result.people}
    assert people["asha@urbancart.in"].questions == 1
    assert people["asha@urbancart.in"].input_tokens == 17_000
    assert people[None].questions == 1  # a run with no person (e.g. a system task)
    assert [h.month for h in result.history] == [
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
        "2026-11",
    ]
    assert [h.questions for h in result.history] == [0, 0, 0, 1, 0, 2]
