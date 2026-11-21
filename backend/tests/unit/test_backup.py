"""Backup and restore tooling (M19, docs/RUNBOOK_BACKUP_RESTORE.md). The full cycle runs in ``make backup-drill``;
these check the parts that decide whether a restore is trusted."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from ops import backup
from tests.tenancy import build_world


def test_the_password_never_goes_on_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    target = backup.PgTarget.from_url("postgresql+psycopg://app:s3cr%40t@localhost:5432/analyst")
    assert (target.user, target.password, target.database) == ("app", "s3cr@t", "analyst")
    monkeypatch.setattr(backup, "_local_tools_major", lambda: 14)  # too old: Docker runs the tools
    monkeypatch.setattr("ops.backup.shutil.which", lambda _name: "/usr/bin/docker")
    command, env = backup.pg_command("pg_dump", target, ["--format=custom"])
    assert "s3cr@t" not in " ".join(command) and env["PGPASSWORD"] == "s3cr@t"
    assert "host.docker.internal" in command and backup.PG_IMAGE in command


def test_object_keys_cannot_leave_the_backup_folder(tmp_path: Path) -> None:
    assert backup._safe_path(tmp_path, "orgs/a/files/b").is_relative_to(tmp_path)
    for key in ("../../etc/passwd", "/abs", "orgs/../../x"):
        with pytest.raises(backup.BackupError):
            backup._safe_path(tmp_path, key)


def test_a_damaged_dump_is_refused(tmp_path: Path) -> None:
    (tmp_path / backup.DUMP).write_bytes(b"dump")
    (tmp_path / backup.MANIFEST).write_text(json.dumps({"dump_sha256": "0" * 64}))
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.load_manifest(tmp_path)


def test_a_restore_never_overwrites_a_database(migrated_engine: Engine, database_url: str) -> None:
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.ensure_empty(backup.PgTarget.from_url(database_url))


def test_isolation_holds_on_a_real_database(
    db: DbSession, settings: Settings, database_url: str
) -> None:
    from datetime import UTC, datetime

    build_world(db, settings, datetime(2026, 11, 21, tzinfo=UTC))
    engine = backup.engine_for(backup.PgTarget.from_url(database_url))
    try:
        assert backup.check_isolation(engine) == []
        facts = backup.database_facts(engine)
    finally:
        engine.dispose()
    assert facts["revision"] and facts["tables"]["memberships"] >= 5
    assert "memberships.tenant_isolation" in facts["policies"]
    assert "audit_log.audit_log_no_update_delete" in facts["triggers"]


def test_verify_reports_what_did_not_come_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / backup.DUMP).write_bytes(b"dump")
    import hashlib

    manifest: dict[str, Any] = {
        "dump_sha256": hashlib.sha256(b"dump").hexdigest(),
        "revision": "0018",
        "tables": {"memberships": 5, "audit_log": 9},
        "policies": ["memberships.tenant_isolation"],
        "rls_tables": ["memberships"],
        "triggers": ["audit_log.audit_log_no_update_delete"],
        "objects": {},
    }
    (tmp_path / backup.MANIFEST).write_text(json.dumps(manifest))
    restored = {
        "revision": "0017",
        "tables": {"memberships": 5, "audit_log": 8},
        "policies": [],
        "rls_tables": ["memberships"],
        "triggers": [],
    }
    monkeypatch.setattr(
        backup, "engine_for", lambda _t: type("E", (), {"dispose": lambda s: None})()
    )
    monkeypatch.setattr(backup, "database_facts", lambda _e: restored)
    monkeypatch.setattr(backup, "check_isolation", lambda _e: [])
    problems = backup.verify(tmp_path, backup.PgTarget.from_url("postgresql://a:b@h/d"), None)
    assert "migration 0017 != 0018" in problems
    assert "rows in audit_log: 8 restored, 9 backed up" in problems
    assert any(p.startswith("policies missing") for p in problems)
    assert any(p.startswith("triggers missing") for p in problems)
