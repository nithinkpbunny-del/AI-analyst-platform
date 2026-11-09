"""Security of secrets, BYOK keys and usage (BUILD_SPEC 6.2, 6.4, P3).

Tenant-scoped sessions (the ``app_tenant`` role with ``app.org_id`` set, as every workspace request runs) must not
see another workspace's secrets, keys or usage, must not change usage history, and must not rewrite a secret.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.tenant import scope_to_org
from app.llm import keys, usage
from app.llm.base import Usage
from app.models import LlmKey, Organization, Secret, UsageEvent
from app.security.secrets import SecretStore, key_wrapper
from tests.conftest import FakeClock
from tests.tenancy import World, build_world

KEY_A = "sk-ant-urbancart-secret-0000000000000000"
KEY_B = "AIzaSyChaiwalaSecret00000000000000000000"


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    world = build_world(db, settings, clock.now)
    store = SecretStore(key_wrapper(settings))
    for org, owner, key in (
        (world.org_a, world.owner_a, KEY_A),
        (world.org_b, world.owner_b, KEY_B),
    ):
        keys.store_key(
            db, org_id=org.id, provider="gemini", api_key=key, user_id=owner.id, secrets=store
        )
        usage.record(
            db,
            org_id=org.id,
            user_id=owner.id,
            run_id=uuid.uuid4(),
            kind="question",
            provider="gemini",
            model="gemini-2.5-flash",
            usage=Usage(100, 10),
            key_source="managed",
            at=clock.now,
        )
    db.commit()
    return world


def scoped(session_factory: sessionmaker[DbSession], org: Organization) -> DbSession:
    session = session_factory()
    scope_to_org(session, org.id)
    return session


@pytest.mark.parametrize("model", [Secret, LlmKey, UsageEvent])
def test_only_your_own_workspaces_rows_are_visible(
    session_factory: sessionmaker[DbSession],
    world: World,
    model: type[Secret | LlmKey | UsageEvent],
) -> None:
    with scoped(session_factory, world.org_a) as session:
        rows = session.scalars(select(model)).all()  # no WHERE org_id: RLS alone filters
        assert len(rows) == 1
        assert {row.org_id for row in rows} == {world.org_a.id}  # type: ignore[attr-defined]


def test_another_workspaces_secret_cannot_be_revealed(
    session_factory: sessionmaker[DbSession], world: World, settings: Settings, db: DbSession
) -> None:
    store = SecretStore(key_wrapper(settings))
    secret_b = db.scalars(select(LlmKey.secret_ref).where(LlmKey.org_id == world.org_b.id)).one()
    with scoped(session_factory, world.org_a) as session:
        from app.security.secrets import SecretUnavailable

        with pytest.raises(SecretUnavailable):
            # Even with the right id and claiming to be org B, the row is invisible to org A's session.
            store.reveal(session, org_id=world.org_b.id, secret_id=secret_b, purpose="llm_key")


def test_usage_history_is_append_only(
    session_factory: sessionmaker[DbSession], world: World
) -> None:
    for statement in ("UPDATE usage_events SET input_tokens = 0", "DELETE FROM usage_events"):
        with scoped(session_factory, world.org_a) as session, pytest.raises(DBAPIError) as caught:
            session.execute(text(statement))
        assert "permission denied" in str(caught.value)


def test_secrets_are_never_rewritten_in_place(
    session_factory: sessionmaker[DbSession], world: World
) -> None:
    with scoped(session_factory, world.org_a) as session, pytest.raises(DBAPIError) as caught:
        session.execute(text("UPDATE secrets SET key_version = 9"))
    assert "permission denied" in str(caught.value)


def test_cannot_write_usage_or_keys_into_another_workspace(
    session_factory: sessionmaker[DbSession], world: World
) -> None:
    with scoped(session_factory, world.org_a) as session, pytest.raises(DBAPIError) as caught:
        usage.record(
            session,
            org_id=world.org_b.id,
            user_id=None,
            run_id=uuid.uuid4(),
            kind="question",
            provider="gemini",
            model="m",
            usage=Usage(1, 1),
            key_source="managed",
        )
    assert "row-level security" in str(caught.value)


def test_deleting_another_workspaces_key_does_nothing(
    session_factory: sessionmaker[DbSession], world: World, db: DbSession
) -> None:
    with scoped(session_factory, world.org_a) as session:
        session.execute(
            text("DELETE FROM llm_keys WHERE org_id <> current_setting('app.org_id')::uuid")
        )
        session.commit()
    assert db.scalar(select(func.count()).select_from(LlmKey)) == 2


def test_no_response_ever_contains_a_key(client: TestClient, world: World) -> None:
    for path in ("/org/ai", "/auth/me", "/org", "/org/usage", "/audit"):
        response = client.get(path, headers=world.headers("owner_a"))
        assert response.status_code == 200, path
        assert KEY_A not in response.text and KEY_B not in response.text


def test_deleting_a_workspace_deletes_its_key_secrets_and_usage(
    db: DbSession, world: World
) -> None:
    """Org deletion (M10) relies on the cascades: nothing may block it or be left behind."""
    org_id = world.org_a.id
    db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})
    db.commit()
    for model in (Secret, LlmKey, UsageEvent):
        assert db.scalar(select(func.count()).select_from(model).where(model.org_id == org_id)) == 0
    assert (
        db.scalar(select(func.count()).select_from(LlmKey)) == 1
    )  # the other workspace keeps its key
