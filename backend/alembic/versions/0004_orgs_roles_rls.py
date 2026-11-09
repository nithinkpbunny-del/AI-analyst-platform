"""M4 orgs, roles, invites, RLS: the invitations table, the restricted ``app_tenant`` role and Row-Level
Security on every tenant-owned table, plus a backstop trigger for "at least one active Owner per workspace".

How tenant isolation works (BUILD_SPEC 6.2, DECISIONS D34):

* Tenant-scoped requests run each transaction as ``SET LOCAL ROLE app_tenant`` with
  ``set_config('app.org_id', <org>, true)``. ``app_tenant`` is not a superuser, does not own the tables and has
  no BYPASSRLS, so the ``tenant_isolation`` policies apply: it sees and writes rows of that one organisation only.
  When ``app.org_id`` is missing, the policy compares with NULL and matches nothing (fails closed).
* Sign-in and other person-scoped work (a person can belong to several workspaces) runs as the connecting role,
  which owns the tables. RLS is enabled but not forced, so the owner is not filtered.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"

# Tables with an org_id column (BUILD_SPEC 5: every tenant-owned table gets the policy). organizations uses id.
TENANT_TABLES = (
    "memberships",
    "invitations",
    "sessions",
    "consents",
    "audit_log",
    "login_challenges",
)

# What a tenant-scoped transaction may do. Anything not listed is refused by Postgres itself.
GRANTS = {
    "organizations": "SELECT, UPDATE",
    "memberships": "SELECT, INSERT, UPDATE",
    "invitations": "SELECT, INSERT, UPDATE",
    "sessions": "SELECT, UPDATE",
    "consents": "SELECT, INSERT",
    "audit_log": "SELECT, INSERT",
    # Global tables (no org_id): read to show names and 2FA status; refresh tokens are revoked on removal.
    "users": "SELECT",
    "two_factor": "SELECT",
    "refresh_tokens": "SELECT, UPDATE",
}

CREATE_ROLE = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{TENANT_ROLE}') THEN
        CREATE ROLE {TENANT_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$
"""

# Roles are shared by every database in the cluster: drop it only when nothing else still uses it.
DROP_ROLE = f"""
DO $$
BEGIN
    DROP ROLE IF EXISTS {TENANT_ROLE};
EXCEPTION WHEN dependent_objects_still_exist THEN
    NULL;
END
$$
"""

LAST_OWNER_FUNCTION = """
CREATE FUNCTION memberships_keep_an_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM memberships
        WHERE org_id = OLD.org_id AND role = 'owner' AND status = 'active'
    ) THEN
        RAISE EXCEPTION 'a workspace needs at least one active owner'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('analyst', 'viewer')", name=op.f("ck_invitations_role")),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name=op.f("fk_invitations_accepted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name=op.f("fk_invitations_invited_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_invitations_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
    )
    op.create_index(op.f("ix_invitations_org_id"), "invitations", ["org_id"], unique=False)
    op.create_index(
        "uq_invitations_open_email",
        "invitations",
        ["org_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    # login_challenges.org_id had no index yet; RLS filters on it.
    op.create_index(
        op.f("ix_login_challenges_org_id"), "login_challenges", ["org_id"], unique=False
    )

    op.execute(CREATE_ROLE)
    # A superuser may switch to any role; a non-superuser connecting role needs membership (production, M19).
    op.execute(f"GRANT {TENANT_ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {TENANT_ROLE}")
    for table, privileges in GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO {TENANT_ROLE}")

    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON organizations TO {TENANT_ROLE} "
        f"USING (id = {_ORG}) WITH CHECK (id = {_ORG})"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {TENANT_ROLE} "
            f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
        )

    op.execute(LAST_OWNER_FUNCTION)
    op.execute(
        "CREATE TRIGGER memberships_keep_an_owner AFTER UPDATE OF role, status ON memberships "
        "FOR EACH ROW WHEN (OLD.role = 'owner' AND OLD.status = 'active') "
        "EXECUTE FUNCTION memberships_keep_an_owner()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS memberships_keep_an_owner ON memberships")
    op.execute("DROP FUNCTION IF EXISTS memberships_keep_an_owner()")
    for table in ("organizations", *TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP OWNED BY {TENANT_ROLE}")  # this database's grants only
    op.execute(DROP_ROLE)
    op.drop_index(op.f("ix_login_challenges_org_id"), table_name="login_challenges")
    op.drop_index("uq_invitations_open_email", table_name="invitations")
    op.drop_index(op.f("ix_invitations_org_id"), table_name="invitations")
    op.drop_table("invitations")
