"""The PostgreSQL / MySQL connector (BUILD_SPEC 8.7): the customer's own database, strictly read-only.

Read-only in several layers (BUILD_SPEC 6.3 item 3, DECISIONS D100):

1. **The account:** setup asks for a dedicated read-only database user; the connection check lists what the user
   could change (rows, tables, roles) and the app warns when it can write.
2. **The session:** every connection is opened read-only (PostgreSQL: ``default_transaction_read_only=on`` in the
   startup options, ``SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`` and read-only transactions; MySQL:
   ``SET SESSION TRANSACTION READ ONLY`` and ``START TRANSACTION READ ONLY``) with a statement timeout (and a lock
   timeout on PostgreSQL). Every transaction is rolled back, never committed.
3. **The SQL:** the table reads this module builds and the agent's push-down queries pass
   ``app.security.sql_guard.check_select`` in the source's dialect: one SELECT, no writes, no locking clauses, no
   dangerous functions, only the tables the Owner exposed. Push-down results are wrapped in an outer ``LIMIT``. The
   only other statements are fixed catalog reads written in this module (tables, columns, keys, grants).
4. **Rows:** reads stream in chunks up to ``MAX_ROWS_PER_DATASET`` (and ``rows_to_llm`` for push-down).

Network safety (D101): the host is resolved before connecting and refused when any address is not public
(loopback, private 10/8, 172.16/12, 192.168/16, link-local 169.254/16 incl. cloud metadata, CGNAT, the Docker
network, IPv6 equivalents). PostgreSQL then connects to the checked address (``hostaddr``), so a second DNS
answer cannot redirect it. In development only, the demo database hosts in ``DB_DEV_ALLOWED_HOSTS`` are allowed.
SSL is required by default (``require``: encrypted; ``verify-full``: certificate and host checked); it can be
turned off only in development.

Credentials: host, port, database and user are configuration (shown with the user masked); the password is
envelope-encrypted in ``secrets``. Connection details are fields, not a URL, so no driver option can be smuggled
in through a query string.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import logging
import re
import socket
import ssl
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

import pandas as pd
from sqlglot import exp

from app.config import Settings
from app.connectors.base import (
    ConnectionTestResult,
    ConnectorError,
    DatasetInfo,
    FetchResult,
    TestCheck,
)
from app.connectors.tabular import ColumnInfo, ColumnType
from app.security import sql_guard

logger = logging.getLogger(__name__)

Engine = Literal["postgresql", "mysql"]
SslMode = Literal["verify-full", "require", "disable"]
ENGINES: tuple[Engine, ...] = ("postgresql", "mysql")
DIALECTS: dict[str, str] = {"postgresql": "postgres", "mysql": "mysql"}
DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306}
MAX_TABLES_LISTED = 500
MAX_TABLES_EXPOSED = 100
OVERLAP = dt.timedelta(minutes=5)
HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*\.?$")
NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
# Columns whose name says "this row was last changed at" (incremental reads, BUILD_SPEC R1).
UPDATED = re.compile(
    r"^(updated_?at|updated_?on|modified_?at|modified_?on|last_?modified(_?at)?|last_?updated(_?at)?|"
    r"update_?time|modify_?time|date_?modified|changed_?at|updatedat|lastupdated)$",
    re.IGNORECASE,
)
PRIVATE_MESSAGE = (
    "This address is private or internal (for example localhost, 10.x, 192.168.x or 169.254.x). Connect a "
    "database the internet can reach, through a firewall rule that allows this platform."
)
REAUTH_MESSAGE = (
    "The database did not accept the user name or password. Enter the credentials again."
)


# ------------------------------------------------------------------------------------------- set-up
@dataclass(frozen=True)
class ConnectParams:
    engine: Engine
    host: str
    port: int
    database: str
    username: str
    password: str
    ssl_mode: SslMode
    address: str = ""  # the checked IP address to connect to


class SqlSession(Protocol):
    """One read-only database session (a real driver connection, or a fake in tests)."""

    def rows(self, sql: str) -> list[tuple[Any, ...]]: ...

    def stream(
        self, sql: str, chunk: int
    ) -> Generator[tuple[list[str], list[tuple[Any, ...]]], None, None]: ...

    def close(self) -> None: ...


Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


@dataclass(frozen=True)
class DatabaseSetup:
    app_env: str = "production"
    # Development only: host names of the local demo databases that may be private addresses.
    dev_allowed_hosts: frozenset[str] = frozenset()
    connect_timeout_seconds: int = 10
    statement_timeout_seconds: int = 30
    chunk_rows: int = 10_000
    resolver: Resolver = system_resolver
    # Opens a session; tests replace it (fake MySQL) and real use goes to the drivers.
    opener: Callable[[ConnectParams, int, int], SqlSession] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def development(self) -> bool:
        return self.app_env == "development"

    def open(self, params: ConnectParams, timeout_seconds: int | None = None) -> SqlSession:
        timeout = timeout_seconds or self.statement_timeout_seconds
        opener = self.opener or open_session
        return opener(params, timeout, self.connect_timeout_seconds)


def default_setup(settings: Settings) -> DatabaseSetup:
    hosts = frozenset(
        h.strip().lower() for h in settings.db_dev_allowed_hosts.split(",") if h.strip()
    )
    return DatabaseSetup(
        app_env=settings.app_env,
        dev_allowed_hosts=hosts if settings.app_env == "development" else frozenset(),
        connect_timeout_seconds=settings.db_connect_timeout_seconds,
        statement_timeout_seconds=settings.source_query_timeout_seconds,
        chunk_rows=settings.db_fetch_chunk_rows,
    )


# ------------------------------------------------------------------------------------ the network check
def _blocked(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def check_host(setup: DatabaseSetup, host: str, port: int) -> str:
    """The address to connect to, or a refusal: only public addresses, except the development demo databases."""
    name = host.strip().rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        literal = None
    if literal is None and not HOST.match(name):
        raise ConnectorError("invalid", "Enter the database's host name or IP address.")
    if not 1 <= port <= 65535:
        raise ConnectorError("invalid", "Enter a port between 1 and 65535.")
    local_name = literal is None and (
        name == "localhost" or name.endswith((".localhost", ".local", ".internal", ".localdomain"))
    )
    if local_name and not (setup.development and name in setup.dev_allowed_hosts):
        raise ConnectorError("invalid", PRIVATE_MESSAGE, detail=f"local name {name}")
    try:
        addresses = [str(literal)] if literal is not None else setup.resolver(name, port)
    except (OSError, UnicodeError) as error:
        raise ConnectorError(
            "invalid",
            f"The host {host} could not be found. Check the address.",
            detail=type(error).__name__,
        ) from error
    if not addresses:
        raise ConnectorError("invalid", f"The host {host} could not be found. Check the address.")
    if setup.development and name in setup.dev_allowed_hosts:
        return addresses[0]  # the local demo databases (development only)
    if any(_blocked(a) for a in addresses):
        raise ConnectorError("invalid", PRIVATE_MESSAGE, detail=f"blocked address for {name}")
    return addresses[0]


def check_ssl(setup: DatabaseSetup, mode: str) -> SslMode:
    if mode not in ("verify-full", "require", "disable"):
        raise ConnectorError("invalid", "Choose how the connection is encrypted.")
    if mode == "disable" and not setup.development:
        raise ConnectorError(
            "invalid",
            "Connections without SSL are allowed only in development. Turn on SSL on the database server.",
        )
    return mode  # type: ignore[return-value]


# ----------------------------------------------------------------------------------------- the drivers
def _ms(seconds: int) -> int:
    return max(1, seconds) * 1000


class _PostgresSession:
    def __init__(self, params: ConnectParams, timeout: int, connect_timeout: int) -> None:
        import psycopg

        options = (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={_ms(timeout)} "
            "-c lock_timeout=5000 "
            f"-c idle_in_transaction_session_timeout={_ms(timeout * 2)}"
        )
        kwargs: dict[str, Any] = {
            "host": params.host,
            "port": params.port,
            "dbname": params.database,
            "user": params.username,
            "password": params.password,
            "sslmode": params.ssl_mode,
            "connect_timeout": connect_timeout,
            "options": options,
            "application_name": "analyst-readonly",
            "autocommit": True,
        }
        if params.address:
            kwargs["hostaddr"] = (
                params.address
            )  # connect to the checked address (no second DNS answer)
        if params.ssl_mode == "verify-full":
            kwargs["sslrootcert"] = "system"
        self._conn = psycopg.connect(**kwargs)
        self._conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        self._conn.autocommit = False
        self._conn.read_only = True

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql.encode())
                return list(cursor.fetchall()) if cursor.description else []
        finally:
            self._conn.rollback()

    def stream(
        self, sql: str, chunk: int
    ) -> Generator[tuple[list[str], list[tuple[Any, ...]]], None, None]:
        try:
            with self._conn.cursor(name="analyst_read") as cursor:
                cursor.itersize = chunk
                cursor.execute(sql.encode())
                names = [d.name for d in cursor.description or []]
                while batch := cursor.fetchmany(chunk):
                    yield names, list(batch)
        finally:
            self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


class _MySqlSession:
    def __init__(self, params: ConnectParams, timeout: int, connect_timeout: int) -> None:
        import pymysql
        import pymysql.cursors

        context: ssl.SSLContext | None = None
        if params.ssl_mode == "verify-full":
            context = ssl.create_default_context()
        elif params.ssl_mode == "require":
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = (
                ssl.CERT_NONE
            )  # encrypted; the certificate is not checked ("require")
        self._sscursor = pymysql.cursors.SSCursor
        self._conn = pymysql.connect(
            # verify-full checks the certificate against the host name; otherwise the checked address is used.
            host=params.host
            if params.ssl_mode == "verify-full"
            else (params.address or params.host),
            port=params.port,
            user=params.username,
            password=params.password,
            database=params.database,
            connect_timeout=connect_timeout,
            read_timeout=timeout + 5,
            write_timeout=timeout + 5,
            ssl=context,
            ssl_disabled=params.ssl_mode == "disable",
            charset="utf8mb4",
            autocommit=False,
            local_infile=False,
        )
        with self._conn.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            try:
                cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {_ms(timeout)}")
            except pymysql.err.MySQLError:  # MariaDB names it differently
                cursor.execute(f"SET SESSION max_statement_time = {max(1, timeout)}")

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(sql)
                return list(cursor.fetchall())
        finally:
            self._conn.rollback()

    def stream(
        self, sql: str, chunk: int
    ) -> Generator[tuple[list[str], list[tuple[Any, ...]]], None, None]:
        try:
            with self._conn.cursor(self._sscursor) as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(sql)
                names = [str(d[0]) for d in cursor.description or []]
                while batch := cursor.fetchmany(chunk):
                    yield names, list(batch)
        finally:
            self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def open_session(params: ConnectParams, timeout: int, connect_timeout: int = 10) -> SqlSession:
    if params.engine == "postgresql":
        return _PostgresSession(params, timeout, connect_timeout)
    return _MySqlSession(params, timeout, connect_timeout)


def _driver_error(error: Exception, engine: str) -> ConnectorError:
    """A driver error in plain words; the message never contains the password."""
    text = str(error).lower()
    name = type(error).__name__
    label = "PostgreSQL" if engine == "postgresql" else "MySQL"
    if "password" in text or "authentication" in text or "access denied" in text or "28p01" in text:
        return ConnectorError("needs_reauth", REAUTH_MESSAGE, detail=name)
    if "ssl" in text or "tls" in text or "certificate" in text:
        return ConnectorError(
            "invalid",
            f"{label} did not accept an encrypted connection as configured. Turn on SSL on the server, or "
            "choose 'Encrypted, certificate not checked' if its certificate is self-signed.",
            detail=name,
        )
    if "does not exist" in text or "unknown database" in text:
        return ConnectorError(
            "invalid", "The database name was not found on the server.", detail=name
        )
    if "timeout" in text or "timed out" in text or "canceling statement" in text:
        return ConnectorError(
            "unavailable",
            "The database took too long to answer. Try again, or ask a smaller question.",
            detail=name,
        )
    if "read-only" in text or "read only" in text:
        return ConnectorError(
            "invalid", "The database refused a change: this platform only reads.", detail=name
        )
    return ConnectorError(
        "unavailable",
        f"{label} could not be reached. Check the host, port and firewall, then try again.",
        detail=name,
    )


# ------------------------------------------------------------------------------------- tables and SQL
@dataclass(frozen=True)
class ColumnSpec:
    name: str
    data_type: str  # as the database names it, lower case
    type: ColumnType | None  # None: not read (binary, spatial)


@dataclass(frozen=True)
class TableSpec:
    ref: str  # "schema.table" (PostgreSQL) or "table" (MySQL; the schema is the database)
    schema: str
    name: str
    kind: Literal["table", "view"]
    rows_estimate: int | None
    columns: tuple[ColumnSpec, ...] = ()
    primary_key: str | None = None
    updated_column: str | None = None

    @property
    def incremental(self) -> bool:
        return bool(self.primary_key and self.updated_column)


_SKIPPED = re.compile(
    r"(bytea|blob|binary|varbinary|geometry|geography|point|polygon|linestring|bit varying|tsvector|"
    r"raster|xml)"
)


def column_type(data_type: str, column_type_text: str = "") -> ColumnType | None:
    t = data_type.lower()
    if _SKIPPED.search(t):
        return None
    if column_type_text.lower().startswith("tinyint(1)") or t in ("boolean", "bool", "bit"):
        return "boolean"
    if t in (
        "smallint",
        "integer",
        "int",
        "bigint",
        "mediumint",
        "tinyint",
        "int2",
        "int4",
        "int8",
        "year",
    ):
        return "integer"
    if t in (
        "numeric",
        "decimal",
        "real",
        "double precision",
        "double",
        "float",
        "float4",
        "float8",
        "money",
    ):
        return "number"
    if t == "date":
        return "date"
    if t.startswith("timestamp") or t == "datetime":
        return "datetime"
    return "text"


def quote(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def table_expression(spec: TableSpec, engine: str) -> exp.Table:
    if engine == "postgresql":
        return exp.Table(this=quote(spec.name), db=quote(spec.schema))
    return exp.Table(this=quote(spec.name))


def select_sql(
    spec: TableSpec, engine: str, *, since: str | None = None, limit: int | None = None
) -> str:
    """The read of one exposed table, built by code and then checked like any other SQL."""
    dialect = DIALECTS[engine]
    columns = [exp.Column(this=quote(c.name)) for c in spec.columns if c.type is not None]
    if not columns:
        raise ConnectorError(
            "invalid", f"The table {spec.name} has no columns this platform can read."
        )
    query = exp.select(*columns).from_(table_expression(spec, engine))
    if spec.updated_column:
        updated = exp.Column(this=quote(spec.updated_column))
        if since is not None:
            query = query.where(exp.GT(this=updated, expression=exp.Literal.string(since)))
        query = query.order_by(updated.copy())
    if limit is not None:
        query = query.limit(limit)
    sql = query.sql(dialect=dialect)
    return guard(sql, engine, [spec]).sql


def guard(sql: str, engine: str, exposed: Sequence[TableSpec]) -> sql_guard.CheckedSql:
    """Layer 3: one read-only SELECT over the exposed tables only, in the source's dialect."""
    allowed = [
        f"{t.schema}.{t.name}".lower() if engine == "postgresql" else t.name.lower()
        for t in exposed
    ]
    return sql_guard.check_select(
        sql, dialect=DIALECTS[engine], allowed_tables=allowed, qualified=engine == "postgresql"
    )


def plain(value: Any) -> Any:
    """A database value as JSON-friendly Python (push-down results for the agent)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return None
    if isinstance(value, dict | list):
        return json.dumps(value, default=str)[:500]
    return value


def _series(values: list[Any], kind: ColumnType, timezone: str) -> pd.Series:
    if kind == "datetime":
        aware = any(isinstance(v, dt.datetime) and v.tzinfo is not None for v in values)
        if aware:
            parsed = pd.to_datetime(pd.Series(values, dtype="object"), utc=True, errors="coerce")
            return parsed.dt.tz_convert(timezone).dt.tz_localize(None)
        return pd.to_datetime(pd.Series(values, dtype="object"), errors="coerce")
    if kind == "date":
        return pd.to_datetime(pd.Series(values, dtype="object"), errors="coerce")
    if kind == "integer":
        return (
            pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce")
            .round()
            .astype("Int64")
        )
    if kind == "number":
        numbers = [float(v) if isinstance(v, Decimal) else v for v in values]
        return pd.to_numeric(pd.Series(numbers, dtype="object"), errors="coerce").astype("float64")
    if kind == "boolean":
        return (
            pd.Series(values, dtype="object")
            .map(lambda v: None if v is None else bool(v))
            .astype("boolean")
        )
    return pd.Series(
        [
            None
            if v is None
            else (json.dumps(v, default=str) if isinstance(v, dict | list) else str(v))
            for v in values
        ],
        dtype="string",
    )


def _cursor_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    return str(value)


# ------------------------------------------------------------------------------------------ connector
@dataclass
class WriteCheck:
    can_write: bool
    reasons: list[str]  # "rows", "tables", "admin"


class SqlDatabaseConnector:
    type = "sql_database"

    def __init__(
        self,
        *,
        setup: DatabaseSetup,
        params: ConnectParams,
        exposed: Sequence[str],
        timezone: str,
        max_rows: int,
        timeout_seconds: int | None = None,
    ) -> None:
        if params.engine not in ENGINES:
            raise ConnectorError("invalid", "Choose PostgreSQL or MySQL.")
        self._setup = setup
        self._params = params
        self.engine: Engine = params.engine
        self._exposed = list(exposed)
        self._timezone = timezone
        self._max_rows = max_rows
        self._timeout = timeout_seconds or setup.statement_timeout_seconds
        self._session: SqlSession | None = None
        self._tables: dict[str, TableSpec] | None = None

    # ------------------------------------------------------------------------------------ session
    def _open(self) -> SqlSession:
        if self._session is None:
            check_ssl(self._setup, self._params.ssl_mode)
            address = check_host(self._setup, self._params.host, self._params.port)
            params = ConnectParams(**{**vars(self._params), "address": address})
            try:
                self._session = self._setup.open(params, self._timeout)
            except ConnectorError:
                raise
            except Exception as error:
                raise _driver_error(error, self.engine) from error
        return self._session

    def _rows(self, sql: str) -> list[tuple[Any, ...]]:
        session = self._open()
        try:
            return session.rows(sql)
        except ConnectorError:
            raise
        except Exception as error:
            raise _driver_error(error, self.engine) from error

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None

    # ------------------------------------------------------------------------------------ discovery
    def available_tables(self) -> list[TableSpec]:
        """Tables and views the user may read (system schemas left out), without their columns."""
        if self.engine == "postgresql":
            sql = (
                "SELECT n.nspname, c.relname, c.relkind, GREATEST(c.reltuples, 0)::bigint "  # noqa: S608 (fixed)
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p', 'v', 'm') "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%' "
                "AND has_table_privilege(c.oid, 'SELECT') "
                f"ORDER BY 1, 2 LIMIT {MAX_TABLES_LISTED}"
            )
            out = []
            for schema, name, kind, rows in self._rows(sql):
                ref = f"{schema}.{name}"
                out.append(
                    TableSpec(
                        ref=ref,
                        schema=str(schema),
                        name=str(name),
                        kind="view" if kind in ("v", "m") else "table",
                        rows_estimate=int(rows)
                        if rows is not None and kind in ("r", "p")
                        else None,
                    )
                )
            return out
        sql = (
            "SELECT table_schema, table_name, table_type, table_rows FROM information_schema.tables "  # noqa: S608
            "WHERE table_schema = DATABASE() "
            f"ORDER BY table_name LIMIT {MAX_TABLES_LISTED}"
        )
        return [
            TableSpec(
                ref=str(name),
                schema=str(schema),
                name=str(name),
                kind="view" if "VIEW" in str(kind).upper() else "table",
                rows_estimate=int(rows) if rows is not None else None,
            )
            for schema, name, kind, rows in self._rows(sql)
        ]

    @staticmethod
    def _literal(value: str) -> str:
        """A catalog name as an SQL string. Names with quotes, backslashes or control characters are refused, so
        no dialect's escaping rules matter (they come from the database's own catalog in any case)."""
        if not value or re.search(r"['\"\\\x00-\x1f\x7f]", value):
            raise ConnectorError(
                "invalid", "This table's name has characters this platform cannot read safely."
            )
        return f"'{value}'"

    def describe(self, spec: TableSpec) -> TableSpec:
        """The table's columns (types, primary key, an updated-at column)."""
        schema, name = self._literal(spec.schema), self._literal(spec.name)
        if self.engine == "postgresql":
            columns_sql = (
                "SELECT column_name, data_type, '' FROM information_schema.columns "  # noqa: S608 (_literal)
                f"WHERE table_schema = {schema} AND table_name = {name} ORDER BY ordinal_position"
            )
            key_sql = (
                "SELECT a.attname FROM pg_catalog.pg_index i JOIN pg_catalog.pg_attribute a "  # noqa: S608
                "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                f"WHERE i.indisprimary AND i.indrelid = to_regclass(quote_ident({schema}) || '.' || "
                f"quote_ident({name}))"
            )
        else:
            columns_sql = (
                "SELECT column_name, data_type, column_type FROM information_schema.columns "  # noqa: S608
                f"WHERE table_schema = DATABASE() AND table_name = {name} ORDER BY ordinal_position"
            )
            key_sql = (
                "SELECT column_name FROM information_schema.key_column_usage "  # noqa: S608 (_literal)
                f"WHERE table_schema = DATABASE() AND table_name = {name} AND constraint_name = 'PRIMARY'"
            )
        columns = tuple(
            ColumnSpec(str(c), str(t).lower(), column_type(str(t), str(ct or "")))
            for c, t, ct in self._rows(columns_sql)
        )
        keys = [str(r[0]) for r in self._rows(key_sql)]
        updated = next(
            (c.name for c in columns if c.type in ("datetime", "date") and UPDATED.match(c.name)),
            None,
        )
        return TableSpec(
            ref=spec.ref,
            schema=spec.schema,
            name=spec.name,
            kind=spec.kind,
            rows_estimate=spec.rows_estimate,
            columns=columns,
            primary_key=keys[0] if len(keys) == 1 else None,
            updated_column=updated,
        )

    def _exposed_specs(self) -> dict[str, TableSpec]:
        if self._tables is None:
            listed = {t.ref: t for t in self.available_tables()}
            self._tables = {ref: listed[ref] for ref in self._exposed if ref in listed}
        return self._tables

    def write_check(self) -> WriteCheck:
        """What the user could change (for the warning; the session is read-only either way)."""
        reasons: list[str] = []
        if self.engine == "postgresql":
            row = self._rows(
                "SELECT "
                "COALESCE((SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_catalog.pg_roles "
                "WHERE rolname = current_user), false), "
                "EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
                "ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%' "
                "AND has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')), "
                "has_database_privilege(current_database(), 'CREATE') OR EXISTS (SELECT 1 FROM "
                "pg_catalog.pg_namespace n WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
                "AND n.nspname NOT LIKE 'pg\\_%' AND has_schema_privilege(n.oid, 'CREATE'))"
            )[0]
            admin, rows, tables = (bool(v) for v in row)
        else:
            grants = " ".join(
                str(r[0]) for r in self._rows("SHOW GRANTS FOR CURRENT_USER()")
            ).upper()
            privileges = " ".join(re.findall(r"GRANT (.+?) ON ", grants))
            admin = bool(
                re.search(r"\b(ALL PRIVILEGES|SUPER|CREATE USER|GRANT OPTION|FILE)\b", grants)
            )
            rows = admin or bool(re.search(r"\b(INSERT|UPDATE|DELETE)\b", privileges))
            tables = admin or bool(re.search(r"\b(CREATE|DROP|ALTER|INDEX|TRIGGER)\b", privileges))
        if rows:
            reasons.append("rows")
        if tables:
            reasons.append("tables")
        if admin:
            reasons.append("admin")
        return WriteCheck(can_write=bool(reasons), reasons=reasons)

    def server_version(self) -> str | None:
        try:
            value = self._rows("SELECT version()")[0][0]
        except (ConnectorError, IndexError):
            return None
        return str(value)[:120]

    # --------------------------------------------------------------------------------- the interface
    def test(self) -> ConnectionTestResult:
        checks: list[TestCheck] = []
        try:
            tables = self.available_tables()
        except ConnectorError as error:
            checks.append(TestCheck("signed_in", error.kind != "needs_reauth"))
            return ConnectionTestResult(ok=False, message=error.message, checks=checks)
        checks.append(TestCheck("signed_in", True))
        writes = self.write_check()
        checks.append(TestCheck("read_only", not writes.can_write))
        checks.append(TestCheck("tables_listed", bool(tables)))
        label = "PostgreSQL" if self.engine == "postgresql" else "MySQL"
        if writes.can_write:
            message = (
                f"{label} accepted the user, but this user can change data. This platform only reads (every "
                "session is read-only), but create a read-only user for it (docs/SETUP_DATABASES.md)."
            )
        else:
            message = f"{label} accepted the read-only user. This platform only reads."
        return ConnectionTestResult(
            ok=True,
            message=message,
            checks=checks,
            details={
                "can_write": writes.can_write,
                "write_reasons": writes.reasons,
                "tables": len(tables),
                "server_version": self.server_version(),
            },
        )

    def list_datasets(self) -> list[DatasetInfo]:
        specs = self._exposed_specs()
        return [
            DatasetInfo(ref=ref, name=_dataset_name(spec, self.engine))
            for ref, spec in specs.items()
        ]

    def source_version(self, dataset: DatasetInfo) -> str | None:
        return None

    def supports_incremental(self) -> bool:
        return True

    def supports_pushdown(self) -> bool:
        return True

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        listed = self._exposed_specs().get(dataset.ref)
        if listed is None:
            raise ConnectorError(
                "not_found", "This table is no longer in the database, or the user cannot read it."
            )
        spec = self.describe(listed)
        incremental = bool(spec.incremental and since_cursor)
        since = None
        if incremental and since_cursor:
            since = _minus_overlap(since_cursor)
        sql = select_sql(spec, self.engine, since=since, limit=self._max_rows + 1)
        readable = [c for c in spec.columns if c.type is not None]
        values: dict[str, list[Any]] = {c.name: [] for c in readable}
        count = 0
        truncated = False
        newest: Any = None
        stream = self._open().stream(sql, self._setup.chunk_rows)
        try:
            for names, batch in stream:
                index = {n: i for i, n in enumerate(names)}
                for row in batch:
                    if count >= self._max_rows:
                        truncated = True  # the read asked for one row more than the cap
                        break
                    count += 1
                    for column in readable:
                        values[column.name].append(row[index[column.name]])
                    if spec.updated_column is not None:
                        stamp = row[index[spec.updated_column]]
                        if stamp is not None and (newest is None or stamp > newest):
                            newest = stamp
                if truncated:
                    break
        except ConnectorError:
            raise
        except Exception as error:
            raise _driver_error(error, self.engine) from error
        finally:
            stream.close()
        frame = pd.DataFrame(
            {c.name: _series(values[c.name], c.type or "text", self._timezone) for c in readable}
        )
        infos = [
            ColumnInfo(
                name=c.name,
                label=c.name,
                type=c.type or "text",
                date_format=None,
                null_count=int(frame[c.name].isna().sum()),
            )
            for c in readable
        ]
        cursor = _cursor_text(newest) if spec.updated_column else None
        return FetchResult(
            frame=frame,
            columns=infos,
            source_version=None,
            cursor=cursor or (since_cursor if incremental else None),
            incremental=incremental,
            key_column=spec.primary_key,
            truncated=truncated,
        )

    # ----------------------------------------------------------------------------------- push-down
    def query(self, sql: str, *, max_rows: int) -> tuple[list[str], list[list[Any]], bool]:
        """Run one read-only SELECT over exposed tables (the agent's ``query_source``) and return at most
        ``max_rows`` rows. The SQL is checked, then wrapped in an outer LIMIT, then checked again."""
        exposed = list(self._exposed_specs().values())
        checked = guard(sql, self.engine, exposed)
        dialect = DIALECTS[self.engine]
        inner: exp.Expression = exp.maybe_parse(checked.sql, dialect=dialect)
        wrapped = (
            exp.select(exp.Star())
            .from_(exp.Subquery(this=inner, alias=quote("q")))
            .limit(max_rows + 1)
        ).sql(dialect=dialect)
        final = guard(wrapped, self.engine, exposed).sql
        rows: list[list[Any]] = []
        columns: list[str] = []
        stream = self._open().stream(final, max_rows + 1)
        try:
            for names, batch in stream:
                columns = names
                rows.extend([plain(v) for v in row] for row in batch)
                if len(rows) > max_rows:
                    break
        except ConnectorError:
            raise
        except Exception as error:
            raise _driver_error(error, self.engine) from error
        finally:
            stream.close()
        return columns, rows[:max_rows], len(rows) > max_rows


def _minus_overlap(cursor: str) -> str:
    try:
        stamp = dt.datetime.fromisoformat(cursor)
    except ValueError:
        return cursor
    return (stamp - OVERLAP).isoformat(sep=" ")


def _dataset_name(spec: TableSpec, engine: str) -> str:
    if engine == "postgresql" and spec.schema != "public":
        return f"{spec.schema}.{spec.name}"
    return spec.name


def mask_user(username: str) -> str:
    """``an••••``: enough to recognise the user, not to read it off a screenshot."""
    return f"{username[:2]}••••" if username else ""
