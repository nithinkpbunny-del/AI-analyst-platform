"""Row-Level Security (BUILD_SPEC 6.2, P3): a tenant-scoped session can never see or write another workspace's
rows, even with a query that forgets ``WHERE org_id = …``. These tests talk to Postgres directly, under the same
``app_tenant`` role and ``app.org_id`` setting the API uses (``app.db.tenant``)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.tenant import TENANT_ROLE, scope_to_org
from app.models import (
    AuditLog,
    Consent,
    Invitation,
    LoginChallenge,
    Membership,
    Organization,
    Session,
    User,
)
from tests.conftest import FakeClock
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    world = build_world(db, settings, clock.now)
    for org, email in ((world.org_a, "new@urbancart.in"), (world.org_b, "new@chaiwala.in")):
        db.add(
            Invitation(
                org_id=org.id,
                email=email,
                role="viewer",
                token_hash=f"hash-{email}",
                expires_at=clock.now + timedelta(days=7),
            )
        )
        db.add(
            AuditLog(org_id=org.id, action="test.event", details={}, at=clock.now),
        )
        db.add(
            Consent(
                user_id=world.owner_a.id if org is world.org_a else world.owner_b.id,
                org_id=org.id,
                policy_version="test",
                accepted_at=clock.now,
            )
        )
    db.commit()
    return world


def scoped(session_factory: sessionmaker[DbSession], org: Organization) -> DbSession:
    session = session_factory()
    scope_to_org(session, org.id)
    return session


TENANT_MODELS = [Organization, Membership, Invitation, Session, Consent, AuditLog]


class TestSchema:
    def test_every_tenant_owned_table_has_rls_and_the_policy(self, migrated_engine: Engine) -> None:
        """Future tables with org_id must get the policy too (BUILD_SPEC 5): this test finds any that do not."""
        with migrated_engine.connect() as conn:
            tables = set(
                conn.scalars(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name = 'org_id'"
                    )
                )
            ) | {"organizations"}
            rls: dict[str, bool] = dict(
                conn.execute(
                    text(
                        "SELECT relname, relrowsecurity FROM pg_class "
                        "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
                    )
                ).all()
            )
            policies = set(
                conn.scalars(
                    text(
                        "SELECT tablename FROM pg_policies "
                        "WHERE schemaname = 'public' AND policyname = 'tenant_isolation'"
                    )
                )
            )
        assert tables, "no tenant tables found"
        assert {t for t in tables if not rls.get(t)} == set(), "RLS not enabled"
        assert tables - policies == set(), "tenant_isolation policy missing"

    def test_tenant_role_cannot_bypass_rls(self, migrated_engine: Engine) -> None:
        with migrated_engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = :r"),
                {"r": TENANT_ROLE},
            ).one()
            owners = set(
                conn.scalars(text("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"))
            )
        assert tuple(row) == (False, False, False)
        assert TENANT_ROLE not in owners


class TestIsolation:
    @pytest.mark.parametrize("model", TENANT_MODELS, ids=lambda m: m.__tablename__)
    def test_sees_only_its_own_workspace_without_any_filter(
        self, world: World, session_factory: sessionmaker[DbSession], model: Any
    ) -> None:
        with scoped(session_factory, world.org_a) as session:
            rows: list[Any] = list(session.scalars(select(model)))
            column = "id" if model is Organization else "org_id"
            assert rows, f"no {model.__tablename__} rows visible at all"
            assert {getattr(r, column) for r in rows} == {world.org_a.id}

    def test_rows_of_another_workspace_look_missing(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with scoped(session_factory, world.org_a) as session:
            assert session.get(Organization, world.org_b.id) is None
            other = session.scalar(select(Membership).where(Membership.org_id == world.org_b.id))
            assert other is None

    def test_cannot_write_into_another_workspace(
        self, world: World, session_factory: sessionmaker[DbSession], clock: FakeClock
    ) -> None:
        with scoped(session_factory, world.org_a) as session:
            session.add(
                Membership(
                    org_id=world.org_b.id, user_id=world.analyst_a.id, role="owner", status="active"
                )
            )
            with pytest.raises(ProgrammingError, match="row-level security"):
                session.flush()
            session.rollback()
            session.add(AuditLog(org_id=world.org_b.id, action="x", details={}, at=clock.now))
            with pytest.raises(ProgrammingError, match="row-level security"):
                session.flush()

    def test_updates_cannot_reach_another_workspace(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with scoped(session_factory, world.org_a) as session:
            result = session.execute(update(Organization).values(name="Hacked"))
            session.commit()
            assert result.rowcount == 1  # type: ignore[attr-defined]
        with session_factory() as system:
            names = set(system.scalars(select(Organization.name)))
        assert names == {"Hacked", "Chaiwala Foods"}

    def test_moving_a_row_to_another_workspace_is_refused(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with (
            scoped(session_factory, world.org_a) as session,
            pytest.raises(ProgrammingError, match="row-level security"),
        ):
            session.execute(
                update(Membership)
                .where(Membership.user_id == world.analyst_a.id)
                .values(org_id=world.org_b.id)
            )

    def test_without_a_workspace_nothing_is_visible(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        """Fail closed: the tenant role with no ``app.org_id`` sees no rows at all."""
        with session_factory() as session:
            session.execute(text(f"SET LOCAL ROLE {TENANT_ROLE}"))
            assert session.scalar(select(func.count()).select_from(Membership)) == 0
            assert session.scalar(select(func.count()).select_from(Organization)) == 0

    def test_scope_survives_commits(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with scoped(session_factory, world.org_b) as session:
            assert session.scalar(select(func.count()).select_from(Organization)) == 1
            session.commit()
            assert session.scalar(select(Organization.name)) == "Chaiwala Foods"
            session.rollback()
            assert session.scalar(select(Organization.name)) == "Chaiwala Foods"

    def test_scope_does_not_leak_into_the_next_user_of_a_pooled_connection(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with scoped(session_factory, world.org_a) as session:
            session.scalar(select(Organization))
            session.commit()
        with session_factory() as system:
            assert system.scalar(text("SELECT current_user")) != TENANT_ROLE
            assert system.scalar(text("SELECT current_setting('app.org_id', true)")) in (None, "")
            assert system.scalar(select(func.count()).select_from(Organization)) == 2

    def test_users_are_global_but_read_only(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with scoped(session_factory, world.org_a) as session:
            assert (session.scalar(select(func.count()).select_from(User)) or 0) >= 2
            with pytest.raises(ProgrammingError, match="permission denied"):
                session.execute(update(User).values(name="x"))


class TestTenantRoleLimits:
    def test_cannot_read_sign_in_challenges(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with (
            scoped(session_factory, world.org_a) as session,
            pytest.raises(ProgrammingError, match="permission denied"),
        ):
            session.scalars(select(LoginChallenge)).all()

    def test_cannot_delete_memberships_or_change_the_activity_log(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with scoped(session_factory, world.org_a) as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                session.execute(delete(Membership))
            session.rollback()
            with pytest.raises(ProgrammingError, match="permission denied"):
                session.execute(update(AuditLog).values(action="x"))


class TestKeepsAnOwner:
    def test_the_last_active_owner_cannot_be_demoted_or_removed(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        for change in ({"role": "analyst"}, {"status": "removed"}):
            with (
                session_factory() as session,
                pytest.raises(DBAPIError, match="at least one active owner"),
            ):
                session.execute(
                    update(Membership)
                    .where(Membership.user_id == world.owner_a.id)
                    .values(**change)
                )

    def test_another_owner_can_be_demoted(
        self, world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with session_factory() as session:
            session.execute(
                update(Membership)
                .where(Membership.user_id == world.analyst_a.id)
                .values(role="owner")
            )
            session.execute(
                update(Membership)
                .where(Membership.user_id == world.owner_a.id)
                .values(role="analyst")
            )
            session.commit()
