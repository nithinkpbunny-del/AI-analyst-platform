"""AI plan, the workspace's own key (BYOK), connection checks and usage (R7, BUILD_SPEC 6.4, 7.2, 7.8).

401/403/404/422 for every route are covered by ``test_route_matrix``; these tests cover the behaviour.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.llm import usage
from app.llm.base import LLMError, Usage
from app.llm.fake import FakeLLMFactory, FakeTurn
from app.main import create_app
from app.models import AuditLog, LlmKey, Organization, Secret, UsageEvent
from tests.conftest import FakeClock
from tests.tenancy import World, build_world, token_for

GEMINI_KEY = "AIzaSyOwnWorkspaceKey-000000000000000000"
CLAUDE_KEY = "sk-ant-api03-own-workspace-key-0000000000"


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def owner(world: World) -> dict[str, str]:
    return world.headers("owner_a")


def actions(db: DbSession, org: Organization) -> list[AuditLog]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.org_id == org.id, AuditLog.action.not_like("auth.%"))
            .order_by(AuditLog.at, AuditLog.id)
        )
    )


def put_key(
    client: TestClient, world: World, key: str = GEMINI_KEY, provider: str = "gemini"
) -> Any:
    return client.put(
        "/org/ai/key", json={"provider": provider, "api_key": key}, headers=owner(world)
    )


class TestSettings:
    def test_a_new_workspace_uses_managed_ai(self, client: TestClient, world: World) -> None:
        response = client.get("/org/ai", headers=owner(world))
        assert response.status_code == 200
        assert response.json() == {
            "plan": "managed",
            "managed": {"provider": "gemini", "available": True, "monthly_question_limit": 500},
            "own_key": None,
        }


class TestOwnKey:
    def test_saving_a_key_checks_it_and_shows_only_the_last_four(
        self, client: TestClient, world: World, db: DbSession, llm_factory: FakeLLMFactory
    ) -> None:
        response = put_key(client, world)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["own_key"]["provider"] == "gemini"
        assert body["own_key"]["last4"] == "0000"
        assert body["own_key"]["added_by_name"] == "Asha Rao"
        assert body["plan"] == "managed"  # saving a key does not switch on its own
        assert GEMINI_KEY not in response.text
        assert llm_factory.created == [("gemini", GEMINI_KEY)]  # checked with the provider first
        # Encrypted at rest; the audit entry has no key in it.
        raw = db.execute(text("SELECT ciphertext, wrapped_dek FROM secrets")).one()
        assert GEMINI_KEY.encode() not in bytes(raw[0]) + bytes(raw[1])
        [entry] = actions(db, world.org_a)
        assert entry.action == "llm.key_set"
        assert entry.details == {"provider": "gemini", "last4": "0000"}
        assert GEMINI_KEY not in client.get("/org/ai", headers=owner(world)).text

    def test_a_rejected_key_is_not_stored(
        self, client: TestClient, world: World, db: DbSession, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.rejected_keys.add(CLAUDE_KEY)
        response = put_key(client, world, CLAUDE_KEY, "claude")
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "ai_invalid_key"
        assert "Anthropic Claude did not accept this key" in error["fields"]["api_key"]
        assert db.scalars(select(Secret)).all() == []
        assert actions(db, world.org_a) == []

    def test_an_unreachable_provider(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.unreachable = True
        response = put_key(client, world)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ai_unavailable"

    @pytest.mark.parametrize("key", ["short", "AIza with spaces in it 000000", "AIza\n" + "0" * 30])
    def test_obviously_wrong_keys_fail_validation(
        self, client: TestClient, world: World, key: str
    ) -> None:
        response = put_key(client, world, key)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_pasted_whitespace_is_trimmed(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        assert put_key(client, world, f"  {GEMINI_KEY}\n").status_code == 200
        assert llm_factory.created == [("gemini", GEMINI_KEY)]

    def test_at_most_ten_key_checks_an_hour(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.rejected_keys.add(CLAUDE_KEY)
        for _ in range(10):
            assert put_key(client, world, CLAUDE_KEY, "claude").status_code == 422
        response = put_key(client, world)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "too_many_key_checks"
        assert len(llm_factory.created) == 10  # the 11th never reached the provider

    def test_replacing_a_key(self, client: TestClient, world: World, db: DbSession) -> None:
        put_key(client, world)
        response = put_key(client, world, CLAUDE_KEY, "claude")
        assert response.json()["own_key"] == {
            **response.json()["own_key"],
            "provider": "claude",
            "last4": "0000",
        }
        assert len(db.scalars(select(Secret)).all()) == 1
        assert actions(db, world.org_a)[-1].details == {
            "provider": "claude",
            "last4": "0000",
            "replaced": {"provider": "gemini", "last4": "0000"},
        }


class TestPlan:
    def test_switching_to_byok_needs_a_key(self, client: TestClient, world: World) -> None:
        response = client.put("/org/ai/plan", json={"plan": "byok"}, headers=owner(world))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ai_key_missing"

    def test_switching_back_and_forth(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        put_key(client, world)
        response = client.put("/org/ai/plan", json={"plan": "byok"}, headers=owner(world))
        assert response.status_code == 200
        assert response.json()["plan"] == "byok"
        assert client.get("/auth/me", headers=owner(world)).json()["organization"]["plan"] == "byok"
        client.put(
            "/org/ai/plan", json={"plan": "byok"}, headers=owner(world)
        )  # no change: no entry
        client.put("/org/ai/plan", json={"plan": "managed"}, headers=owner(world))
        changes = [e.details for e in actions(db, world.org_a) if e.action == "org.plan_changed"]
        assert changes == [{"from": "managed", "to": "byok"}, {"from": "byok", "to": "managed"}]

    def test_removing_the_key_switches_back_to_managed(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        put_key(client, world)
        client.put("/org/ai/plan", json={"plan": "byok"}, headers=owner(world))
        response = client.delete("/org/ai/key", headers=owner(world))
        assert response.status_code == 204
        body = client.get("/org/ai", headers=owner(world)).json()
        assert body["plan"] == "managed"
        assert body["own_key"] is None
        assert db.scalars(select(Secret)).all() == []
        assert db.scalars(select(LlmKey)).all() == []
        log = actions(db, world.org_a)
        last = {
            e.action: e.details for e in log[-2:]
        }  # same moment: order between them is not fixed
        assert last == {
            "llm.key_removed": {"provider": "gemini", "last4": "0000"},
            "org.plan_changed": {"from": "byok", "to": "managed", "reason": "key_removed"},
        }

    def test_removing_when_there_is_no_key(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        assert client.delete("/org/ai/key", headers=owner(world)).status_code == 204
        assert actions(db, world.org_a) == []


class TestCheck:
    def test_managed_check_uses_the_platform_key_and_is_metered(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        llm_factory: FakeLLMFactory,
        clock: FakeClock,
    ) -> None:
        response = client.post("/org/ai/check", headers=owner(world))
        assert response.status_code == 200, response.text
        assert response.json() == {
            "reply": "OK",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "key_source": "managed",
            "input_tokens": 120,
            "output_tokens": 30,
        }
        assert llm_factory.created == [("gemini", "test-gemini-key-not-real")]
        call = llm_factory.clients[0].calls[0]
        assert call.max_tokens == 3000
        assert "OK" in call.messages[0].text  # type: ignore[union-attr]
        [event] = db.scalars(select(UsageEvent)).all()
        assert (event.kind, event.key_source, event.user_id) == (
            "check",
            "managed",
            world.owner_a.id,
        )
        assert event.created_at == clock.now
        # A check is not a question.
        month = usage.current_month(clock.now, "Asia/Kolkata")
        assert usage.questions_used(db, world.org_a.id, month) == 0

    def test_byok_check_uses_the_workspaces_key(
        self, client: TestClient, world: World, db: DbSession, llm_factory: FakeLLMFactory
    ) -> None:
        put_key(client, world, CLAUDE_KEY, "claude")
        client.put("/org/ai/plan", json={"plan": "byok"}, headers=owner(world))
        response = client.post("/org/ai/check", headers=owner(world))
        assert response.json()["key_source"] == "byok"
        assert response.json()["provider"] == "claude"
        assert llm_factory.created[-1] == ("claude", CLAUDE_KEY)
        assert db.scalars(select(UsageEvent.key_source)).all() == ["byok"]

    def test_provider_errors_get_a_plain_message(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory, db: DbSession
    ) -> None:
        llm_factory.next_script = [LLMError("quota", "gemini", "429 RESOURCE_EXHAUSTED")]
        response = client.post("/org/ai/check", headers=owner(world))
        assert response.status_code == 502
        assert response.json()["error"] == {
            "code": "ai_quota",
            "message": "The key has used up its Google Gemini quota. It resets with the provider's quota period.",
        }
        assert db.scalars(select(UsageEvent)).all() == []

    def test_at_most_ten_checks_an_hour(
        self, client: TestClient, world: World, db: DbSession, settings: Settings, clock: FakeClock
    ) -> None:
        for _ in range(10):
            assert client.post("/org/ai/check", headers=owner(world)).status_code == 200
        response = client.post("/org/ai/check", headers=owner(world))
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "too_many_checks"
        clock.advance(hours=1)
        fresh = token_for(db, settings, clock.now, world.owner_a, world.org_a.id)
        response = client.post("/org/ai/check", headers={"Authorization": f"Bearer {fresh}"})
        assert response.status_code == 200

    def test_a_check_still_works_when_the_monthly_limit_is_used_up(
        self, client: TestClient, world: World, db: DbSession, clock: FakeClock
    ) -> None:
        org = db.get(Organization, world.org_a.id)
        assert org is not None
        org.monthly_question_limit = 0
        db.commit()
        assert client.post("/org/ai/check", headers=owner(world)).status_code == 200


@pytest.fixture
def unconfigured_client(
    settings: Settings,
    session_factory: sessionmaker[DbSession],
    fake_redis: object,
    outbox: object,
    clock: FakeClock,
    llm_factory: FakeLLMFactory,
) -> Iterator[TestClient]:
    app = create_app(
        settings.model_copy(update={"gemini_api_key": SecretStr("")}),
        readiness_checks={},
        session_factory=session_factory,
        redis_client=fake_redis,  # type: ignore[arg-type]
        email_sender=outbox,  # type: ignore[arg-type]
        clock=clock,
        llm_factory=llm_factory,
    )
    with TestClient(app, base_url="http://localhost:8000") as test_client:
        yield test_client


def test_managed_ai_without_a_platform_key(unconfigured_client: TestClient, world: World) -> None:
    body = unconfigured_client.get("/org/ai", headers=owner(world)).json()
    assert body["managed"]["available"] is False
    response = unconfigured_client.post("/org/ai/check", headers=owner(world))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_not_configured"


class TestUsage:
    def _record(self, db: DbSession, world: World, clock: FakeClock, **kwargs: object) -> None:
        values: dict[str, object] = {
            "org_id": world.org_a.id,
            "user_id": world.analyst_a.id,
            "run_id": uuid.uuid4(),
            "kind": "question",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "usage": Usage(10_000, 2_000),
            "key_source": "managed",
            "at": clock.now,
        }
        values.update(kwargs)
        usage.record(db, **values)  # type: ignore[arg-type]

    def test_this_month(
        self, client: TestClient, world: World, db: DbSession, clock: FakeClock
    ) -> None:
        run = uuid.uuid4()
        self._record(db, world, clock, run_id=run)
        self._record(db, world, clock, run_id=run)
        self._record(db, world, clock, kind="briefing", user_id=world.owner_a.id)
        self._record(db, world, clock, at=clock.now - timedelta(days=20))  # 20 October
        self._record(db, world, clock, org_id=world.org_b.id, user_id=world.analyst_b.id)
        db.commit()
        response = client.get("/org/usage", headers=owner(world))
        assert response.status_code == 200
        body = response.json()
        assert (body["month"], body["resets_on"], body["is_current_month"]) == (
            "2026-11",
            "2026-12-01",
            True,
        )
        assert (body["plan"], body["limit"]) == ("managed", 500)
        totals = body["totals"]
        assert (totals["questions"], totals["limited_questions"], totals["briefings"]) == (1, 1, 1)
        assert (totals["input_tokens"], totals["output_tokens"]) == (30_000, 6_000)
        assert totals["cost_usd"] == "0.024000"
        assert {p["email"]: p["questions"] for p in body["people"]} == {
            "ravi@urbancart.in": 1,
            "asha@urbancart.in": 0,
        }
        assert len(body["history"]) == 6
        assert body["history"][-1]["month"] == "2026-11"
        assert body["history"][-2]["questions"] == 1  # October's question
        assert "zoya@chaiwala.in" not in response.text

    def test_another_month(
        self, client: TestClient, world: World, db: DbSession, clock: FakeClock
    ) -> None:
        self._record(db, world, clock, at=clock.now - timedelta(days=40))
        db.commit()
        body = client.get("/org/usage?month=2026-09", headers=owner(world)).json()
        assert (body["month"], body["is_current_month"], body["totals"]["questions"]) == (
            "2026-09",
            False,
            1,
        )

    def test_byok_has_no_platform_limit(self, client: TestClient, world: World) -> None:
        put_key(client, world)
        client.put("/org/ai/plan", json={"plan": "byok"}, headers=owner(world))
        assert client.get("/org/usage", headers=owner(world)).json()["limit"] is None


def test_keys_never_reach_the_logs(
    client: TestClient, world: World, llm_factory: FakeLLMFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    llm_factory.rejected_keys.add(CLAUDE_KEY)
    put_key(client, world)
    put_key(client, world, CLAUDE_KEY, "claude")
    client.put("/org/ai/plan", json={"plan": "byok"}, headers=owner(world))
    llm_factory.next_script = [FakeTurn("OK")]
    client.post("/org/ai/check", headers=owner(world))
    assert GEMINI_KEY not in caplog.text
    assert CLAUDE_KEY not in caplog.text
