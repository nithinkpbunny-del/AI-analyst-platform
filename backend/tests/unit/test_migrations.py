"""Migrations are reversible (BUILD_SPEC 15): upgrade, downgrade to nothing, upgrade again."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from tests.conftest import alembic_config


def test_upgrade_downgrade_upgrade(migrated_engine: Engine, database_url: str) -> None:
    scratch = "migration_roundtrip"
    with migrated_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {scratch}"))
        conn.execute(text(f"CREATE DATABASE {scratch}"))
    url = make_url(database_url).set(database=scratch).render_as_string(hide_password=False)
    config = alembic_config(url)
    try:
        command.upgrade(config, "head")
        from app.db.session import make_engine

        engine = make_engine(url)
        tables = set(inspect(engine).get_table_names())
        assert {"users", "organizations", "memberships", "sessions", "audit_log"} <= tables
        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, "head")
        engine.dispose()
    finally:
        with migrated_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))
