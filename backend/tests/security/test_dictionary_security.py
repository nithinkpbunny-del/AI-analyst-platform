"""Security of the business dictionary (BUILD_SPEC 6.3, 6.5, P2, P3, P4; M7): tenant isolation of the new table,
read-only handling of customer data, nothing but the profile sent to the model, and metric formulas that cannot
reach anything outside the sample they run on."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.data.objects import MemoryObjectStore
from app.db.tenant import scope_to_org
from app.dictionary import model, validate
from app.dictionary.model import Dictionary, Metric
from app.llm.fake import FakeLLMFactory
from app.models import Dataset, DictionaryVersion
from tests.api.test_dictionary import PAYMENTS, answer, orders_csv, put_file
from tests.conftest import FakeClock
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


class TestIsolation:
    def test_each_workspace_sees_only_its_own_versions(
        self, client: TestClient, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        for who in ("owner_b",):
            upload = client.post(
                "/connections/files",
                headers=world.headers(who),
                files={"file": ("menu.csv", b"Item,Price\nTea,20\nCoffee,30\n", "text/csv")},
            )
            assert upload.status_code == 201
        for org in (world.org_a, world.org_b):
            with session_factory() as session:
                scope_to_org(session, org.id)
                rows = list(session.scalars(select(DictionaryVersion)))
                assert rows and {r.org_id for r in rows} == {org.id}
                tables = {name for r in rows for name in r.content["datasets"]}
                assert ("orders" in tables) == (org is world.org_a)
                session.rollback()

    def test_the_tenant_role_cannot_write_into_another_workspace(
        self, client: TestClient, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        with session_factory() as session, pytest.raises(DBAPIError):
            scope_to_org(session, world.org_b.id)
            session.add(
                DictionaryVersion(
                    org_id=world.org_a.id,
                    version=9,
                    status="draft",
                    origin="initial",
                    content={},
                    diff_json=[],
                    notes_json={},
                )
            )
            session.flush()

    def test_with_no_workspace_set_nothing_is_visible(
        self, client: TestClient, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        with session_factory() as session:
            session.execute(text("SET LOCAL ROLE app_tenant"))
            assert list(session.scalars(select(DictionaryVersion))) == []
            session.rollback()

    def test_profiles_on_datasets_belong_to_their_workspace(
        self, client: TestClient, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        with session_factory() as session:
            scope_to_org(session, world.org_b.id)
            assert list(session.scalars(select(Dataset))) == []
            session.rollback()
        with session_factory() as session:
            scope_to_org(session, world.org_a.id)
            profile = session.scalars(select(Dataset.profile_json)).one()
            assert profile["row_count"] == 40
            session.rollback()


class TestCustomerDataIsOnlyRead:
    def test_drafting_changes_no_stored_file_or_snapshot(
        self, client: TestClient, world: World, object_store: MemoryObjectStore
    ) -> None:
        put_file(client, world, "razorpay_payments.csv", PAYMENTS)
        before = dict(object_store.objects)
        assert before
        assert (
            client.post(
                "/dictionary/draft", json={"fresh_ai": True}, headers=world.headers("owner_a")
            ).status_code
            == 202
        )
        content = client.get("/dictionary", headers=world.headers("owner_a")).json()["draft"][
            "content"
        ]
        client.put("/dictionary/draft", json=content, headers=world.headers("owner_a"))
        assert dict(object_store.objects) == before

    def test_dividing_paise_works_on_a_copy(self) -> None:
        from app.dictionary.units import to_units

        frame = pd.DataFrame({"amount": [10000, 25000]})
        entry = model.DatasetEntry(
            name="p",
            columns={
                "amount": model.ColumnEntry(
                    label="amount", type="integer", role="measure", unit="INR", divide_by=100
                )
            },
        )
        converted = to_units(frame, entry)
        assert converted["amount"].tolist() == [100.0, 250.0]
        assert frame["amount"].tolist() == [10000, 25000]


class TestWhatTheModelSees:
    def test_only_the_profile_not_rows_and_no_personal_values(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [answer()]
        put_file(client, world, "orders.csv", orders_csv(60))
        sent = llm_factory.clients[0].calls[0]
        text_sent = sent.messages[0].text  # type: ignore[union-attr]
        # Rows of the file: ids beyond the five samples, notes, e-mail addresses never appear.
        # (only the range of an id column, 1000 to 1059, is a profile; its middle is not)
        assert "1030" not in text_sent and "note-59" not in text_sent
        assert "example.com" not in text_sent
        assert sent.tools == []  # no tools: the model cannot ask for data

    def test_secrets_never_reach_the_model_or_the_logs(
        self,
        client: TestClient,
        world: World,
        llm_factory: FakeLLMFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        llm_factory.next_script = [answer()]
        put_file(client, world, "orders.csv", orders_csv())
        prompt = llm_factory.clients[0].calls[0].messages[0].text  # type: ignore[union-attr]
        for secret in ("test-gemini-key-not-real", "test-master-key", "test-jwt-secret"):
            assert secret not in prompt
            assert secret not in caplog.text

    def test_the_fake_client_is_the_only_one_used(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        assert llm_factory.created and all(
            provider == "gemini" for provider, _ in llm_factory.created
        )


class TestMetricFormulasStayInTheSample:
    def dictionary(self, expr: str) -> Dictionary:
        d = Dictionary(
            datasets={
                "t": model.DatasetEntry(
                    name="t",
                    columns={
                        "a": model.ColumnEntry(label="a", type="integer", role="measure"),
                    },
                )
            },
            metrics={"m": Metric(expr=expr, dataset="t")},
        )
        return d

    @pytest.mark.parametrize(
        "expr",
        [
            "SUM(a); ATTACH '/tmp/x.db'",
            "SUM(a) -- comment",
            "SUM(a) /* c */",
            "(SELECT COUNT(*) FROM data)",
            "COUNT(*) FROM read_csv('/etc/passwd')",
            "SUM(a) FROM glob('/*')",
            "COPY data TO '/tmp/out.csv'",
            "SUM(a); INSTALL httpfs",
            "SUM(a); LOAD httpfs",
            "SUM(a); SET enable_external_access = true",
            "SUM(a); PRAGMA database_list",
            "SUM(a); DROP VIEW data",
        ],
    )
    def test_nothing_but_a_plain_formula_runs(self, expr: str) -> None:
        d = self.dictionary(expr)
        validate.check_metrics(d, {"t": pd.DataFrame({"a": [1, 2, 3]})})
        assert d.metrics["m"].valid is False
        assert (d.metrics["m"].error or "").startswith(("unsafe", "syntax", "unknown_column"))

    def test_even_a_formula_that_slips_past_the_filter_cannot_reach_files(self) -> None:
        # Defence in depth: external access is switched off in the engine itself.
        con = validate._connection(pd.DataFrame({"a": [1]}))
        import duckdb

        with pytest.raises(duckdb.Error):
            con.execute("SELECT * FROM read_csv('/etc/hosts')").fetchall()
        with pytest.raises(duckdb.Error):
            con.execute("SET enable_external_access = true")
        con.close()

    def test_an_edited_formula_is_tested_not_trusted(
        self, client: TestClient, world: World
    ) -> None:
        put_file(client, world, "orders.csv", orders_csv())
        content: dict[str, Any] = client.get(
            "/dictionary", headers=world.headers("owner_a")
        ).json()["draft"]["content"]
        content["metrics"]["evil"] = {
            "expr": "COUNT(*); COPY data TO '/tmp/x'",
            "dataset": "orders",
        }
        saved = client.put(
            "/dictionary/draft", json=content, headers=world.headers("owner_a")
        ).json()
        assert saved["content"]["metrics"]["evil"]["valid"] is False
        assert (
            client.post("/dictionary/draft/confirm", headers=world.headers("owner_a")).status_code
            == 409
        )
