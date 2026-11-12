"""The per-question DuckDB workspace: copies only, no file or network access, time and row limits (6.3, 6.5)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from app.data.workspace import QueryError, Workspace, plain


@pytest.fixture
def ws() -> Workspace:
    workspace = Workspace(timeout_seconds=2, max_rows=3)
    workspace.load(
        "orders",
        pd.DataFrame(
            {
                "id": pd.array([1, 2, 3, 4, 5], dtype="Int64"),
                "region": pd.array(["South", "North", "South", None, "West"], dtype="string"),
                "total": [100.0, 250.5, 80.0, 70.0, 10.0],
                "at": pd.to_datetime(
                    ["2026-07-01", "2026-07-02", "2026-08-01", "2026-08-02", "2026-09-01"]
                ),
            }
        ),
    )
    return workspace


def test_queries_return_at_most_max_rows_and_count_the_rest(ws: Workspace) -> None:
    result = ws.query("SELECT id, region, at FROM orders ORDER BY id")
    assert result.columns == ["id", "region", "at"]
    assert len(result.rows) == 3 and result.total_rows == 5 and result.truncated
    assert result.rows[0] == [1, "South", "2026-07-01 00:00:00"]
    assert result.rows[2][1] == "South"
    grouped = ws.query(
        "SELECT region, SUM(total) AS t FROM orders GROUP BY 1 ORDER BY 1 NULLS LAST"
    )
    assert grouped.records()[0] == {"region": "North", "t": 250.5}


def test_unsafe_sql_never_reaches_the_engine(ws: Workspace) -> None:
    for sql in (
        "DROP TABLE orders",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM other",
    ):
        with pytest.raises(QueryError) as caught:
            ws.query(sql)
        assert caught.value.code == "unsafe"


def test_the_engine_itself_cannot_read_files_or_change_settings(ws: Workspace) -> None:
    con = ws._con
    with pytest.raises(Exception, match="disabled by configuration"):
        con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()
    with pytest.raises(Exception, match="locked"):
        con.execute("SET enable_external_access = true")


def test_a_slow_query_is_interrupted() -> None:
    ws = Workspace(timeout_seconds=0.3)
    ws.load("n", pd.DataFrame({"x": range(3000)}))
    with pytest.raises(QueryError) as caught:
        ws.query("SELECT count(*) FROM n a, n b, n c WHERE a.x + b.x + c.x > 1")
    assert caught.value.code == "timeout"


def test_errors_are_explained(ws: Workspace) -> None:
    with pytest.raises(QueryError) as caught:
        ws.query("SELECT no_such_column FROM orders")
    assert caught.value.code == "error"


def test_bad_table_names_are_refused(ws: Workspace) -> None:
    with pytest.raises(QueryError):
        ws.load("orders; drop", pd.DataFrame({"x": [1]}))


def test_values_become_plain_json() -> None:
    assert plain(datetime(2026, 7, 1, 10, 0)) == "2026-07-01 10:00:00"
    assert plain(date(2026, 7, 1)) == "2026-07-01"
    assert plain(float("nan")) is None
    assert plain(pd.NaT) is None
    import numpy as np

    assert plain(np.int64(5)) == 5
