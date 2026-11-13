"""Long tasks end to end with the fake LLM (BUILD_SPEC R10, 13.5, 6.5; M10). Jobs run inline."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.data.objects import MemoryObjectStore
from app.email.sender import MemoryEmailSender
from app.llm.base import LLMError
from app.llm.fake import FakeLLMFactory
from app.models import AuditLog, Report, Task, UsageEvent
from app.tasks import long_tasks
from tests.api.test_chat import answer_turn, kpi_turn, orders_csv
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world

PROMPT = "Analyse January sales by channel and tell me what to fix."


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def orders(client: TestClient, world: World) -> dict[str, Any]:
    response = upload(client, world.headers("owner_a"), "orders.csv", orders_csv())
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


def submit(client: TestClient, world: World, who: str = "owner_a", prompt: str = PROMPT) -> Any:
    return client.post("/tasks", json={"prompt": prompt}, headers=world.headers(who))


class TestRunning:
    def test_a_task_runs_makes_a_report_and_emails_the_pdf(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        outbox: MemoryEmailSender,
        object_store: MemoryObjectStore,
        db: DbSession,
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        response = submit(client, world)
        assert response.status_code == 201, response.text
        task = client.get(
            f"/tasks/{response.json()['id']}", headers=world.headers("owner_a")
        ).json()
        assert task["status"] == "done", task
        assert task["progress"][-1] == {"code": "done"}
        assert task["report_id"]
        report = client.get(
            f"/reports/{task['report_id']}", headers=world.headers("owner_a")
        ).json()
        assert report["source"] == "task" and report["mine"]
        assert report["body"]["question"] == PROMPT
        assert report["body"]["answer"]["lead"].startswith("Sales in January were")
        # The cited result keeps its rows for the Excel export.
        assert report["provenance"]["r1"]["data"]["columns"]
        stored = db.scalars(select(Report)).one()
        assert stored.pdf_key and object_store.exists(stored.pdf_key)
        mail = outbox.to("asha@urbancart.in")[-1]
        assert mail.subject.startswith("Your analysis is ready")
        assert mail.attachments and mail.attachments[0].data.startswith(b"%PDF")
        assert f"/reports/{task['report_id']}" in mail.text and "[r1]" not in mail.text
        # Metered as a long task, and the submission is in the activity log (hash only).
        kinds = set(db.scalars(select(UsageEvent.kind)))
        assert kinds == {"task"} or "task" in kinds
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "task.submitted")).one()
        assert "prompt_sha256" in entry.details and PROMPT not in str(entry.details)

    def test_long_task_limits_are_used(
        self, client: TestClient, world: World, orders: dict[str, Any], llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        submit(client, world)
        client_llm = llm_factory.clients[-1]
        assert "40" in client_llm.calls[0].system  # AGENT_MAX_STEPS_LONG_TASK in the prompt

    def test_a_failure_is_emailed_with_the_reason(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        outbox: MemoryEmailSender,
    ) -> None:
        llm_factory.next_script = [LLMError("quota", "gemini", "daily quota")]
        task = submit(client, world).json()
        found = client.get(f"/tasks/{task['id']}", headers=world.headers("owner_a")).json()
        assert found["status"] == "failed" and found["error_code"] == "llm:quota"
        mail = outbox.to("asha@urbancart.in")[-1]
        assert mail.subject == "Your analysis could not finish"
        assert "AI service" in mail.text

    def test_without_data_it_fails_politely(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        task = submit(client, world).json()
        assert task["status"] == "failed" and task["error_code"] == "no_dictionary"
        assert "dictionary" in outbox.to("asha@urbancart.in")[-1].text


class TestLimitsAndCancel:
    def test_two_running_tasks_per_workspace(
        self, client: TestClient, world: World, db: DbSession, orders: dict[str, Any]
    ) -> None:
        for _ in range(2):
            db.add(
                Task(
                    org_id=world.org_a.id,
                    user_id=world.analyst_a.id,
                    prompt="x" * 10,
                    title="x",
                    lens="business",
                    status="running",
                    progress_json=[],
                    token_usage={},
                )
            )
        db.commit()
        response = submit(client, world)
        assert response.status_code == 429 and response.json()["error"]["code"] == "too_many_tasks"
        # Another workspace is not affected.
        assert client.get("/tasks", headers=world.headers("owner_b")).json()["running"] == 0

    def test_a_dead_task_frees_its_slot(
        self, client: TestClient, world: World, db: DbSession, clock: FakeClock
    ) -> None:
        for _ in range(2):
            db.add(
                Task(
                    org_id=world.org_a.id,
                    user_id=world.owner_a.id,
                    prompt="x" * 10,
                    title="x",
                    lens="business",
                    status="running",
                    progress_json=[],
                    token_usage={},
                    started_at=clock.now - timedelta(hours=3),
                )
            )
        db.commit()
        listed = client.get("/tasks", headers=world.headers("owner_a")).json()
        assert {t["status"] for t in listed["tasks"]} == {"failed"}
        assert {t["error_code"] for t in listed["tasks"]} == {"timeout"}
        assert listed["running"] == 0

    def test_cancel_a_queued_task(self, client: TestClient, world: World, db: DbSession) -> None:
        task = Task(
            org_id=world.org_a.id,
            user_id=world.owner_a.id,
            prompt="x" * 10,
            title="x",
            lens="business",
            status="queued",
            progress_json=[],
            token_usage={},
        )
        db.add(task)
        db.commit()
        response = client.post(f"/tasks/{task.id}/cancel", headers=world.headers("owner_a"))
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
        again = client.post(f"/tasks/{task.id}/cancel", headers=world.headers("owner_a"))
        assert again.status_code == 409

    def test_a_running_task_stops_before_its_next_ai_call(
        self,
        runtime: Any,
        world: World,
        db: DbSession,
        llm_factory: FakeLLMFactory,
        client: TestClient,
        orders: dict[str, Any],
    ) -> None:
        task = Task(
            org_id=world.org_a.id,
            user_id=world.owner_a.id,
            prompt=PROMPT,
            title="x",
            lens="business",
            status="queued",
            progress_json=[],
            token_usage={},
            cancel_requested=False,
        )
        db.add(task)
        db.commit()
        calls = {"n": 0}

        def stop_after_first() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        from app.agent import service as agent_service

        llm_factory.next_script = [kpi_turn(), answer_turn()]
        original = agent_service.run_agent

        def run(*args: Any, **kwargs: Any) -> Any:
            kwargs["should_stop"] = stop_after_first
            return original(*args, **kwargs)

        agent_service.run_agent = run
        try:
            status = long_tasks.run_task(runtime, llm_factory, world.org_a.id, task.id)
        finally:
            agent_service.run_agent = original
        assert status == "cancelled"
        assert llm_factory.clients[-1].remaining == 1  # the second model call never happened


class TestAccess:
    def test_viewers_cannot_submit_or_see_tasks(self, client: TestClient, world: World) -> None:
        assert submit(client, world, "viewer_a").status_code == 403
        missing = "0190c0de-0000-7000-8000-000000000000"
        headers = world.headers("viewer_a")
        assert client.get(f"/tasks/{missing}", headers=headers).status_code == 403
        assert client.post(f"/tasks/{missing}/cancel", headers=headers).status_code == 403
        assert client.post(f"/tasks/{missing}/cancel").status_code == 401

    def test_someone_elses_task_is_not_found(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        task = Task(
            org_id=world.org_a.id,
            user_id=world.analyst_a.id,
            prompt="x" * 10,
            title="x",
            lens="business",
            status="queued",
            progress_json=[],
            token_usage={},
        )
        db.add(task)
        db.commit()
        for who in ("owner_a", "owner_b"):
            assert client.get(f"/tasks/{task.id}", headers=world.headers(who)).status_code == 404
            assert (
                client.post(f"/tasks/{task.id}/cancel", headers=world.headers(who)).status_code
                == 404
            )
        assert (
            client.get(f"/tasks/{task.id}", headers=world.headers("analyst_a")).status_code == 200
        )
        assert client.get(f"/tasks/{task.id}").status_code == 401
        assert (
            client.get("/tasks/not-a-uuid", headers=world.headers("analyst_a")).status_code == 422
        )

    def test_validation(self, client: TestClient, world: World) -> None:
        response = client.post(
            "/tasks", json={"prompt": "   hi   "}, headers=world.headers("owner_a")
        )
        assert response.status_code == 422
