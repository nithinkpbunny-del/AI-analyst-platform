"""M7 business dictionary: ``dictionary_versions`` (RLS + ``tenant_isolation``), ``datasets.profile_json`` and the
``dictionary`` kind of AI usage.

Grants for ``app_tenant``: read, add, update and delete (a discarded draft is deleted; confirmed versions are only
ever superseded in place).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "dictionary_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("diff_json", JSONB, nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=True),
        sa.Column("ai_input_hash", sa.String(length=64), nullable=True),
        sa.Column("ai_json", JSONB, nullable=True),
        sa.Column("notes_json", JSONB, nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("confirmed_by", sa.UUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'superseded')", name=op.f("ck_dictionary_versions_status")
        ),
        sa.CheckConstraint(
            "origin IN ('initial', 'schema_change', 'replacement', 'redraft')",
            name=op.f("ck_dictionary_versions_origin"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_dictionary_versions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_dictionary_versions_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
            name=op.f("fk_dictionary_versions_confirmed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dictionary_versions")),
    )
    op.create_index("ix_dictionary_versions_org_id", "dictionary_versions", ["org_id"])
    op.create_index(
        "uq_dictionary_versions_org_id_version",
        "dictionary_versions",
        ["org_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_dictionary_versions_one_draft",
        "dictionary_versions",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON dictionary_versions TO {TENANT_ROLE}")
    op.execute("ALTER TABLE dictionary_versions ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON dictionary_versions TO {TENANT_ROLE} "
        f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
    )

    op.add_column(
        "datasets", sa.Column("profile_json", JSONB, server_default="{}", nullable=False)
    )

    op.drop_constraint(op.f("ck_usage_events_kind"), "usage_events", type_="check")
    op.create_check_constraint(
        op.f("ck_usage_events_kind"),
        "usage_events",
        "kind IN ('question', 'task', 'briefing', 'check', 'dictionary')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM usage_events WHERE kind = 'dictionary'")
    op.drop_constraint(op.f("ck_usage_events_kind"), "usage_events", type_="check")
    op.create_check_constraint(
        op.f("ck_usage_events_kind"),
        "usage_events",
        "kind IN ('question', 'task', 'briefing', 'check')",
    )
    op.drop_column("datasets", "profile_json")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON dictionary_versions")
    op.drop_table("dictionary_versions")
