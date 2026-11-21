"""The activity log's one allowed change (D124, migration 0018): anonymising a deleted workspace's entries, and
nothing else, ever (BUILD_SPEC 5 "append-only")."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as DbSession

from app.audit import writer as audit
from app.db.tenant import scope_to_org
from app.models import AuditLog, Organization
from app.privacy.deletion import anonymise_activity

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
META = audit.RequestMeta("203.0.113.9", "Firefox")


def _entry(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID | None = None) -> uuid.UUID:
    row = audit.write(
        db,
        action="member.invited",
        at=NOW,
        meta=META,
        org_id=org_id,
        user_id=user_id or uuid.uuid4(),
        target_type="invitation",
        target_id=uuid.uuid4(),
        details={"email": "ravi@urbancart.in"},
    )
    db.commit()
    return row.id


def _workspace(db: DbSession, name: str = "UrbanCart") -> Organization:
    org = Organization(name=name, monthly_question_limit=500)
    db.add(org)
    db.commit()
    return org


def _anonymise(db: DbSession, entry_id: uuid.UUID, **overrides: object) -> None:
    values = {
        "user_id": None,
        "ip": None,
        "user_agent": None,
        "target_id": None,
        "details": '{"anonymised": true}',
        **overrides,
    }
    db.execute(text("SET LOCAL app.audit_anonymise = 'on'"))
    db.execute(
        text(
            "UPDATE audit_log SET user_id = :user_id, ip = :ip, user_agent = :user_agent, "
            "target_id = :target_id, details = CAST(:details AS jsonb) WHERE id = :id"
        ),
        {**values, "id": entry_id},
    )


def test_a_deleted_workspaces_entries_are_anonymised_and_kept(db: DbSession) -> None:
    gone, kept = _workspace(db), _workspace(db, "Chaiwala Foods")
    _entry(db, gone.id)
    _entry(db, gone.id)
    other = _entry(db, kept.id)
    db.delete(gone)
    db.flush()
    assert anonymise_activity(db, gone.id) == 2
    db.commit()
    rows = list(db.scalars(select(AuditLog).where(AuditLog.org_id == gone.id)))
    assert len(rows) == 2
    for row in rows:
        assert (row.user_id, row.ip, row.user_agent, row.target_id) == (None, None, None, None)
        assert row.details == {"anonymised": True}
        assert row.action == "member.invited" and row.target_type == "invitation" and row.at == NOW
    untouched = db.get(AuditLog, other)
    assert untouched is not None and untouched.details == {"email": "ravi@urbancart.in"}
    # Running it again changes nothing (already anonymised).
    assert anonymise_activity(db, gone.id) == 0
    db.rollback()


def test_a_living_workspaces_entries_cannot_be_anonymised(db: DbSession) -> None:
    org = _workspace(db)
    entry = _entry(db, org.id)
    with pytest.raises(DBAPIError, match="append-only"):
        _anonymise(db, entry)
    db.rollback()


def test_without_the_flag_nothing_may_change(db: DbSession) -> None:
    org = _workspace(db)
    entry = _entry(db, org.id)
    db.delete(org)
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text(
                "UPDATE audit_log SET user_id = NULL, ip = NULL, user_agent = NULL, target_id = NULL, "
                "details = '{\"anonymised\": true}'::jsonb WHERE id = :id"
            ),
            {"id": entry},
        )
    db.rollback()


@pytest.mark.parametrize(
    "change",
    [
        {"ip": "203.0.113.9"},  # keeps where
        {"details": '{"anonymised": true, "email": "ravi@urbancart.in"}'},  # keeps details
        {"details": "{}"},
        {"target_id": "kept"},
    ],
)
def test_only_a_complete_anonymisation_is_allowed(db: DbSession, change: dict[str, object]) -> None:
    org = _workspace(db)
    entry = _entry(db, org.id)
    db.delete(org)
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        _anonymise(db, entry, **change)
    db.rollback()


def test_other_columns_cannot_be_rewritten_while_anonymising(db: DbSession) -> None:
    org = _workspace(db)
    entry = _entry(db, org.id)
    db.delete(org)
    db.commit()
    db.execute(text("SET LOCAL app.audit_anonymise = 'on'"))
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text(
                "UPDATE audit_log SET user_id = NULL, ip = NULL, user_agent = NULL, target_id = NULL, "
                "details = '{\"anonymised\": true}'::jsonb, action = 'org.created' WHERE id = :id"
            ),
            {"id": entry},
        )
    db.rollback()


def test_deleting_entries_is_still_refused_even_with_the_flag(db: DbSession) -> None:
    org = _workspace(db)
    entry = _entry(db, org.id)
    db.delete(org)
    db.commit()
    db.execute(text("SET LOCAL app.audit_anonymise = 'on'"))
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": entry})
    db.rollback()


def test_a_workspace_request_cannot_update_the_log_at_all(db: DbSession) -> None:
    org = _workspace(db)
    entry = _entry(db, org.id)
    scope_to_org(db, org.id)
    db.execute(text("SET LOCAL app.audit_anonymise = 'on'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        _anonymise(db, entry)
    db.rollback()
