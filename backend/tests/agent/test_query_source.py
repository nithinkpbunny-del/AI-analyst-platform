"""The agent's ``query_source`` tool (M14): push-down to a customer database, with the asking person's access,
read-only, in the database's own dialect. A real PostgreSQL database (test container) and the fake MySQL."""

# ruff: noqa: S608 (the tests write the SQL a model would send)

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session as DbSession

from app.agent.access import access_context, visible_dictionary
from app.agent.provenance import ResultStore
from app.agent.service import pushdown_runner
from app.agent.tools import ToolContext, execute, specs
from app.config import Settings
from app.connectors.registry import ConnectorRuntime
from app.data.cache import DatasetCache
from app.data.workspace import Workspace
from app.db.tenant import scope_to_org
from app.dictionary import service as dictionary_service
from app.llm.base import ToolCall
from app.llm.fake import FakeLLMFactory
from tests.conftest import FakeClock
from tests.customer_db import FakeMySql
from tests.datasources import make_database_connection, make_file_connection
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def context(
    db: DbSession,
    runtime: ConnectorRuntime,
    llm_factory: FakeLLMFactory,
    world: World,
    *,
    who: str = "owner_a",
    pushdown: bool = True,
) -> ToolContext:
    org_id = world.org_a.id
    dictionary_service.run_draft(runtime, llm_factory, org_id=org_id)
    scope_to_org(db, org_id)
    user = getattr(world, who)
    access = access_context(db, org_id, user.id)
    active = dictionary_service.active(db, org_id)
    db.commit()
    assert active.dictionary is not None
    visible = visible_dictionary(active.dictionary, access)
    return ToolContext(
        access=access,
        visible=visible,
        workspace=Workspace(timeout_seconds=10, max_rows=200),
        cache=DatasetCache(runtime, org_id),
        store=ResultStore(),
        today=date(2026, 11, 9),
        pushdown=pushdown_runner(
            runtime,
            org_id=org_id,
            allowed_connections=access.connection_ids,
            timezone=access.timezone,
            timeout_seconds=10,
        )
        if pushdown
        else None,
    )


def call(ctx: ToolContext, sql: str) -> Any:
    return execute(ctx, ToolCall(id=str(uuid.uuid4()), name="query_source", input={"sql": sql}))


def key_of(ctx: ToolContext, name: str) -> str:
    return next(k for k, ref in ctx.visible.tables.items() if ref.name == name)


class TestPostgres:
    def test_a_query_runs_on_the_database_with_provenance(
        self, db: DbSession, runtime: ConnectorRuntime, llm_factory: FakeLLMFactory, world: World
    ) -> None:
        make_database_connection(
            db, runtime, world.org_a.id, tables=("public.orders", "sales.targets")
        )
        ctx = context(db, runtime, llm_factory, world)
        orders, targets = key_of(ctx, "orders"), key_of(ctx, "sales.targets")
        result = call(
            ctx,
            f"SELECT o.region, SUM(o.amount) AS amount, MAX(t.target) AS target FROM {orders} AS o "
            f"LEFT JOIN {targets} AS t ON t.region = o.region GROUP BY o.region ORDER BY o.region",
        )
        assert not result.is_error, result.content
        content = result.content
        assert content["columns"] == ["region", "amount", "target"]
        assert [row[0] for row in content["rows"]] == ["East", "North", "South", "West"]
        rid = content["result_id"]
        provenance = ctx.store.provenance[rid]
        assert (
            provenance.sql[0].startswith('SELECT "o"."region"')
            or 'FROM "public"."orders"' in provenance.sql[0]
        )
        assert '"sales"."targets"' in provenance.sql[0]
        assert {t["table"] for t in provenance.tables} == {orders, targets}
        assert all(
            t["pushdown"] and t["connection_type"] == "sql_database" for t in provenance.tables
        )
        assert not ctx.workspace.has(orders)  # nothing was copied: the database did the work

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM {orders}",
            "UPDATE {orders} SET amount = 0",
            "SELECT * FROM {orders}; DROP TABLE {orders}",
            "SELECT pg_sleep(30)",
            "SELECT * FROM {orders} FOR UPDATE",
            "SELECT * FROM public.secret_payroll",
        ],
    )
    def test_writes_and_other_tables_are_refused(
        self,
        db: DbSession,
        runtime: ConnectorRuntime,
        llm_factory: FakeLLMFactory,
        world: World,
        sql: str,
    ) -> None:
        make_database_connection(db, runtime, world.org_a.id)
        ctx = context(db, runtime, llm_factory, world)
        result = call(ctx, sql.format(orders=key_of(ctx, "orders")))
        assert result.is_error
        assert result.content["error"] in ("unsafe_sql", "unknown_table")

    def test_results_are_capped_and_flagged(
        self, db: DbSession, runtime: ConnectorRuntime, llm_factory: FakeLLMFactory, world: World
    ) -> None:
        make_database_connection(db, runtime, world.org_a.id)
        ctx = context(db, runtime, llm_factory, world)
        result = call(ctx, f"SELECT id FROM {key_of(ctx, 'orders')}")
        assert len(result.content["rows"]) == 200 and result.content["truncated"] is True

    def test_offered_only_with_a_visible_database(
        self, db: DbSession, runtime: ConnectorRuntime, llm_factory: FakeLLMFactory, world: World
    ) -> None:
        make_file_connection(db, runtime, world.org_a.id)
        files_only = context(db, runtime, llm_factory, world)
        assert "query_source" not in [s.name for s in specs(files_only)]
        make_database_connection(db, runtime, world.org_a.id)
        with_db = context(db, runtime, llm_factory, world)
        assert "query_source" in [s.name for s in specs(with_db)]
        assert "query_source" not in [
            s.name for s in specs(context(db, runtime, llm_factory, world, pushdown=False))
        ]

    def test_other_tables_go_to_run_sql(
        self, db: DbSession, runtime: ConnectorRuntime, llm_factory: FakeLLMFactory, world: World
    ) -> None:
        make_file_connection(db, runtime, world.org_a.id)
        make_database_connection(db, runtime, world.org_a.id)
        ctx = context(db, runtime, llm_factory, world)
        file_key = next(k for k, r in ctx.visible.tables.items() if r.connection_type == "file")
        result = call(ctx, f"SELECT COUNT(*) FROM {file_key}")
        assert result.is_error and result.content["error"] == "not_a_database"

    def test_a_person_without_access_to_the_database_cannot_reach_it(
        self, db: DbSession, runtime: ConnectorRuntime, llm_factory: FakeLLMFactory, world: World
    ) -> None:
        connection = make_database_connection(db, runtime, world.org_a.id)
        ctx = context(db, runtime, llm_factory, world)
        assert ctx.pushdown is not None
        stranger = pushdown_runner(
            runtime,
            org_id=world.org_a.id,
            allowed_connections=frozenset(),
            timezone="Asia/Kolkata",
            timeout_seconds=5,
        )
        from app.connectors.base import ConnectorError

        with pytest.raises(ConnectorError):
            stranger(connection.id, 'SELECT 1 FROM "public"."orders"', 5)


class TestMySql:
    def test_the_sql_is_translated_to_mysql(
        self,
        db: DbSession,
        runtime: ConnectorRuntime,
        llm_factory: FakeLLMFactory,
        world: World,
        fake_mysql: FakeMySql,
    ) -> None:
        make_database_connection(
            db,
            runtime,
            world.org_a.id,
            engine="mysql",
            tables=("orders",),
            username="ro_user",
            password="ro-pass",
            name="MySQL (shop)",
        )
        ctx = context(db, runtime, llm_factory, world)
        result = call(
            ctx,
            f"SELECT region, COUNT(*) AS n FROM {key_of(ctx, 'orders')} GROUP BY region ORDER BY region",
        )
        assert not result.is_error, result.content
        assert result.content["rows"] == [["East", 15], ["North", 15], ["South", 15], ["West", 15]]
        assert "FROM `orders`" in fake_mysql.statements[-1]
