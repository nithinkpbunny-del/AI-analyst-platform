"""Checking metric expressions on a sample (BUILD_SPEC 9 step 4; M7).

Every metric is a DuckDB SQL expression over its table's columns (money already in rupees, ``units.to_units``).
It is run on a sample of the stored rows in an in-memory DuckDB with no file or network access. A metric is valid
when it runs and gives one number. Metrics may use other metrics by name (``revenue / orders``).
"""

from __future__ import annotations

import re

import duckdb
import pandas as pd

from app.dictionary.model import Dictionary

SAMPLE_ROWS = 5000
# Anything that is not a plain expression over columns.
_UNSAFE = re.compile(
    r"(;|--|/\*|\b(select|attach|detach|copy|pragma|install|load|create|drop|insert|update|delete|call|"
    r"export|import|set|read_[a-z_]+|glob)\b)",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"'[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_]*")


class MetricError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

    def text(self) -> str:
        return f"{self.code}: {self.detail[:200]}" if self.detail else self.code


def expand(key: str, dictionary: Dictionary, stack: tuple[str, ...] = ()) -> tuple[str, str | None]:
    """The SQL of a metric with other metrics written out, and the table key it runs on."""
    metric = dictionary.metrics[key]
    if key in stack:
        raise MetricError("cycle", key)
    if _UNSAFE.search(metric.expr):
        raise MetricError("unsafe")
    owner = dictionary.datasets.get(metric.dataset) if metric.dataset else None
    if metric.dataset and owner is None:
        raise MetricError("no_table", metric.dataset)
    columns = {c.lower() for c in owner.columns} if owner else set()
    lowered = {k.lower(): k for k in dictionary.metrics}
    tables: set[str] = {metric.dataset} if metric.dataset else set()

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token[0] in "'\"":
            return token
        name = lowered.get(token.lower())
        if name is None or name == key or token.lower() in columns:
            return token
        inner, table = expand(name, dictionary, (*stack, key))
        if table:
            tables.add(table)
        return f"({inner})"

    sql = _TOKEN.sub(replace, metric.expr)
    if len(tables) > 1:
        raise MetricError("many_tables", ", ".join(sorted(tables)))
    return sql, next(iter(tables), None)


def _connection(frame: pd.DataFrame) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:", config={"enable_external_access": False})
    con.register("data", frame)
    con.execute("SET lock_configuration = true")
    return con


def run_metric(sql: str, frame: pd.DataFrame) -> None:
    con = _connection(frame)
    try:
        # The expression passed ``_UNSAFE`` and runs in an in-memory engine with no file or network access.
        rows = con.execute(f"SELECT ({sql}) AS value FROM data").fetchall()  # noqa: S608
    except duckdb.Error as error:
        message = str(error).splitlines()[0]
        if "not found" in message and "olumn" in message:
            raise MetricError("unknown_column", message) from error
        raise MetricError("syntax", message) from error
    finally:
        con.close()
    if len(rows) != 1:
        raise MetricError("not_a_total")
    value = rows[0][0]
    if value is not None and not isinstance(value, int | float) and not hasattr(value, "is_finite"):
        raise MetricError("not_a_number")


def check_metrics(
    dictionary: Dictionary, samples: dict[str, pd.DataFrame], *, keep_unchecked: bool = False
) -> None:
    """Set ``valid`` / ``error`` on every metric. ``samples`` are tables already in rupees, by dataset key.
    With ``keep_unchecked`` a metric whose table has no sample keeps its earlier result."""
    for key, metric in dictionary.metrics.items():
        try:
            sql, table = expand(key, dictionary)
            if table is None:
                raise MetricError("no_table")
            frame = samples.get(table)
            if frame is None:
                if keep_unchecked:
                    continue
                raise MetricError("no_sample", table)
            run_metric(sql, frame)
        except MetricError as error:
            metric.valid, metric.error = False, error.text()
        else:
            metric.valid, metric.error = True, None
