"""Customer databases for the M14 tests (no live customer systems).

* **PostgreSQL:** a separate database ``customer_demo`` in the test container, with a read-only role and a role that
  can write. Tests reach it through a "public" host name (``db.example.com``) whose fake DNS answer is a public
  address; the opener then connects to the container (the network check still runs on the public address). SSL is
  off in the container, so the opener turns it off; the SSL rules themselves are unit-tested.
* **MySQL:** ``FakeMySql``, a scripted session: catalog questions (tables, columns, keys, grants) are answered from
  its tables, data queries are translated from MySQL to DuckDB and run on the same rows, so the SQL the connector
  writes really runs. It records every statement and refuses anything that is not a read.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import duckdb
import pandas as pd
import psycopg
import pytest
import sqlglot
from sqlalchemy.engine import make_url

from app.connectors.sql_database import ConnectParams, DatabaseSetup, SqlSession, open_session

READER = ("m14_reader", "reader-pass-0001")
WRITER = ("m14_writer", "writer-pass-0001")
PUBLIC_HOSTS = {
    "db.example.com": ["52.66.10.20"],
    "mysql.example.com": ["52.66.10.21"],
    "internal.example.com": ["10.0.0.5"],
    "metadata.example.com": ["169.254.169.254"],
    "docker.example.com": ["172.18.0.4"],
    "mixed.example.com": ["52.66.10.22", "192.168.1.4"],
}
ORDERS = 500


@dataclass(frozen=True)
class CustomerDb:
    host: str
    port: int
    database: str


def resolver(host: str, port: int) -> list[str]:
    if host in PUBLIC_HOSTS:
        return PUBLIC_HOSTS[host]
    raise OSError(f"unknown host {host}")


def _admin(url: str, database: str | None = None) -> psycopg.Connection[Any]:
    parsed = make_url(url)
    return psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=database or parsed.database,
        autocommit=True,
    )


@pytest.fixture(scope="session")
def customer_db(database_url: str) -> Iterator[CustomerDb]:
    parsed = make_url(database_url)
    with _admin(database_url) as admin:
        admin.execute("DROP DATABASE IF EXISTS customer_demo")
        admin.execute("CREATE DATABASE customer_demo")
        for role, password in (READER, WRITER):
            admin.execute(f"DROP ROLE IF EXISTS {role}")
            admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
    with _admin(database_url, "customer_demo") as conn:
        conn.execute(
            """
            CREATE SCHEMA sales;
            CREATE TABLE public.orders (
                id bigint PRIMARY KEY, created_at timestamptz, updated_at timestamptz, region text,
                amount numeric(12, 2), paid boolean, photo bytea
            );
            INSERT INTO public.orders
            SELECT i, timestamptz '2026-06-01 10:00+05:30' + i * interval '1 hour',
                   timestamptz '2026-06-01 10:00+05:30' + i * interval '1 hour',
                   (ARRAY['North', 'South', 'East', 'West'])[1 + i % 4], 100 + i % 50, i % 3 <> 0, NULL
            FROM generate_series(1, 500) AS i;
            CREATE TABLE public.visits (day date, visitors integer);
            INSERT INTO public.visits SELECT date '2026-06-01' + i, 100 + i FROM generate_series(0, 29) AS i;
            CREATE TABLE sales.targets (id integer PRIMARY KEY, month date, region text, target numeric);
            INSERT INTO sales.targets VALUES (1, '2026-06-01', 'North', 10000), (2, '2026-06-01', 'South', 9000);
            CREATE TABLE public.secret_payroll (id integer PRIMARY KEY, salary numeric);
            CREATE VIEW public.monthly AS
              SELECT date_trunc('month', created_at)::date AS month, region, SUM(amount) AS amount
              FROM public.orders GROUP BY 1, 2;
            """
        )
        reader, writer = READER[0], WRITER[0]
        conn.execute(
            f"""
            REVOKE CREATE ON SCHEMA public FROM PUBLIC;
            GRANT USAGE ON SCHEMA public, sales TO {reader}, {writer};
            GRANT SELECT ON public.orders, public.visits, public.monthly, sales.targets TO {reader};
            GRANT ALL ON ALL TABLES IN SCHEMA public, sales TO {writer};
            GRANT CREATE ON SCHEMA public TO {writer};
            """
        )
    yield CustomerDb(host=str(parsed.host), port=int(parsed.port or 5432), database="customer_demo")
    with _admin(database_url) as admin:
        admin.execute("DROP DATABASE IF EXISTS customer_demo WITH (FORCE)")
        for role, _ in (READER, WRITER):
            admin.execute(f"DROP ROLE IF EXISTS {role}")


def redirect_opener(target: CustomerDb, mysql: FakeMySql | None = None) -> Any:
    """Opens the test container for any PostgreSQL host (after the network check passed), FakeMySql for MySQL."""

    def opener(params: ConnectParams, timeout: int, connect_timeout: int) -> SqlSession:
        if params.engine == "mysql":
            fake = mysql or FakeMySql()
            if (params.username, params.password) not in fake.users:
                raise RuntimeError('(1045, "Access denied for user (using password: YES)")')
            fake.connected_as = params.username
            fake.connections.append(params)
            return fake
        local = ConnectParams(
            engine="postgresql",
            host=target.host,
            port=target.port,
            database=params.database,
            username=params.username,
            password=params.password,
            ssl_mode="disable",
        )
        return open_session(local, timeout, connect_timeout)

    return opener


def database_setup(
    target: CustomerDb, mysql: FakeMySql | None = None, **kwargs: Any
) -> DatabaseSetup:
    values: dict[str, Any] = {
        "app_env": "production",
        "resolver": resolver,
        "opener": redirect_opener(target, mysql),
        "statement_timeout_seconds": 5,
    } | kwargs
    return DatabaseSetup(**values)


# ------------------------------------------------------------------------------------------- fake MySQL
def _mysql_tables() -> dict[str, pd.DataFrame]:
    start = dt.datetime(2026, 6, 1, 10, 0)
    orders = pd.DataFrame(
        {
            "id": range(1, 61),
            "created_at": [start + dt.timedelta(hours=i) for i in range(60)],
            "updated_at": [start + dt.timedelta(hours=i) for i in range(60)],
            "region": [("North", "South", "East", "West")[i % 4] for i in range(60)],
            "amount": [Decimal(100 + i % 7) for i in range(60)],
            "paid": [i % 3 != 0 for i in range(60)],
        }
    )
    stock = pd.DataFrame({"sku": ["A-1", "B-2"], "units": [5, 9]})
    return {"orders": orders, "stock": stock}


MYSQL_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "orders": [
        ("id", "bigint", "bigint"),
        ("created_at", "datetime", "datetime"),
        ("updated_at", "datetime", "datetime"),
        ("region", "varchar", "varchar(16)"),
        ("amount", "decimal", "decimal(12,2)"),
        ("paid", "tinyint", "tinyint(1)"),
        ("photo", "blob", "blob"),
    ],
    "stock": [("sku", "varchar", "varchar(16)"), ("units", "int", "int")],
}
MYSQL_KEYS = {"orders": ["id"], "stock": []}


@dataclass
class FakeMySql:
    tables: dict[str, pd.DataFrame] = field(default_factory=_mysql_tables)
    users: dict[tuple[str, str], list[str]] = field(
        default_factory=lambda: {
            ("ro_user", "ro-pass"): [
                "GRANT USAGE ON *.* TO `ro_user`@`%`",
                "GRANT SELECT ON `shop`.* TO `ro_user`@`%`",
            ],
            ("rw_user", "rw-pass"): [
                "GRANT USAGE ON *.* TO `rw_user`@`%`",
                "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE ON `shop`.* TO `rw_user`@`%`",
            ],
        }
    )
    statements: list[str] = field(default_factory=list)
    connections: list[ConnectParams] = field(default_factory=list)
    connected_as: str = ""
    closed: int = 0

    def _answer(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        self.statements.append(sql)
        text = sql.strip()
        upper = text.upper()
        if not upper.startswith(("SELECT", "SHOW GRANTS", "WITH")):
            raise RuntimeError("(1792, 'Cannot execute statement in a READ ONLY transaction.')")
        if upper.startswith("SHOW GRANTS"):
            grants = next(g for (user, _), g in self.users.items() if user == self.connected_as)
            return ["grants"], [(g,) for g in grants]
        if "INFORMATION_SCHEMA.TABLES" in upper:
            return ["s", "n", "t", "r"], [
                ("shop", name, "BASE TABLE", len(frame))
                for name, frame in sorted(self.tables.items())
            ]
        named = re.search(r"table_name = '([^']+)'", text)
        if "INFORMATION_SCHEMA.COLUMNS" in upper and named:
            return ["c", "t", "ct"], list(MYSQL_COLUMNS.get(named.group(1), []))
        if "KEY_COLUMN_USAGE" in upper and named:
            return ["c"], [(k,) for k in MYSQL_KEYS.get(named.group(1), [])]
        if upper.startswith("SELECT VERSION()"):
            return ["v"], [("8.4.0-fake",)]
        translated = sqlglot.transpile(text, read="mysql", write="duckdb")[0]
        con = duckdb.connect()
        try:
            for name, frame in self.tables.items():
                con.register(name, frame)
            cursor = con.execute(translated)
            names = [d[0] for d in cursor.description or []]
            return names, [tuple(row) for row in cursor.fetchall()]
        finally:
            con.close()

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        return self._answer(sql)[1]

    def stream(
        self, sql: str, chunk: int
    ) -> Generator[tuple[list[str], list[tuple[Any, ...]]], None, None]:
        names, rows = self._answer(sql)
        for start in range(0, len(rows), chunk):
            yield names, rows[start : start + chunk]

    def close(self) -> None:
        self.closed += 1
