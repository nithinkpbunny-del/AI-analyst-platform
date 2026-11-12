"""The business dictionary end to end (BUILD_SPEC 9, R2, R13; M7): profiling, the AI draft with the fake client,
validation, the review and confirm flow, schema-change drafts, access, export and replacing a connection.

Background jobs run at once (``inline_jobs``); the AI is always the fake client.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.dictionary import ai as ai_step
from app.dictionary import service
from app.llm import usage
from app.llm.base import LLMError
from app.llm.fake import FakeLLMFactory
from app.models import AuditLog, DictionaryVersion, Organization, UsageEvent
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world


def orders_csv(rows: int = 40, extra: str = "") -> bytes:
    lines = [f"id,created_at,customer_id,note,email,total_price,total_refunds,currency{extra}"]
    for i in range(rows):
        refund = 50 if i % 7 == 0 else 0
        tail = ",x" if extra else ""
        lines.append(
            f"{1000 + i},2026-01-{1 + i % 28:02d}T10:00:00+05:30,{7000 + i % 5},note-{i},"
            f"p{i}@example.com,{100 + i * 3}.5,{refund},INR{tail}"
        )
    return ("\n".join(lines) + "\n").encode()


CUSTOMERS = (
    "id,city\n" + "\n".join(f"{7000 + i},{'Pune' if i % 2 else 'Delhi'}" for i in range(5)) + "\n"
).encode()

PAYMENTS = (
    "id,entity,amount,currency,status,order_id,method,captured,fee,tax,created_at\n"
    + "\n".join(
        f"pay_{i},payment,{50000 + i * 1000},INR,captured,order_{i},upi,True,{1000 + i},{180 + i},"
        f"{1767225600 + i * 3600}"
        for i in range(30)
    )
    + "\n"
).encode()

TARGETS = (
    "Month,Region,Revenue target (₹),Owner\n"
    "01/07/2026,North,650000,Meera\n01/07/2026,South,675000,Meera\n"
    "01/08/2026,North,650000,Meera\n01/08/2026,South,675000,Meera\n"
).encode()


def answer(**changes: Any) -> str:
    data: dict[str, Any] = {
        "industry": "d2c",
        "tables": {
            "orders": {
                "meaning": "One row per customer order",
                "grain": "order",
                "time_column": "created_at",
                "columns": {
                    "total_price": {
                        "meaning": "Order value incl. tax",
                        "role": "measure",
                        "unit": "INR",
                        "synonyms": ["revenue", "sales"],
                    },
                    "customer_id": {"meaning": "Customer", "role": "key"},
                    "city": {"meaning": "Not a column of orders"},
                },
            },
        },
        "metrics": {
            "revenue": {
                "expr": "SUM(total_price) - SUM(total_refunds)",
                "table": "orders",
                "unit": "INR",
                "good_direction": "up",
                "meaning": "Sales after refunds",
            },
            "orders": {"expr": "COUNT(*)", "table": "orders", "unit": "count"},
            "aov": {"expr": "revenue / orders", "unit": "INR"},
            "broken": {"expr": "SUM(no_such_column)", "table": "orders"},
        },
        "relationships": [{"left": "orders.customer_id", "right": "customers.id"}],
    }
    data.update(changes)
    return json.dumps(data)


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def owner(world: World) -> dict[str, str]:
    return world.headers("owner_a")


def put_file(
    client: TestClient, world: World, name: str, data: bytes, **kwargs: Any
) -> dict[str, Any]:
    response = upload(client, owner(world), name, data, **kwargs)
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


def state(client: TestClient, world: World, who: str = "owner_a") -> dict[str, Any]:
    response = client.get("/dictionary", headers=world.headers(who))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def draft_of(client: TestClient, world: World) -> dict[str, Any]:
    draft = state(client, world)["draft"]
    assert draft is not None
    assert isinstance(draft, dict)
    return draft


def column(content: dict[str, Any], table: str, name: str) -> dict[str, Any]:
    found: dict[str, Any] = content["datasets"][table]["columns"][name]
    return found


def acknowledge(client: TestClient, world: World) -> dict[str, Any]:
    """The Owner checks every flagged item (✓) and saves."""
    content = draft_of(client, world)["content"]
    for entry in content["datasets"].values():
        for item in entry["columns"].values():
            item["needs_review"] = False
    for relationship in content["relationships"]:
        relationship["needs_review"] = False
    response = client.put("/dictionary/draft", json=content, headers=owner(world))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestFirstDraft:
    def test_a_first_upload_drafts_a_dictionary_in_code_even_when_the_ai_says_nothing(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        body = state(client, world)
        assert body["confirmed"] is False and body["active"] is None
        draft = body["draft"]
        assert draft["status"] == "draft" and draft["origin"] == "initial"
        # The default fake answers "OK", which is not JSON: the code draft stands and says so.
        assert draft["notes"]["ai"] == "unreadable"
        content = draft["content"]
        orders = content["datasets"]["orders"]
        assert orders["time_column"] == "created_at"
        price = column(content, "orders", "total_price")
        # The real reader gives no unit for this CSV; the profiler finds it from the currency column.
        assert (price["unit"], price["role"], price["divide_by"]) == ("INR", "measure", None)
        assert price["evidence"] == ["money_name", "currency_column"]
        assert price["needs_review"] is False
        assert column(content, "orders", "id")["role"] == "key"
        assert content["metrics"]["orders_count"]["valid"] is True
        assert content["metrics"]["sum_total_price"]["valid"] is True

    def test_the_ai_describes_the_tables_and_bad_suggestions_are_dropped(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory, db: DbSession
    ) -> None:
        put_file(client, world, "customers.csv", CUSTOMERS)
        llm_factory.next_script = [answer()]
        put_file(client, world, "orders.csv", orders_csv())
        draft = draft_of(client, world)
        content = draft["content"]
        assert draft["notes"]["ai"] == "ok"
        assert content["company"]["industry"] == "d2c"
        assert content["datasets"]["orders"]["meaning"] == "One row per customer order"
        price = column(content, "orders", "total_price")
        assert price["meaning"] == "Order value incl. tax"
        assert price["synonyms"] == ["revenue", "sales"] and price["source"] == "ai"
        assert set(content["metrics"]) >= {"revenue", "orders", "aov"}
        assert content["metrics"]["aov"]["valid"] is True
        assert "broken" not in content["metrics"]
        assert draft["notes"]["dropped_metrics"] == [{"name": "broken", "error": "unknown_column"}]
        joins = {(r["left"], r["right"]): r for r in content["relationships"]}
        assert joins[("orders.customer_id", "customers.id")]["match_rate"] == 1.0
        assert column(content, "orders", "customer_id")["joins"] == ["customers.id"]

    def test_only_the_profile_reaches_the_model_never_rows(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [answer()]
        put_file(client, world, "orders.csv", orders_csv(40))
        prompt = llm_factory.clients[0].calls[0].messages[0].text  # type: ignore[union-attr]
        assert "total_price" in prompt and '"rows":40' in prompt
        assert "note-0" in prompt and "note-39" not in prompt  # five samples, not the rows
        assert "@example.com" not in prompt  # personal columns carry no samples at all
        assert "p1@" not in prompt
        assert len(llm_factory.clients[0].calls) == 1

    def test_the_call_is_metered_but_is_not_a_question(
        self,
        client: TestClient,
        world: World,
        llm_factory: FakeLLMFactory,
        db: DbSession,
        clock: FakeClock,
    ) -> None:
        llm_factory.next_script = [answer()]
        put_file(client, world, "orders.csv", orders_csv())
        events = list(db.scalars(select(UsageEvent)))
        assert [(e.kind, e.key_source) for e in events] == [("dictionary", "managed")]
        assert events[0].input_tokens > 0
        month = usage.current_month(clock.now, "Asia/Kolkata")
        assert usage.questions_used(db, world.org_a.id, month) == 0

    def test_the_same_structure_needs_no_new_draft_and_no_ai_call(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory, db: DbSession
    ) -> None:
        llm_factory.next_script = [answer()]
        created = put_file(client, world, "orders.csv", orders_csv())
        assert len(llm_factory.created) == 1
        refresh = client.post(f"/connections/{created['id']}/refresh", headers=owner(world))
        assert refresh.status_code == 200
        put_file(client, world, "orders.csv", orders_csv(60), connection_id=created["id"])
        assert len(llm_factory.created) == 1  # no second AI call, nothing re-sent
        assert db.scalar(select(DictionaryVersion.id).where(DictionaryVersion.status == "draft"))
        assert len(list(db.scalars(select(DictionaryVersion)))) == 1

    def test_a_quota_error_is_not_retried_and_the_draft_still_exists(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [LLMError("quota", "gemini", "daily limit", retryable=False)]
        put_file(client, world, "orders.csv", orders_csv())
        assert draft_of(client, world)["notes"]["ai"] == "failed:quota"
        assert len(llm_factory.clients[0].calls) == 1

    def test_an_invalid_key_is_not_retried_either(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [LLMError("invalid_key", "gemini", "bad key")]
        put_file(client, world, "orders.csv", orders_csv())
        assert draft_of(client, world)["notes"]["ai"] == "failed:invalid_key"
        assert len(llm_factory.clients[0].calls) == 1

    def test_without_an_ai_key_the_draft_is_made_in_code(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory, db: DbSession
    ) -> None:
        org = db.get(Organization, world.org_a.id)
        assert org is not None
        org.plan = "byok"  # and no key saved
        db.commit()
        put_file(client, world, "orders.csv", orders_csv())
        assert draft_of(client, world)["notes"]["ai"] == "unavailable:ai_key_missing"
        assert llm_factory.created == []

    def test_a_long_profile_is_sent_in_small_batches(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory, settings: Settings
    ) -> None:
        # Three files make three tables; the default batch is 8 tables, so one call covers them.
        for name, data in (("a.csv", orders_csv()), ("b.csv", CUSTOMERS), ("c.csv", PAYMENTS)):
            put_file(client, world, name, data)
        total_calls = sum(len(c.calls) for c in llm_factory.clients)
        # One call per upload that changed the structure, each describing only what was new.
        assert total_calls == 3
        last = llm_factory.clients[-1].calls[0].messages[0].text  # type: ignore[union-attr]
        assert '"key":"c"' in last and '"key":"a"' not in last


class TestMoneyAndReview:
    def test_razorpay_style_amounts_are_paise_and_wait_for_the_owner(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "razorpay_payments.csv", PAYMENTS)
        content = draft_of(client, world)["content"]
        for name in ("amount", "fee", "tax"):
            item = column(content, "razorpay_payments", name)
            assert (item["unit"], item["divide_by"]) == ("INR", 100), name
            assert (item["needs_review"], item["review_reason"]) == (True, "paise")
            assert "paise_signature" in item["evidence"]
        assert draft_of(client, world)["review_items"] == 3

    def test_confirming_waits_until_every_flagged_item_is_checked(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        put_file(client, world, "razorpay_payments.csv", PAYMENTS)
        refused = client.post("/dictionary/draft/confirm", headers=owner(world))
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "review_pending"
        assert service.active(db, world.org_a.id).note == "dictionary not confirmed"
        acknowledge(client, world)
        confirmed = client.post("/dictionary/draft/confirm", headers=owner(world))
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert (body["version"], body["status"]) == (1, "confirmed")
        assert body["confirmed_by_name"] == "Asha Rao"
        paid = column(body["content"], "razorpay_payments", "amount")
        assert (paid["divide_by"], paid["source"], paid["needs_review"]) == (100, "owner", False)
        db.expire_all()
        active = service.active(db, world.org_a.id)
        assert active.confirmed is True and active.note is None and active.version == 1

    def test_owners_can_say_the_amounts_are_rupees_after_all(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "razorpay_payments.csv", PAYMENTS)
        content = draft_of(client, world)["content"]
        item = column(content, "razorpay_payments", "amount")
        item.update(divide_by=None, needs_review=False)
        saved = client.put("/dictionary/draft", json=content, headers=owner(world)).json()
        assert column(saved["content"], "razorpay_payments", "amount")["divide_by"] is None
        assert saved["review_items"] == 2

    def test_dividing_only_makes_sense_for_rupees(self, client: TestClient, world: World) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        content = draft_of(client, world)["content"]
        item = column(content, "orders", "id")
        item.update(unit=None, divide_by=100)
        response = client.put("/dictionary/draft", json=content, headers=owner(world))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "divide_needs_rupees"

    def test_a_metric_that_does_not_run_blocks_confirming_until_fixed(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        content = draft_of(client, world)["content"]
        content["metrics"]["margin"] = {"expr": "SUM(total_price) - SUM(cost)", "dataset": "orders"}
        saved = client.put("/dictionary/draft", json=content, headers=owner(world)).json()
        margin = saved["content"]["metrics"]["margin"]
        assert margin["valid"] is False and margin["error"].startswith("unknown_column")
        assert margin["source"] == "owner"
        blocked = client.post("/dictionary/draft/confirm", headers=owner(world))
        assert blocked.status_code == 409
        saved["content"]["metrics"]["margin"]["expr"] = "SUM(total_price) - SUM(total_refunds)"
        fixed = client.put("/dictionary/draft", json=saved["content"], headers=owner(world)).json()
        assert fixed["content"]["metrics"]["margin"]["valid"] is True
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200

    def test_the_structure_cannot_be_edited_and_joins_cannot_be_invented(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "customers.csv", CUSTOMERS)
        put_file(client, world, "orders.csv", orders_csv())
        content = draft_of(client, world)["content"]
        content["relationships"].append({"left": "orders.note", "right": "customers.city"})
        response = client.put("/dictionary/draft", json=content, headers=owner(world))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unknown_join"
        content["relationships"].pop()
        content["datasets"]["orders"]["columns"]["total_price"]["type"] = "text"
        content["datasets"]["orders"]["name"] = "renamed"
        saved = client.put("/dictionary/draft", json=content, headers=owner(world)).json()
        assert column(saved["content"], "orders", "total_price")["type"] == "number"
        assert saved["content"]["datasets"]["orders"]["name"] == "orders"

    def test_owners_can_remove_a_join(self, client: TestClient, world: World) -> None:
        put_file(client, world, "customers.csv", CUSTOMERS)
        put_file(client, world, "orders.csv", orders_csv())
        content = draft_of(client, world)["content"]
        assert content["relationships"]
        content["relationships"] = []
        saved = client.put("/dictionary/draft", json=content, headers=owner(world)).json()
        assert saved["content"]["relationships"] == []
        assert column(saved["content"], "orders", "customer_id")["joins"] == []

    def test_other_validation_errors(self, client: TestClient, world: World) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        assert (
            client.put("/dictionary/draft", json={"datasets": 1}, headers=owner(world)).status_code
            == 422
        )
        content = draft_of(client, world)["content"]
        content["metrics"]["x"] = {"expr": "COUNT(*)", "dataset": "elsewhere"}
        response = client.put("/dictionary/draft", json=content, headers=owner(world))
        assert response.json()["error"]["code"] == "unknown_table"


class TestVersionsAndChanges:
    def confirmed(self, client: TestClient, world: World) -> dict[str, Any]:
        created = put_file(client, world, "orders.csv", orders_csv())
        content = draft_of(client, world)["content"]
        column(content, "orders", "total_price")["meaning"] = "Order value incl. tax"
        assert (
            client.put("/dictionary/draft", json=content, headers=owner(world)).status_code == 200
        )
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200
        return created

    def test_a_new_column_makes_a_draft_with_a_diff_and_the_old_meanings_stay(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        created = self.confirmed(client, world)
        assert state(client, world)["draft"] is None
        llm_factory.next_script = [
            json.dumps({"tables": {"orders": {"columns": {"coupon": {"meaning": "Coupon used"}}}}})
        ]
        put_file(
            client,
            world,
            "orders.csv",
            orders_csv(40, extra=",coupon"),
            connection_id=created["id"],
        )
        body = state(client, world)
        assert body["confirmed"] is True and body["active"]["version"] == 1
        draft = body["draft"]
        assert (draft["version"], draft["origin"]) == (2, "schema_change")
        assert {"kind": "column_added", "dataset": "orders", "column": "coupon"} in draft["diff"]
        content = draft["content"]
        assert column(content, "orders", "total_price")["meaning"] == "Order value incl. tax"
        assert column(content, "orders", "coupon")["meaning"] == "Coupon used"
        # Only the new column was described by the model.
        prompt = llm_factory.clients[-1].calls[0].messages[0].text  # type: ignore[union-attr]
        assert "coupon" in prompt and "total_price" not in prompt
        # Answers keep using version 1 until the draft is confirmed.
        assert body["active"]["content"]["datasets"]["orders"]["columns"].get("coupon") is None
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).json()["version"] == 2
        versions = state(client, world)["versions"]
        assert [(v["version"], v["status"]) for v in versions] == [
            (2, "confirmed"),
            (1, "superseded"),
        ]

    def test_a_removed_connection_leaves_the_confirmed_dictionary_alone(
        self, client: TestClient, world: World
    ) -> None:
        created = self.confirmed(client, world)
        assert (
            client.delete(f"/connections/{created['id']}", headers=owner(world)).status_code == 204
        )
        body = state(client, world)
        assert "orders" in body["active"]["content"]["datasets"]
        assert body["draft"] is None  # nothing is left to draft from

    def test_a_removed_table_leaves_the_draft_with_a_note_in_the_diff(
        self, client: TestClient, world: World
    ) -> None:
        self.confirmed(client, world)
        other = put_file(client, world, "customers.csv", CUSTOMERS)
        assert state(client, world)["draft"] is not None
        client.post("/dictionary/draft/confirm", headers=owner(world))
        client.delete(f"/connections/{other['id']}", headers=owner(world))
        draft = draft_of(client, world)
        assert "customers" not in draft["content"]["datasets"]
        assert {"kind": "dataset_removed", "dataset": "customers", "columns": 2} in draft["diff"]
        assert "customers" in state(client, world)["active"]["content"]["datasets"]

    def test_removing_the_first_of_two_same_named_tables_moves_its_metrics(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        """The M7 bug (D64): the second source's ``orders`` got a prefixed key; removing the first source must not
        leave metrics pointing at a table that no longer exists."""
        llm_factory.next_script = [answer()]
        first = put_file(client, world, "orders.csv", orders_csv())
        acknowledge(client, world)
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200
        second = put_file(client, world, "orders.csv", orders_csv(30))
        content = draft_of(client, world)["content"]
        prefixed = [k for k, e in content["datasets"].items() if e["connection_id"] == second["id"]]
        assert prefixed and prefixed[0] != "orders"
        acknowledge(client, world)
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200
        # The warning before removal says where the metrics go.
        detail = client.get(f"/connections/{first['id']}", headers=owner(world)).json()
        impact = detail["dictionary_impact"]
        assert impact["moves"] == [
            {"table": "orders", "to": second["name"], "metrics": impact["moves"][0]["metrics"]}
        ]
        assert impact["moves"][0]["metrics"] >= 3 and impact["orphaned_metrics"] == []
        assert client.delete(f"/connections/{first['id']}", headers=owner(world)).status_code == 204
        draft = draft_of(client, world)
        content = draft["content"]
        assert list(content["datasets"]) == ["orders"]
        assert content["datasets"]["orders"]["connection_id"] == second["id"]
        assert content["datasets"]["orders"]["meaning"] == "One row per customer order"
        metrics = content["metrics"]
        assert all(m["dataset"] in (None, "orders") for m in metrics.values())
        assert metrics["revenue"]["dataset"] == "orders" and metrics["revenue"]["valid"] is True
        assert all(m["valid"] is True for m in metrics.values())
        assert any(d["kind"] == "metrics_moved" for d in draft["diff"])
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200

    def test_removing_the_only_table_of_a_metric_flags_it(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [answer()]
        put_file(client, world, "orders.csv", orders_csv())
        other = put_file(client, world, "customers.csv", CUSTOMERS)
        acknowledge(client, world)
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200
        orders_id = next(
            c["id"]
            for c in client.get("/connections", headers=owner(world)).json()["connections"]
            if c["id"] != other["id"]
        )
        impact = client.get(f"/connections/{orders_id}", headers=owner(world)).json()[
            "dictionary_impact"
        ]
        assert impact["moves"] == [] and "revenue" in impact["orphaned_metrics"]
        client.delete(f"/connections/{orders_id}", headers=owner(world))
        draft = draft_of(client, world)
        metrics = draft["content"]["metrics"]
        assert "orders_count" not in metrics  # automatic metrics of the gone table go
        assert metrics["revenue"]["needs_review"] is True
        assert draft["content"]["relationships"] == []
        kinds = {d["kind"] for d in draft["diff"]}
        assert {"metric_orphaned", "join_removed", "dataset_removed"} <= kinds
        # It cannot be confirmed while a metric points at nothing.
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 409
        # The same file uploaded again gives the metric a table again: its flag goes, nothing is left to check.
        put_file(client, world, "orders.csv", orders_csv())
        again = draft_of(client, world)
        assert again["content"]["metrics"]["revenue"]["needs_review"] is False
        assert any(d["kind"] == "metrics_restored" for d in again["diff"])
        assert again["content"]["metrics"]["revenue"]["dataset"] == "orders"

    def test_discarding_the_draft_keeps_the_confirmed_dictionary(
        self, client: TestClient, world: World
    ) -> None:
        created = self.confirmed(client, world)
        put_file(
            client,
            world,
            "orders.csv",
            orders_csv(40, extra=",coupon"),
            connection_id=created["id"],
        )
        assert state(client, world)["draft"] is not None
        assert client.delete("/dictionary/draft", headers=owner(world)).status_code == 204
        body = state(client, world)
        assert body["draft"] is None and body["active"]["version"] == 1
        assert client.delete("/dictionary/draft", headers=owner(world)).status_code == 409

    def test_asking_for_a_new_draft(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        self.confirmed(client, world)
        calls_before = len(llm_factory.created)
        response = client.post("/dictionary/draft", json={}, headers=owner(world))
        assert response.status_code == 202
        body = state(client, world)
        assert body["drafting"] is False  # the inline job has finished
        assert body["draft"] is not None and body["draft"]["origin"] == "redraft"
        assert len(llm_factory.created) == calls_before  # nothing new to describe: no AI call
        again = client.post("/dictionary/draft", json={"fresh_ai": True}, headers=owner(world))
        assert again.status_code == 202
        assert len(llm_factory.created) == calls_before + 1

    def test_a_draft_cannot_be_requested_twice_at_once(
        self, client: TestClient, world: World, fake_redis: Any
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        fake_redis.set(f"{service.DRAFTING_KEY}{world.org_a.id}", "running")
        assert state(client, world)["drafting"] is True
        response = client.post("/dictionary/draft", json={}, headers=owner(world))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_drafting"

    def test_the_same_file_uploaded_again_is_the_same_table_and_costs_no_ai_call(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [answer()]
        created = put_file(client, world, "orders.csv", orders_csv())
        acknowledge(client, world)
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200
        client.delete(f"/connections/{created['id']}", headers=owner(world))
        again = put_file(client, world, "orders.csv", orders_csv())
        assert len(llm_factory.created) == 1  # nothing was sent again
        draft = draft_of(client, world)
        orders = draft["content"]["datasets"]["orders"]
        assert orders["meaning"] == "One row per customer order"  # the work is kept
        assert orders["connection_id"] == again["id"]  # and points at the data that is there now
        assert draft["diff"] == []

    def test_an_answer_already_paid_for_is_never_asked_again(
        self,
        client: TestClient,
        world: World,
        llm_factory: FakeLLMFactory,
        db: DbSession,
        runtime: Any,
    ) -> None:
        payloads = [{"tables": [{"key": "orders"}], "all_table_keys": ["orders"]}]
        digest = ai_step.input_hash(payloads, "v1")
        db.add(
            DictionaryVersion(
                org_id=world.org_a.id,
                version=1,
                status="confirmed",
                origin="initial",
                content={},
                diff_json=[],
                notes_json={},
                ai_input_hash=digest,
                ai_json={"answers": [{"industry": "retail"}]},
            )
        )
        db.commit()
        result = service._run_ai(runtime, llm_factory, world.org_a.id, None, payloads, fresh=False)
        assert (result.status, result.answers) == ("cached", [{"industry": "retail"}])
        assert llm_factory.created == []
        fresh = service._run_ai(runtime, llm_factory, world.org_a.id, None, payloads, fresh=True)
        assert fresh.status == "unreadable" and len(llm_factory.created) == 1  # "OK" is no answer

    def test_the_activity_log_records_the_owners_actions(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        self.confirmed(client, world)
        client.post("/dictionary/draft", json={}, headers=owner(world))
        client.delete("/dictionary/draft", headers=owner(world))
        actions = [
            a.action
            for a in db.scalars(select(AuditLog).where(AuditLog.action.like("dictionary.%")))
        ]
        assert sorted(actions) == [
            "dictionary.confirmed",
            "dictionary.draft_discarded",
            "dictionary.draft_requested",
            "dictionary.edited",
        ]


class TestAccess:
    def test_analysts_read_the_confirmed_dictionary_but_not_the_draft_or_edit(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        analyst = state(client, world, "analyst_a")
        # Nothing confirmed yet: they see the working version, labelled unconfirmed.
        assert analyst["confirmed"] is False and analyst["draft"] is None
        assert analyst["active"]["status"] == "draft" and analyst["versions"] == []
        content = draft_of(client, world)["content"]
        denied = client.put("/dictionary/draft", json=content, headers=world.headers("analyst_a"))
        assert denied.status_code == 403
        assert (
            client.post("/dictionary/draft/confirm", headers=world.headers("analyst_a")).status_code
            == 403
        )
        assert (
            client.post(
                "/dictionary/replace", json={}, headers=world.headers("analyst_a")
            ).status_code
            == 403
        )

    def test_viewers_do_not_see_it(self, client: TestClient, world: World) -> None:
        assert client.get("/dictionary", headers=world.headers("viewer_a")).status_code == 403

    def test_tables_of_connections_a_person_may_not_see_are_left_out(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        payments = put_file(client, world, "razorpay_payments.csv", PAYMENTS)
        members = client.get("/connections/access", headers=owner(world)).json()["people"]
        analyst = next(m for m in members if m["name"] == "Ravi")
        denied = client.put(
            f"/connections/access/{analyst['membership_id']}",
            json={"access": "selected", "connection_ids": [payments["id"]]},
            headers=owner(world),
        )
        assert denied.status_code == 200, denied.text
        acknowledge(client, world)
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200
        shown = state(client, world, "analyst_a")["active"]["content"]
        assert list(shown["datasets"]) == ["razorpay_payments"]
        assert all(m["dataset"] in shown["datasets"] for m in shown["metrics"].values())
        export = client.get("/dictionary/export.yaml", headers=world.headers("analyst_a")).text
        assert "razorpay_payments" in export and "orders" not in export.replace("order_id", "")

    def test_another_workspace_sees_nothing_of_it(self, client: TestClient, world: World) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        theirs = state(client, world, "owner_b")
        assert theirs["active"] is None and theirs["draft"] is None and theirs["versions"] == []
        version_id = draft_of(client, world)["id"]
        response = client.get(
            f"/dictionary/versions/{version_id}", headers=world.headers("owner_b")
        )
        assert response.status_code == 404

    def test_drafts_are_the_owners_working_copy(self, client: TestClient, world: World) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        version_id = draft_of(client, world)["id"]
        assert (
            client.get(f"/dictionary/versions/{version_id}", headers=owner(world)).status_code
            == 200
        )
        response = client.get(
            f"/dictionary/versions/{version_id}", headers=world.headers("analyst_a")
        )
        assert response.status_code == 404


class TestExport:
    def test_yaml_of_the_confirmed_version_or_one_by_id(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        draft_id = draft_of(client, world)["id"]
        as_draft = client.get("/dictionary/export.yaml", headers=owner(world))
        assert as_draft.status_code == 200 and "datasets:" in as_draft.text
        client.post("/dictionary/draft/confirm", headers=owner(world))
        exported = client.get("/dictionary/export.yaml", headers=owner(world))
        assert exported.headers["content-type"].startswith("text/yaml")
        assert 'filename="dictionary-v1.yaml"' in exported.headers["content-disposition"]
        assert "total_price:" in exported.text and "unit: INR" in exported.text
        by_id = client.get(f"/dictionary/export.yaml?version_id={draft_id}", headers=owner(world))
        assert by_id.text == exported.text

    def test_nothing_to_export_yet(self, client: TestClient, world: World) -> None:
        assert client.get("/dictionary/export.yaml", headers=owner(world)).status_code == 404


class TestReplacingAConnection:
    """R2: move to another kind of source and keep the dictionary's mappings (D60)."""

    def make_dictionary(self, client: TestClient, world: World) -> dict[str, Any]:
        created = put_file(client, world, "targets.csv", TARGETS)
        content = draft_of(client, world)["content"]
        column(content, "targets", "revenue_target_inr")["meaning"] = "What we plan to sell"
        column(content, "targets", "revenue_target_inr")["synonyms"] = ["plan"]
        content["metrics"]["planned"] = {
            "expr": "SUM(revenue_target_inr)",
            "dataset": "targets",
            "unit": "INR",
        }
        assert (
            client.put("/dictionary/draft", json=content, headers=owner(world)).status_code == 200
        )
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200
        return created

    def google_connection(self, db: DbSession, runtime: Any, world: World) -> Any:
        from tests.datasources import make_sheets_connection

        return make_sheets_connection(db, runtime, world.org_a.id)

    def test_the_mappings_move_to_a_google_sheet(
        self, client: TestClient, world: World, db: DbSession, runtime: Any
    ) -> None:
        old = self.make_dictionary(client, world)
        new = self.google_connection(db, runtime, world)
        response = client.post(
            "/dictionary/replace",
            json={"from_connection_id": old["id"], "to_connection_id": str(new.id)},
            headers=owner(world),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (
            body["matched"] == [["targets", "UrbanCart targets 2026 · Targets"]]
            and body["removed_old"] is True
        )
        content = body["draft"]["content"]
        assert body["draft"]["origin"] == "replacement"
        targets = content["datasets"]["targets"]
        assert targets["connection_id"] == str(new.id) and targets["name"].endswith("Targets")
        carried = targets["columns"]["revenue_target_inr"]
        assert carried["meaning"] == "What we plan to sell" and carried["synonyms"] == ["plan"]
        assert carried["unit"] == "INR" and carried["source"] == "owner"
        assert content["metrics"]["planned"]["expr"] == "SUM(revenue_target_inr)"
        assert content["metrics"]["planned"]["valid"] is True
        # The old connection is gone; the confirmed dictionary still answers until the Owner confirms.
        assert client.get(f"/connections/{old['id']}", headers=owner(world)).status_code == 404
        assert state(client, world)["active"]["version"] == 1
        diff = {(d["kind"], d.get("column")) for d in body["draft"]["diff"]}
        assert ("column_added", "owner") not in diff  # same columns on both sides
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 200

    def test_the_old_connection_can_stay(
        self, client: TestClient, world: World, db: DbSession, runtime: Any
    ) -> None:
        old = self.make_dictionary(client, world)
        new = self.google_connection(db, runtime, world)
        response = client.post(
            "/dictionary/replace",
            json={
                "from_connection_id": old["id"],
                "to_connection_id": str(new.id),
                "remove_old": False,
            },
            headers=owner(world),
        )
        assert response.status_code == 200 and response.json()["removed_old"] is False
        assert client.get(f"/connections/{old['id']}", headers=owner(world)).status_code == 200

    def test_columns_without_a_match_are_flagged_and_listed(
        self, client: TestClient, world: World
    ) -> None:
        old = self.make_dictionary(client, world)
        other = put_file(
            client,
            world,
            "targets_new.csv",
            b"Month,Region,Revenue target (\xe2\x82\xb9),Channel\n01/07/2026,North,650000,App\n",
        )
        body = client.post(
            "/dictionary/replace",
            json={"from_connection_id": old["id"], "to_connection_id": other["id"]},
            headers=owner(world),
        ).json()
        content = body["draft"]["content"]
        channel = column(content, "targets", "channel")
        assert (channel["needs_review"], channel["review_reason"]) == (True, "new_column")
        diff = {(d["kind"], d.get("column")) for d in body["draft"]["diff"]}
        assert ("column_unmatched", "Owner") in diff
        assert body["draft"]["review_items"] >= 1
        assert client.post("/dictionary/draft/confirm", headers=owner(world)).status_code == 409

    def test_tables_with_no_partner_are_reported(self, client: TestClient, world: World) -> None:
        old = self.make_dictionary(client, world)
        other = put_file(client, world, "customers.csv", CUSTOMERS)
        body = client.post(
            "/dictionary/replace",
            json={"from_connection_id": old["id"], "to_connection_id": other["id"]},
            headers=owner(world),
        ).json()
        assert body["matched"] == [] and body["unmatched"] == ["targets"]

    def test_refusals(self, client: TestClient, world: World, db: DbSession) -> None:
        old = self.make_dictionary(client, world)
        same = client.post(
            "/dictionary/replace",
            json={"from_connection_id": old["id"], "to_connection_id": old["id"]},
            headers=owner(world),
        )
        assert same.status_code == 422
        # A connection of another workspace looks like a missing one.
        from tests.datasources import make_file_connection

        theirs = make_file_connection(db, self.runtime_of(client), world.org_b.id)
        cross = client.post(
            "/dictionary/replace",
            json={"from_connection_id": old["id"], "to_connection_id": str(theirs.id)},
            headers=owner(world),
        )
        assert cross.status_code == 404
        assert client.get(f"/connections/{old['id']}", headers=owner(world)).status_code == 200

    @staticmethod
    def runtime_of(client: TestClient) -> Any:
        return client.app.state.connector_runtime  # type: ignore[attr-defined]

    def test_a_connection_that_has_not_been_read_cannot_take_over(
        self, client: TestClient, world: World, db: DbSession, runtime: Any
    ) -> None:
        from tests.datasources import make_sheets_connection

        old = self.make_dictionary(client, world)
        unread = make_sheets_connection(db, runtime, world.org_a.id, sync=False)
        response = client.post(
            "/dictionary/replace",
            json={"from_connection_id": old["id"], "to_connection_id": str(unread.id)},
            headers=owner(world),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_read"

    def test_there_must_be_a_dictionary_to_move(
        self, client: TestClient, world: World, db: DbSession, runtime: Any
    ) -> None:
        old = put_file(client, world, "targets.csv", TARGETS)
        db.query(DictionaryVersion).delete()
        db.commit()
        new = self.google_connection(db, runtime, world)
        response = client.post(
            "/dictionary/replace",
            json={"from_connection_id": old["id"], "to_connection_id": str(new.id)},
            headers=owner(world),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_dictionary"
