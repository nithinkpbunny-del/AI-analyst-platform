"""Briefings end to end (BUILD_SPEC R10, 13; M10): settings, "send me one now", the scheduler, emails, reports.

Jobs run inline; the fake LLM stands in for the model (no provider is called).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.briefings import schedule
from app.briefings import service as briefings
from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.llm.base import ToolCall
from app.llm.fake import FakeLLMFactory, FakeTurn
from app.models import AuditLog, BriefingRun, BriefingSetting, Organization, Report, UsageEvent
from tests.api.test_chat import orders_csv
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def orders(client: TestClient, world: World) -> dict[str, Any]:
    response = upload(client, world.headers("owner_a"), "orders.csv", orders_csv())
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


def recent_orders_csv(days: int = 70) -> bytes:
    """Steady daily orders up to yesterday (the test clock is 2026-11-09): fresh data, nothing unusual."""
    lines = ["id,created_at,customer_id,sales_channel,total_price,currency"]
    start = datetime(2026, 11, 8) - timedelta(days=days - 1)
    for d in range(days):
        day = start + timedelta(days=d)
        for i in range(3):
            lines.append(
                f"{d * 10 + i},{day:%Y-%m-%d}T1{i}:00:00+05:30,{7000 + (d + i) % 9},App,{1000 + (i * 7 + d) % 5}.0,INR"
            )
    return ("\n".join(lines) + "\n").encode()


def send_now(client: TestClient, world: World, who: str = "owner_a") -> Any:
    return client.post("/briefings/send-now", headers=world.headers(who))


class TestSettings:
    def test_defaults_by_role(self, client: TestClient, world: World) -> None:
        owner = client.get("/briefings/settings", headers=world.headers("owner_a")).json()
        assert owner["enabled"] and owner["local_time"] == "06:00" and owner["default"]
        assert owner["timezone"] == "Asia/Kolkata" and owner["next_at"]
        analyst = client.get("/briefings/settings", headers=world.headers("analyst_a")).json()
        assert not analyst["enabled"] and analyst["next_at"] is None

    def test_change_and_audit(self, client: TestClient, world: World, db: DbSession) -> None:
        response = client.put(
            "/briefings/settings",
            json={"enabled": True, "local_time": "07:30", "frequency": "weekly", "weekday": 2},
            headers=world.headers("viewer_a"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] and body["local_time"] == "07:30" and body["frequency"] == "weekly"
        assert not body["default"]
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "briefing.settings_changed")
        ).one()
        assert entry.user_id == world.viewer_a.id and entry.details["frequency"] == "weekly"

    @pytest.mark.parametrize(
        "body",
        [
            {"frequency": "hourly"},
            {"weekday": 7},
            {"language": "hi"},
            {"timezone": "Mars/Base"},
            {"x": 1},
        ],
    )
    def test_validation(self, client: TestClient, world: World, body: dict[str, Any]) -> None:
        response = client.put("/briefings/settings", json=body, headers=world.headers("owner_a"))
        assert response.status_code == 422

    def test_signed_out(self, client: TestClient) -> None:
        assert client.get("/briefings/settings").status_code == 401


class TestSendNow:
    def test_without_ai_code_writes_it_and_it_is_emailed(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        outbox: MemoryEmailSender,
        db: DbSession,
    ) -> None:
        # The fake model answers "OK" (no write_briefing call): the code version is used.
        response = send_now(client, world)
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["status"] == "sent" and run["kind"] == "test" and run["report_id"]
        report = client.get(f"/reports/{run['report_id']}", headers=world.headers("owner_a")).json()
        assert report["source"] == "briefing" and report["body"]["written_by"] == "code"
        assert report["body"]["headline"]
        mail = outbox.to("asha@urbancart.in")[-1]
        assert f"/reports/{run['report_id']}" in mail.html and "/briefings" in mail.text
        assert "/today" in mail.html
        # The AI call was metered as a briefing, not a question.
        assert set(db.scalars(select(UsageEvent.kind))) <= {"briefing", "dictionary"}

    def test_the_ai_writes_it_once_and_identical_facts_reuse_the_words(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        llm_factory.next_script = [
            FakeTurn(
                tool_calls=[
                    ToolCall(
                        "w1",
                        "write_briefing",
                        {
                            "subject": "Quiet day",
                            "headline": "A quiet day for UrbanCart.",
                            "health": ["Nothing stood out."],
                        },
                    )
                ]
            )
        ]
        first = send_now(client, world).json()
        report = client.get(
            f"/reports/{first['report_id']}", headers=world.headers("owner_a")
        ).json()
        assert report["body"]["written_by"] == "ai" and report["body"]["subject"] == "Quiet day"
        assert db.scalars(select(UsageEvent).where(UsageEvent.kind == "briefing")).one()
        clients = len(llm_factory.clients)
        second = send_now(client, world).json()
        again = client.get(
            f"/reports/{second['report_id']}", headers=world.headers("owner_a")
        ).json()
        assert again["body"]["headline"] == "A quiet day for UrbanCart."
        # No second AI call for the same facts.
        assert len(db.scalars(select(UsageEvent).where(UsageEvent.kind == "briefing")).all()) == 1
        assert (
            len(llm_factory.clients) == clients
        )  # the cached words are found before any AI client is made

    def test_no_data_is_skipped(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        run = send_now(client, world).json()
        assert run["status"] == "skipped" and run["error_code"] == "no_dictionary"
        assert outbox.to("asha@urbancart.in") == []

    def test_rate_limited(self, client: TestClient, world: World) -> None:
        codes = [send_now(client, world, "analyst_a").status_code for _ in range(4)]
        assert codes == [202, 202, 202, 429]

    def test_list_my_briefings(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        send_now(client, world)
        runs = client.get("/briefings", headers=world.headers("owner_a")).json()["runs"]
        assert len(runs) == 1 and runs[0]["headline"]
        assert client.get("/briefings", headers=world.headers("owner_b")).json()["runs"] == []

    def test_not_while_the_workspace_is_being_deleted(
        self, client: TestClient, world: World, db: DbSession, clock: FakeClock
    ) -> None:
        org = db.get(Organization, world.org_a.id)
        assert org is not None
        org.deletion_requested_at = clock.now
        db.commit()
        assert send_now(client, world).status_code == 409


class TestScheduler:
    def test_due_owners_get_one_run_per_slot(
        self, db: DbSession, world: World, session_factory: Any
    ) -> None:
        at = datetime(2026, 11, 13, 0, 31, tzinfo=UTC)  # 06:01 in India
        with session_factory() as unscoped:
            created = schedule.plan(unscoped, at)
        owners = {world.owner_a.id, world.owner_b.id}
        runs = list(db.scalars(select(BriefingRun)))
        assert {r.user_id for r in runs} == owners and len(created) == 2
        assert {r.scheduled_for for r in runs} == {datetime(2026, 11, 13, 0, 30, tzinfo=UTC)}
        with session_factory() as unscoped:
            assert schedule.plan(unscoped, at + timedelta(minutes=5)) == []  # idempotent

    def test_settings_are_followed(self, db: DbSession, world: World, session_factory: Any) -> None:
        db.add(
            BriefingSetting(
                org_id=world.org_a.id,
                user_id=world.analyst_a.id,
                enabled=True,
                local_time=time(8, 0),
                frequency="daily",
                weekday=0,
                language="en",
            )
        )
        db.add(
            BriefingSetting(
                org_id=world.org_a.id,
                user_id=world.owner_a.id,
                enabled=False,
                local_time=time(6, 0),
                frequency="daily",
                weekday=0,
                language="en",
            )
        )
        db.commit()
        with session_factory() as unscoped:
            created = schedule.plan(
                unscoped, datetime(2026, 11, 13, 2, 30, tzinfo=UTC)
            )  # 08:00 IST
        runs = [db.get(BriefingRun, run_id) for _, run_id in created]
        assert [r.user_id for r in runs if r is not None] == [world.analyst_a.id]

    def test_a_workspace_being_deleted_gets_none(
        self, db: DbSession, world: World, session_factory: Any, clock: FakeClock
    ) -> None:
        org = db.get(Organization, world.org_a.id)
        assert org is not None
        org.deletion_requested_at = clock.now
        db.commit()
        with session_factory() as unscoped:
            created = schedule.plan(unscoped, datetime(2026, 11, 13, 0, 31, tzinfo=UTC))
        assert [org_id for org_id, _ in created] == [world.org_b.id]

    def test_alerts_only_skips_a_quiet_day(
        self,
        db: DbSession,
        world: World,
        runtime: Any,
        llm_factory: FakeLLMFactory,
        client: TestClient,
        outbox: MemoryEmailSender,
    ) -> None:
        upload(client, world.headers("owner_a"), "orders.csv", recent_orders_csv())
        db.add(
            BriefingSetting(
                org_id=world.org_a.id,
                user_id=world.owner_a.id,
                enabled=True,
                local_time=time(6, 0),
                frequency="alerts_only",
                weekday=0,
                language="en",
            )
        )
        run = BriefingRun(
            org_id=world.org_a.id,
            user_id=world.owner_a.id,
            scheduled_for=datetime(2026, 11, 13, 0, 30, tzinfo=UTC),
            kind="scheduled",
            status="queued",
            findings=[],
        )
        db.add(run)
        db.commit()
        before = len(outbox.sent)
        assert briefings.send_briefing(runtime, llm_factory, world.org_a.id, run.id) == "skipped"
        assert len(outbox.sent) == before
        db.expire_all()
        assert db.get(BriefingRun, run.id).error_code == "nothing_to_report"  # type: ignore[union-attr]

    def test_a_finished_run_is_not_sent_twice(
        self,
        runtime: Any,
        llm_factory: FakeLLMFactory,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        db: DbSession,
    ) -> None:
        run_id = send_now(client, world).json()["id"]
        assert briefings.send_briefing(runtime, llm_factory, world.org_a.id, run_id) == "sent"
        assert len(db.scalars(select(Report)).all()) == 1


def test_dictionary_draft_after_a_schema_change_is_emailed_to_owners(
    client: TestClient, world: World, outbox: MemoryEmailSender, db: DbSession
) -> None:
    headers = world.headers("owner_a")
    first = upload(client, headers, "orders.csv", orders_csv()).json()
    assert client.post("/dictionary/draft/confirm", headers=headers).status_code == 200
    before = len(outbox.to("asha@urbancart.in"))
    changed = (
        orders_csv()
        .replace(b",currency\n", b",currency,coupon\n", 1)
        .replace(b",INR\n", b",INR,X\n")
    )
    response = upload(client, headers, "orders.csv", changed, connection_id=first["id"])
    assert response.status_code == 200, response.text
    mails = outbox.to("asha@urbancart.in")[before:]
    assert any(m.subject == "Your data changed: check the business dictionary" for m in mails)
    assert all("ravi@" not in m.to for m in mails)
