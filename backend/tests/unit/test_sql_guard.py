"""SELECT-only validation of the agent's SQL (BUILD_SPEC 6.3 items 3-4)."""

from __future__ import annotations

import pytest

from app.security.sql_guard import UnsafeSql, check_select, safe_identifier

TABLES = {"orders", "customers"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, SUM(total_price) FROM orders GROUP BY 1",
        "select * from orders where id = 1",
        "WITH a AS (SELECT * FROM orders) SELECT count(*) FROM a",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT o.id FROM orders o JOIN customers c ON o.customer_id = c.id",
        "SELECT date_trunc('month', created_at) AS m, count(*) FROM orders GROUP BY 1 ORDER BY 1",
        "SELECT * FROM orders QUALIFY row_number() OVER (PARTITION BY id) = 1",
        "SELECT 'DROP TABLE orders' AS text FROM orders",
    ],
)
def test_read_only_queries_pass(sql: str) -> None:
    checked = check_select(sql, allowed_tables=TABLES)
    assert checked.tables <= TABLES


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "empty"),
        ("SELECT * FROM orders; DROP TABLE orders", "one_statement"),
        ("SELECT 1; SELECT 2", "one_statement"),
        ("INSERT INTO orders VALUES (1)", "not_a_query"),
        ("UPDATE orders SET id = 1", "not_a_query"),
        ("DELETE FROM orders", "not_a_query"),
        ("DROP TABLE orders", "not_a_query"),
        ("CREATE TABLE x AS SELECT * FROM orders", "not_a_query"),
        ("ALTER TABLE orders ADD COLUMN x INT", "not_a_query"),
        ("COPY orders TO '/tmp/x.csv'", "not_a_query"),
        ("ATTACH 'other.db'", "not_a_query"),
        ("PRAGMA database_list", "not_a_query"),
        ("SET enable_external_access = true", "not_a_query"),
        ("INSTALL httpfs", "not_a_query"),
        ("CALL pragma_version()", "not_a_query"),
        ("SELECT * INTO backup FROM orders", "not_read_only"),
        ("SELECT * FROM orders FOR UPDATE", "not_read_only"),
        ("SELECT pg_sleep(10)", "function"),
        ("SELECT getenv('HOME')", "function"),
        ("SELECT current_setting('threads')", "function"),
        ("SELECT * FROM read_csv('/etc/passwd')", "table_function"),
        ("SELECT * FROM read_parquet('s3://bucket/x')", "table_function"),
        ("SELECT * FROM '/etc/passwd'", "table_function"),
        ('SELECT * FROM "data.parquet"', "table_function"),
        ("SELECT * FROM glob('*')", "table_function"),
        ("SELECT * FROM duckdb_settings()", "table_function"),
        ("SELECT * FROM main.orders", "qualified_table"),
        ("SELECT * FROM secrets", "unknown_table"),
        ("SELECT * FROM information_schema.tables", "qualified_table"),
        ("SELEC * FROM orders", "syntax"),
    ],
)
def test_everything_else_is_refused(sql: str, code: str) -> None:
    with pytest.raises(UnsafeSql) as caught:
        check_select(sql, allowed_tables=TABLES)
    assert caught.value.code == code


def test_postgres_dialect_refuses_its_own_dangers() -> None:
    for sql in (
        "SELECT dblink('x', 'y')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT pg_read_file('x')",
    ):
        with pytest.raises(UnsafeSql):
            check_select(sql, dialect="postgres")


def test_identifiers_are_quoted_unless_plain() -> None:
    assert safe_identifier("total_price") == "total_price"
    assert safe_identifier('weird"name') == '"weird""name"'
    assert safe_identifier("Revenue (INR)") == '"Revenue (INR)"'
