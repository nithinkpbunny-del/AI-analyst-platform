"""The PostgreSQL / MySQL connector (M14): the network check, SSL rules, the SQL it writes and checks, read-only
sessions, write-privilege warnings, chunked and incremental reads, push-down. PostgreSQL tests use a real database
in the test container; MySQL tests use a fake that runs the connector's SQL on DuckDB."""

from __future__ import annotations

from typing import Any

import pytest

from app.connectors.base import ConnectorError, DatasetInfo
from app.connectors.sql_database import (
    ColumnSpec,
    ConnectParams,
    DatabaseSetup,
    SqlDatabaseConnector,
    TableSpec,
    check_host,
    check_ssl,
    column_type,
    guard,
    mask_user,
    open_session,
    select_sql,
)
from app.security.sql_guard import UnsafeSql
from tests.customer_db import READER, WRITER, CustomerDb, FakeMySql, database_setup, resolver

PROD = DatabaseSetup(app_env="production", resolver=resolver)
DEV = DatabaseSetup(
    app_env="development", resolver=resolver, dev_allowed_hosts=frozenset({"demo-postgres"})
)


def pg(target: CustomerDb, user: tuple[str, str] = READER, **kw: Any) -> ConnectParams:
    values: dict[str, Any] = {
        "engine": "postgresql",
        "host": "db.example.com",
        "port": 5432,
        "database": target.database,
        "username": user[0],
        "password": user[1],
        "ssl_mode": "require",
    } | kw
    return ConnectParams(**values)


def my(user: tuple[str, str] = ("ro_user", "ro-pass"), **kw: Any) -> ConnectParams:
    values: dict[str, Any] = {
        "engine": "mysql",
        "host": "mysql.example.com",
        "port": 3306,
        "database": "shop",
        "username": user[0],
        "password": user[1],
        "ssl_mode": "require",
    } | kw
    return ConnectParams(**values)


def connector(
    setup: DatabaseSetup,
    params: ConnectParams,
    exposed: list[str] | None = None,
    max_rows: int = 1_000_000,
) -> SqlDatabaseConnector:
    return SqlDatabaseConnector(
        setup=setup,
        params=params,
        exposed=exposed or [],
        timezone="Asia/Kolkata",
        max_rows=max_rows,
    )


# ------------------------------------------------------------------------------------------- network
class TestNetwork:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "127.0.0.1",
            "10.1.2.3",
            "192.168.0.10",
            "172.17.0.2",  # the Docker bridge
            "169.254.169.254",  # cloud metadata
            "100.64.0.1",  # carrier-grade NAT
            "0.0.0.0",  # noqa: S104
            "::1",
            "fe80::1",
            "::ffff:10.0.0.1",
            "internal.example.com",
            "metadata.example.com",
            "docker.example.com",
            "mixed.example.com",  # one public and one private answer
            "db.localhost",
            "postgres.internal",
            "printer.local",
        ],
    )
    def test_private_and_internal_addresses_are_refused_in_production(self, host: str) -> None:
        with pytest.raises(ConnectorError) as raised:
            check_host(PROD, host, 5432)
        assert raised.value.kind == "invalid"
        assert (
            "private or internal" in raised.value.message
            or "could not be found" in raised.value.message
        )

    def test_a_public_name_gives_its_checked_address(self) -> None:
        assert check_host(PROD, "db.example.com", 5432) == "52.66.10.20"
        assert check_host(PROD, "52.66.10.20", 5432) == "52.66.10.20"

    @pytest.mark.parametrize(
        "host", ["", "a b", "db.example.com/x", "user@db.example.com", "-x", "x" * 300]
    )
    def test_malformed_hosts_are_refused(self, host: str) -> None:
        with pytest.raises(ConnectorError):
            check_host(PROD, host, 5432)

    def test_unknown_names_and_bad_ports(self) -> None:
        with pytest.raises(ConnectorError, match="could not be found"):
            check_host(PROD, "nowhere.example.com", 5432)
        with pytest.raises(ConnectorError, match="port"):
            check_host(PROD, "db.example.com", 70000)

    def test_only_the_named_demo_hosts_pass_and_only_in_development(self) -> None:
        dev = DatabaseSetup(
            app_env="development",
            resolver=lambda _h, _p: ["172.18.0.9"],
            dev_allowed_hosts=frozenset({"demo-postgres"}),
        )
        assert check_host(dev, "demo-postgres", 5432) == "172.18.0.9"
        with pytest.raises(ConnectorError):
            check_host(dev, "postgres", 5432)  # the app's own database is not a demo
        with pytest.raises(ConnectorError):
            check_host(dev, "localhost", 5432)
        prod = DatabaseSetup(
            app_env="production",
            resolver=lambda _h, _p: ["172.18.0.9"],
            dev_allowed_hosts=frozenset({"demo-postgres"}),
        )
        with pytest.raises(ConnectorError):
            check_host(prod, "demo-postgres", 5432)

    def test_ssl_is_required_unless_in_development(self) -> None:
        assert check_ssl(PROD, "require") == "require"
        assert check_ssl(PROD, "verify-full") == "verify-full"
        with pytest.raises(ConnectorError, match="only in development"):
            check_ssl(PROD, "disable")
        assert check_ssl(DEV, "disable") == "disable"
        with pytest.raises(ConnectorError):
            check_ssl(PROD, "prefer")

    def test_the_default_setup_keeps_demo_hosts_for_development_only(self) -> None:
        from app.config import Settings
        from app.connectors.sql_database import default_setup

        dev = default_setup(Settings(_env_file=None, app_env="development"))
        assert dev.dev_allowed_hosts == frozenset({"demo-postgres", "demo-mysql"})
        prod = default_setup(
            Settings(_env_file=None, app_env="production", master_key="m" * 32, jwt_secret="j" * 32)
        )
        assert prod.dev_allowed_hosts == frozenset() and not prod.development

    def test_the_user_is_masked(self) -> None:
        assert mask_user("analyst_ro") == "an••••"
        assert mask_user("") == ""


# ---------------------------------------------------------------------------------------------- SQL
SPEC = TableSpec(
    ref="sales.orders",
    schema="sales",
    name="orders",
    kind="table",
    rows_estimate=10,
    columns=(
        ColumnSpec("id", "bigint", "integer"),
        ColumnSpec("Updated At", "timestamp", "datetime"),
        ColumnSpec('we"ird', "text", "text"),
        ColumnSpec("photo", "bytea", None),
    ),
    primary_key="id",
    updated_column="Updated At",
)


class TestSql:
    def test_a_table_read_is_quoted_ordered_and_limited(self) -> None:
        sql = select_sql(SPEC, "postgresql", limit=11)
        assert sql == (
            'SELECT "id", "Updated At", "we""ird" FROM "sales"."orders" ORDER BY "Updated At" LIMIT 11'
        )
        mysql = SPEC.__class__(**{**vars(SPEC), "ref": "orders", "schema": "shop"})
        assert select_sql(mysql, "mysql", since="2026-07-01 10:00:00") == (
            "SELECT `id`, `Updated At`, `we\"ird` FROM `orders` WHERE `Updated At` > '2026-07-01 10:00:00' "
            "ORDER BY `Updated At`"
        )

    def test_a_cursor_cannot_break_out_of_its_literal(self) -> None:
        sql = select_sql(SPEC, "postgresql", since="x'; DROP TABLE orders; --")
        assert "> 'x''; DROP TABLE orders; --'" in sql
        guard(sql, "postgresql", [SPEC])  # still one read-only SELECT

    def test_binary_and_spatial_columns_are_not_read(self) -> None:
        assert "photo" not in select_sql(SPEC, "postgresql")
        assert column_type("bytea") is None and column_type("geometry") is None
        assert column_type("tinyint", "tinyint(1)") == "boolean"
        assert (
            column_type("numeric") == "number"
            and column_type("timestamp with time zone") == "datetime"
        )
        assert column_type("character varying") == "text" and column_type("date") == "date"

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM sales.orders",
            "UPDATE sales.orders SET id = 1",
            "SELECT * FROM sales.orders FOR UPDATE",
            "SELECT pg_sleep(10)",
            "SELECT * FROM sales.orders; DROP TABLE sales.orders",
            "SELECT * FROM public.secret_payroll",  # not exposed
            "SELECT * FROM orders",  # unqualified: the search path could name another table
            "SELECT * FROM pg_catalog.pg_authid",
            "SELECT * FROM other_db.sales.orders",
            "SELECT * INTO copy FROM sales.orders",
            "COPY sales.orders TO '/tmp/x'",
            "SELECT dblink('x', 'y')",
            "SELECT lo_import('/etc/passwd')",
            "SELECT * FROM pg_read_file('/etc/passwd')",
            # Switching the session or transaction back to writing never reaches the database.
            "SET TRANSACTION READ WRITE",
            "SET default_transaction_read_only = off",
            "SELECT set_config('default_transaction_read_only', 'off', false)",
            "BEGIN; UPDATE sales.orders SET id = 1",
        ],
    )
    def test_the_guard_refuses_writes_and_other_tables(self, sql: str) -> None:
        with pytest.raises(UnsafeSql):
            guard(sql, "postgresql", [SPEC])

    def test_the_guard_accepts_reads_of_exposed_tables(self) -> None:
        checked = guard(
            "WITH m AS (SELECT * FROM sales.orders) SELECT COUNT(*) FROM m", "postgresql", [SPEC]
        )
        assert checked.tables == frozenset({"sales.orders"})


# ------------------------------------------------------------------------------------------- MySQL
class TestMySql:
    def setup_for(self, fake: FakeMySql, target: CustomerDb | None = None) -> DatabaseSetup:
        return database_setup(target or CustomerDb("unused", 1, "unused"), fake)

    def test_a_read_only_user_passes_and_a_writer_is_flagged(self) -> None:
        fake = FakeMySql()
        ok = connector(self.setup_for(fake), my()).test()
        assert ok.ok and ok.details["can_write"] is False
        assert "read-only user" in ok.message
        writer = connector(self.setup_for(fake), my(("rw_user", "rw-pass"))).test()
        assert writer.ok and writer.details["can_write"] is True
        assert set(writer.details["write_reasons"]) == {"rows", "tables"}
        assert "can change data" in writer.message
        assert any(c.label == "read_only" and not c.ok for c in writer.checks)

    def test_a_wrong_password_needs_new_credentials(self) -> None:
        result = connector(self.setup_for(FakeMySql()), my(("ro_user", "nope"))).test()
        assert not result.ok and "user name or password" in result.message
        assert any(c.label == "signed_in" and not c.ok for c in result.checks)

    def test_tables_columns_and_a_whole_read(self) -> None:
        fake = FakeMySql()
        c = connector(self.setup_for(fake), my(), ["orders", "stock"])
        assert [t.ref for t in c.available_tables()] == ["orders", "stock"]
        assert [d.ref for d in c.list_datasets()] == ["orders", "stock"]
        result = c.fetch(DatasetInfo("orders", "orders"))
        assert len(result.frame) == 60 and "photo" not in result.frame.columns
        assert (
            result.frame["amount"].dtype == "float64"
            and str(result.frame["paid"].dtype) == "boolean"
        )
        assert result.key_column == "id" and result.cursor == "2026-06-03 21:00:00"
        stock = c.fetch(DatasetInfo("stock", "stock"))
        assert stock.cursor is None and stock.key_column is None and len(stock.frame) == 2

    def test_later_reads_ask_only_for_changed_rows(self) -> None:
        fake = FakeMySql()
        c = connector(self.setup_for(fake), my(), ["orders"])
        first = c.fetch(DatasetInfo("orders", "orders"))
        later = c.fetch(DatasetInfo("orders", "orders"), since_cursor=first.cursor)
        assert (
            later.incremental and len(later.frame) == 1
        )  # the overlap re-reads the newest row only
        assert "WHERE `updated_at` > '2026-06-03 20:55:00'" in fake.statements[-1]

    def test_the_row_cap_stops_a_read(self) -> None:
        c = connector(self.setup_for(FakeMySql()), my(), ["orders"], max_rows=25)
        result = c.fetch(DatasetInfo("orders", "orders"))
        assert len(result.frame) == 25 and result.truncated

    def test_push_down_is_checked_limited_and_read_only(self) -> None:
        fake = FakeMySql()
        c = connector(self.setup_for(fake), my(), ["orders"])
        columns, rows, truncated = c.query(
            "SELECT region, SUM(amount) AS total FROM orders GROUP BY region ORDER BY region",
            max_rows=3,
        )
        assert columns == ["region", "total"] and len(rows) == 3 and truncated
        assert fake.statements[-1].startswith("SELECT * FROM (SELECT")
        assert fake.statements[-1].endswith("LIMIT 4")
        for bad in (
            "DELETE FROM orders",
            "SELECT * FROM stock",
            "SELECT 1; SELECT 2",
            "SELECT sleep(5)",
        ):
            with pytest.raises(UnsafeSql):
                c.query(bad, max_rows=3)
        assert not any(
            s.upper().startswith(("DELETE", "UPDATE", "INSERT")) for s in fake.statements
        )

    def test_the_session_is_set_read_only_with_a_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real MySQL session (with a stand-in for the driver): read-only before anything is read."""
        import pymysql

        sent: list[str] = []

        class Cursor:
            description = (("x",),)

            def __enter__(self) -> Cursor:
                return self

            def __exit__(self, *_a: object) -> None:
                return None

            def execute(self, sql: str) -> None:
                sent.append(sql)

            def fetchall(self) -> list[tuple[int]]:
                return [(1,)]

            def fetchmany(self, _n: int) -> list[tuple[int]]:
                return []

        class Conn:
            def cursor(self, *_a: object) -> Cursor:
                return Cursor()

            def rollback(self) -> None:
                sent.append("ROLLBACK")

            def close(self) -> None:
                sent.append("CLOSE")

        captured: dict[str, Any] = {}

        def connect(**kwargs: Any) -> Conn:
            captured.update(kwargs)
            return Conn()

        monkeypatch.setattr(pymysql, "connect", connect)
        session = open_session(my(address="52.66.10.21"), 30, 10)
        session.rows("SELECT 1")
        session.close()
        assert sent[:2] == [
            "SET SESSION TRANSACTION READ ONLY",
            "SET SESSION MAX_EXECUTION_TIME = 30000",
        ]
        assert sent[2:] == ["START TRANSACTION READ ONLY", "SELECT 1", "ROLLBACK", "CLOSE"]
        assert captured["host"] == "52.66.10.21" and captured["local_infile"] is False
        assert captured["ssl"] is not None and captured["ssl_disabled"] is False
        assert "COMMIT" not in sent


# -------------------------------------------------------------------------------------- PostgreSQL
class TestPostgres:
    def test_a_read_only_user_passes(self, customer_db: CustomerDb) -> None:
        result = connector(database_setup(customer_db), pg(customer_db)).test()
        assert result.ok, result.message
        assert result.details["can_write"] is False and result.details["tables"] == 4
        assert "PostgreSQL" in (result.details["server_version"] or "")

    def test_a_user_who_can_write_is_flagged_but_still_only_reads(
        self, customer_db: CustomerDb
    ) -> None:
        setup = database_setup(customer_db)
        result = connector(setup, pg(customer_db, WRITER)).test()
        assert result.ok and result.details["can_write"] is True
        assert {"rows", "tables"} <= set(result.details["write_reasons"])
        session = setup.open(pg(customer_db, WRITER))
        try:
            for sql in (
                "UPDATE public.orders SET amount = 0",
                "DELETE FROM public.orders",
                "CREATE TABLE public.x (id int)",
            ):
                with pytest.raises(Exception, match=r"read-only|read only"):
                    session.rows(sql)
        finally:
            session.close()

    def test_statements_time_out(self, customer_db: CustomerDb) -> None:
        setup = database_setup(customer_db, statement_timeout_seconds=1)
        session = setup.open(pg(customer_db), 1)
        try:
            with pytest.raises(Exception, match="statement timeout"):
                session.rows("SELECT pg_sleep(3)")
        finally:
            session.close()

    def test_tables_and_views_the_user_may_read(self, customer_db: CustomerDb) -> None:
        tables = connector(database_setup(customer_db), pg(customer_db)).available_tables()
        assert [(t.ref, t.kind) for t in tables] == [
            ("public.monthly", "view"),
            ("public.orders", "table"),
            ("public.visits", "table"),
            ("sales.targets", "table"),
        ]  # secret_payroll is not granted, so it is not listed

    def test_reading_in_chunks_with_types_and_a_cursor(self, customer_db: CustomerDb) -> None:
        setup = database_setup(customer_db, chunk_rows=100)
        c = connector(setup, pg(customer_db), ["public.orders", "sales.targets", "public.visits"])
        assert [d.name for d in c.list_datasets()] == ["orders", "sales.targets", "visits"]
        orders = c.fetch(DatasetInfo("public.orders", "orders"))
        assert len(orders.frame) == 500 and "photo" not in orders.frame.columns
        assert orders.frame["created_at"].iloc[0].isoformat() == "2026-06-01T11:00:00"  # IST, naive
        assert orders.key_column == "id" and orders.cursor == "2026-06-22 00:30:00+00:00"
        visits = c.fetch(DatasetInfo("public.visits", "visits"))
        assert visits.cursor is None and visits.key_column is None and len(visits.frame) == 30
        later = c.fetch(DatasetInfo("public.orders", "orders"), since_cursor=orders.cursor)
        assert later.incremental and len(later.frame) == 1

    def test_push_down_on_the_database(self, customer_db: CustomerDb) -> None:
        c = connector(
            database_setup(customer_db), pg(customer_db), ["public.orders", "sales.targets"]
        )
        columns, rows, truncated = c.query(
            "SELECT o.region, SUM(o.amount) AS amount, MAX(t.target) AS target FROM public.orders AS o "
            "LEFT JOIN sales.targets AS t ON t.region = o.region GROUP BY o.region ORDER BY o.region",
            max_rows=10,
        )
        assert columns == ["region", "amount", "target"] and not truncated
        assert rows[0][0] == "East" and isinstance(rows[0][1], float)
        with pytest.raises(UnsafeSql):
            c.query("SELECT * FROM public.visits", max_rows=10)  # not exposed

    def test_a_table_that_disappeared_is_not_found(self, customer_db: CustomerDb) -> None:
        c = connector(database_setup(customer_db), pg(customer_db), ["public.gone"])
        assert c.list_datasets() == []
        with pytest.raises(ConnectorError) as raised:
            c.fetch(DatasetInfo("public.gone", "gone"))
        assert raised.value.kind == "not_found"

    def test_wrong_password_and_ssl_problems_in_plain_words(self, customer_db: CustomerDb) -> None:
        wrong = connector(database_setup(customer_db), pg(customer_db, (READER[0], "nope"))).test()
        assert not wrong.ok and "user name or password" in wrong.message

        def strict(params: ConnectParams, timeout: int, connect: int) -> Any:
            local = ConnectParams(
                **{
                    **vars(params),
                    "host": customer_db.host,
                    "port": customer_db.port,
                    "address": "",
                }
            )
            return open_session(local, timeout, connect)  # SSL required; the container has none

        refused = connector(
            DatabaseSetup(app_env="production", resolver=resolver, opener=strict), pg(customer_db)
        ).test()
        assert not refused.ok and "encrypted connection" in refused.message

    def test_a_private_address_never_reaches_the_driver(self, customer_db: CustomerDb) -> None:
        opened: list[ConnectParams] = []

        def spy(params: ConnectParams, timeout: int, connect: int) -> Any:
            opened.append(params)
            raise AssertionError("must not connect")

        result = connector(
            DatabaseSetup(app_env="production", resolver=resolver, opener=spy),
            pg(customer_db, host="internal.example.com"),
        ).test()
        assert not result.ok and "private or internal" in result.message and opened == []

    def test_the_checked_address_is_what_the_driver_gets(self, customer_db: CustomerDb) -> None:
        opened: list[ConnectParams] = []
        base = database_setup(customer_db)

        def spy(params: ConnectParams, timeout: int, connect: int) -> Any:
            opened.append(params)
            assert base.opener is not None
            return base.opener(params, timeout, connect)

        setup = database_setup(customer_db, opener=spy)
        connector(setup, pg(customer_db)).available_tables()
        assert opened[0].address == "52.66.10.20" and opened[0].host == "db.example.com"
