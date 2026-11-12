"""SELECT-only validation of SQL written by the agent (BUILD_SPEC 6.3 items 3-4; P2).

``check_select(sql, dialect=..., allowed_tables=...)`` parses with ``sqlglot`` in the given dialect and accepts
exactly one read-only query: a ``SELECT``, a ``WITH ... SELECT`` or a ``UNION``/``INTERSECT``/``EXCEPT`` of
selects. It refuses:

* more than one statement, anything that is not a query (``INSERT``, ``UPDATE``, ``DELETE``, ``CREATE``, ``DROP``,
  ``ALTER``, ``COPY``, ``ATTACH``, ``PRAGMA``, ``SET``, ``CALL``, ...);
* ``SELECT ... INTO`` and locking clauses (``FOR UPDATE``);
* functions on a deny-list (sleeping, other databases, large objects, files, the network, settings);
* table functions (``read_csv('...')``) and, when ``allowed_tables`` is given, any table not in it.

The DuckDB workspace adds its own walls (no external access, locked configuration); this check is the first one,
and it is the same check M14 uses for customer databases.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Function names (lower case) that a read-only analysis never needs. Prefixes end with "*".
DENIED_FUNCTIONS = (
    "pg_sleep*",
    "sleep",
    "benchmark",
    "dblink*",
    "lo_*",
    "pg_read_*",
    "pg_ls_*",
    "pg_stat_file",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "set_config",
    "current_setting",
    "copy",
    "load_file",
    "read_*",
    "write_*",
    "glob",
    "parquet_*",
    "sniff_csv",
    "httpfs*",
    "http_*",
    "getenv",
    "system",
    "shell",
    "query",
    "query_table",
    "sqlite_*",
    "postgres_*",
    "mysql_*",
    "duckdb_*",
    "pragma_*",
    "install",
    "load",
    "attach",
    "detach",
    "export_database",
    "import_database",
    "checkpoint",
    "into_outfile",
    "sys_exec",
    "xp_*",
)

_WRITES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Into,
    exp.Lock,
    exp.Set,
    exp.Pragma,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.LoadData,
    exp.Grant,
)
_QUERIES: tuple[type[exp.Expression], ...] = (exp.Select, exp.Union, exp.Intersect, exp.Except)
# sqlglot logs a warning for syntax it only knows as a "Command"; that is refused anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)
_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeSql(Exception):
    """The SQL is not a single read-only query. ``code`` is stable; ``detail`` names what was refused."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CheckedSql:
    sql: str  # normalised by sqlglot, in the same dialect
    tables: frozenset[str]  # lower-case names of the tables it reads


def _denied(name: str) -> bool:
    lowered = name.lower()
    for pattern in DENIED_FUNCTIONS:
        if pattern.endswith("*"):
            if lowered.startswith(pattern[:-1]):
                return True
        elif lowered == pattern:
            return True
    return False


def _function_name(node: Any) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this)
    name: str = node.sql_name() if isinstance(node, exp.Func) else node.key
    return name


def check_select(
    sql: str,
    *,
    dialect: str = "duckdb",
    allowed_tables: Iterable[str] | None = None,
    max_length: int = 20_000,
) -> CheckedSql:
    if not sql or not sql.strip():
        raise UnsafeSql("empty")
    if len(sql) > max_length:
        raise UnsafeSql("too_long")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as error:
        raise UnsafeSql("syntax", str(error).splitlines()[0][:200]) from error
    if len(statements) != 1:
        raise UnsafeSql("one_statement")
    tree = statements[0]
    if not isinstance(tree, _QUERIES):
        raise UnsafeSql("not_a_query", tree.key)
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: set[str] = set()
    for node in tree.walk():
        if isinstance(node, _WRITES):
            raise UnsafeSql("not_read_only", node.key)
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise UnsafeSql("not_read_only", "lock")
        if isinstance(node, exp.Func) and _denied(_function_name(node)):
            raise UnsafeSql("function", _function_name(node).lower())
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier):
                # read_csv('x'), a string as a table, a function in FROM: never needed here.
                raise UnsafeSql("table_function", node.sql(dialect=dialect)[:80])
            if node.args.get("db") or node.args.get("catalog"):
                raise UnsafeSql("qualified_table", node.sql(dialect=dialect)[:80])
            name = node.name.lower()
            if any(ch in name for ch in "/\\.:~"):
                # DuckDB reads a quoted path as a file ("FROM '/etc/passwd'").
                raise UnsafeSql("table_function", name[:80])
            if name not in ctes:
                tables.add(name)
    if allowed_tables is not None:
        allowed = {t.lower() for t in allowed_tables}
        unknown = sorted(tables - allowed)
        if unknown:
            raise UnsafeSql("unknown_table", ", ".join(unknown))
    return CheckedSql(sql=tree.sql(dialect=dialect), tables=frozenset(tables))


def safe_identifier(name: str) -> str:
    """A table or column name the code puts into SQL itself: plain names are used as they are, anything else is
    double-quoted with quotes escaped."""
    if _SAFE_NAME.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'
