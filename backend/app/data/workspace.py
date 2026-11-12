"""The per-question analysis engine (BUILD_SPEC 3 "DuckDB, in-process, per request"; 6.3 item 4).

One ``Workspace`` per question: an in-memory DuckDB holding **copies** of the tables the agent fetched, under the
dictionary's table keys (``orders``, ``customers``). It can join tables from different sources (R4).

Walls around it:
* ``enable_external_access = false`` and a locked configuration: no files, no network, no extensions, no settings
  changes, whatever SQL reaches it;
* every query the agent writes passes ``sql_guard.check_select`` first (one SELECT over known tables);
* every query is interrupted after ``timeout_seconds``;
* at most ``max_rows`` rows come back to the caller (the agent's limit of rows per tool call).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb
import pandas as pd

from app.security import sql_guard

TABLE_NAME_MAX = 60


class QueryError(Exception):
    """A query could not run. ``code``: unsafe | timeout | error | unknown_table."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class TableInfo:
    name: str
    rows: int
    columns: list[str]
    meta: dict[str, Any] = field(
        default_factory=dict
    )  # provenance of the copy (dataset, fetched_at, ...)


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int
    truncated: bool
    sql: str

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


def plain(value: Any) -> Any:
    """A DuckDB/pandas cell as JSON-friendly Python (dates as ISO text, decimals and numpy numbers as floats)."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):  # numpy scalars
        try:
            return plain(value.item())
        except (ValueError, TypeError):
            return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, int | float | str | bool):
        return value
    return str(value)


class Workspace:
    def __init__(self, *, timeout_seconds: float = 30.0, max_rows: int = 200) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.tables: dict[str, TableInfo] = {}
        self._frames: dict[str, pd.DataFrame] = {}
        self._con = duckdb.connect(
            ":memory:",
            config={"enable_external_access": False, "threads": 2, "memory_limit": "2GB"},
        )
        self._con.execute("SET lock_configuration = true")
        self._lock = threading.Lock()

    # ------------------------------------------------------------------------------------------ tables
    def load(self, name: str, frame: pd.DataFrame, meta: dict[str, Any] | None = None) -> TableInfo:
        """Put a copy of ``frame`` into the workspace as table ``name`` (replacing an earlier copy)."""
        if not name or len(name) > TABLE_NAME_MAX or sql_guard.safe_identifier(name) != name:
            raise QueryError("error", f"bad table name {name!r}")
        prepared = frame.copy()
        for column in prepared.columns:
            series = prepared[column]
            if isinstance(series.dtype, pd.DatetimeTZDtype):
                # Analysis happens in the business's local time; the reader already converted to it.
                prepared[column] = series.dt.tz_localize(None)
        with self._lock:
            if name in self._frames:
                self._con.unregister(name)
            self._con.register(name, prepared)
            self._frames[name] = prepared
        info = TableInfo(name, len(prepared), [str(c) for c in prepared.columns], dict(meta or {}))
        self.tables[name] = info
        return info

    def has(self, name: str) -> bool:
        return name in self.tables

    def frame_of(self, name: str) -> pd.DataFrame:
        return self._frames[name]

    # ----------------------------------------------------------------------------------------- queries
    @contextmanager
    def _deadline(self) -> Iterator[None]:
        timer = threading.Timer(self.timeout_seconds, self._con.interrupt)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()

    def _execute(self, sql: str, params: Sequence[Any] | None = None) -> duckdb.DuckDBPyConnection:
        try:
            with self._deadline():
                return self._con.execute(sql, list(params or []))
        except duckdb.InterruptException as error:
            raise QueryError(
                "timeout", f"the query took longer than {self.timeout_seconds:g} s"
            ) from error
        except duckdb.Error as error:
            raise QueryError("error", str(error).splitlines()[0][:300]) from error

    def check(self, sql: str) -> sql_guard.CheckedSql:
        try:
            return sql_guard.check_select(sql, dialect="duckdb", allowed_tables=self.tables)
        except sql_guard.UnsafeSql as error:
            raise QueryError("unsafe", str(error)) from error

    def query(self, sql: str, *, max_rows: int | None = None) -> QueryResult:
        """Run SQL written by the agent: checked SELECT-only, at most ``max_rows`` rows back."""
        checked = self.check(sql)
        return self._run(checked.sql, None, max_rows)

    def run_code_sql(
        self, sql: str, params: Sequence[Any] | None = None, *, max_rows: int | None = None
    ) -> QueryResult:
        """SQL built by the analytics code (identifiers quoted, values as parameters). Still checked."""
        self.check(sql)
        return self._run(sql, params, max_rows)

    def frame(self, sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
        """A full result as a DataFrame, for the analytics code (never sent to the LLM as it is)."""
        self.check(sql)
        with self._lock:
            cursor = self._execute(sql, params)
            try:
                result: pd.DataFrame = cursor.df()
            except duckdb.Error as error:  # pragma: no cover - fetch errors are rare
                raise QueryError("error", str(error).splitlines()[0][:300]) from error
        return result

    def _run(self, sql: str, params: Sequence[Any] | None, max_rows: int | None) -> QueryResult:
        limit = self.max_rows if max_rows is None else max_rows
        with self._lock:
            cursor = self._execute(sql, params)
            columns = [d[0] for d in cursor.description or []]
            fetched = cursor.fetchmany(limit + 1)
            total = len(fetched)
            if total > limit:
                # Count the rest without keeping it.
                while chunk := cursor.fetchmany(10_000):
                    total += len(chunk)
        rows = [[plain(v) for v in row] for row in fetched[:limit]]
        return QueryResult(columns, rows, total, total > limit, sql)

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
