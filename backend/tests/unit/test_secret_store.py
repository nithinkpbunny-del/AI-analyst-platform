"""Write-only secrets in Postgres (BUILD_SPEC 6.4): encrypted at rest, bound to their workspace and purpose."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import Secret
from app.security import crypto
from app.security.secrets import SecretStore, SecretUnavailable, key_wrapper
from tests.tenancy import make_org

VALUE = "AIzaSyTestOnly-0000000000000000000000001"


@pytest.fixture
def store(settings: Settings) -> SecretStore:
    return SecretStore(key_wrapper(settings))


def test_round_trip_and_nothing_readable_at_rest(db: DbSession, store: SecretStore) -> None:
    org = make_org(db, "UrbanCart", "asha@urbancart.in")
    row = store.put(db, org_id=org.id, purpose="llm_key", value=VALUE)
    db.commit()
    raw = db.execute(text("SELECT ciphertext, wrapped_dek, key_version FROM secrets")).one()
    assert VALUE.encode() not in bytes(raw[0]) + bytes(raw[1])
    assert raw[2] == crypto.KEY_VERSION
    assert store.reveal(db, org_id=org.id, secret_id=row.id, purpose="llm_key") == VALUE


def test_a_row_copied_into_another_workspace_does_not_open(
    db: DbSession, store: SecretStore
) -> None:
    a = make_org(db, "UrbanCart", "asha@urbancart.in")
    b = make_org(db, "Chaiwala", "kabir@chaiwala.in")
    original = store.put(db, org_id=a.id, purpose="llm_key", value=VALUE)
    copy = Secret(
        org_id=b.id,
        purpose="llm_key",
        ciphertext=original.ciphertext,
        wrapped_dek=original.wrapped_dek,
        key_version=original.key_version,
    )
    db.add(copy)
    db.commit()
    with pytest.raises(SecretUnavailable):
        store.reveal(db, org_id=b.id, secret_id=copy.id, purpose="llm_key")


def test_wrong_workspace_purpose_or_master_key(
    db: DbSession, store: SecretStore, settings: Settings
) -> None:
    a = make_org(db, "UrbanCart", "asha@urbancart.in")
    b = make_org(db, "Chaiwala", "kabir@chaiwala.in")
    row = store.put(db, org_id=a.id, purpose="llm_key", value=VALUE)
    db.commit()
    with pytest.raises(SecretUnavailable):
        store.reveal(db, org_id=b.id, secret_id=row.id, purpose="llm_key")
    with pytest.raises(SecretUnavailable):
        store.reveal(db, org_id=a.id, secret_id=row.id, purpose="connection")
    other = SecretStore(crypto.MasterKeyWrapper("a-different-master-key-000000000000000"))
    with pytest.raises(SecretUnavailable):
        other.reveal(db, org_id=a.id, secret_id=row.id, purpose="llm_key")


def test_delete(db: DbSession, store: SecretStore) -> None:
    org = make_org(db, "UrbanCart", "asha@urbancart.in")
    row = store.put(db, org_id=org.id, purpose="llm_key", value=VALUE)
    store.delete(db, org_id=org.id, secret_id=row.id)
    store.delete(db, org_id=org.id, secret_id=row.id)  # already gone: no error
    db.commit()
    assert db.scalars(select(Secret)).all() == []
