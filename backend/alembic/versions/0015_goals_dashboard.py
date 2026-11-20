"""M15: ``goals`` and ``dashboard_tiles``, each with RLS and the ``tenant_isolation`` policy.

Grants for ``app_tenant``: read, add, update, delete on both (goals and tiles are edited and removed by people).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = ("dashboard_tiles", "goals")


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id"],
        ["organizations.id"],
        name=op.f(f"fk_{table}_org_id_organizations"),
        ondelete="CASCADE",
    )


def _user_fk(table: str, column: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}_users"), ondelete=ondelete
    )


def _protect(table: str) -> None:
    op.create_index(f"ix_{table}_org_id", table, ["org_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {TENANT_ROLE}")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} TO {TENANT_ROLE} "
        f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
    )


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("filters", JSONB, nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "direction IN ('at_least', 'at_most')", name=op.f("ck_goals_direction")
        ),
        sa.CheckConstraint("period_end >= period_start", name=op.f("ck_goals_period")),
        _org_fk("goals"),
        _user_fk("goals", "created_by", "SET NULL"),
        _user_fk("goals", "updated_by", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goals")),
    )
    op.create_index("ix_goals_org_id_period", "goals", ["org_id", "period_end"])
    _protect("goals")

    op.create_table(
        "dashboard_tiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("refresh_minutes", sa.Integer(), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "refresh_minutes BETWEEN 5 AND 1440", name=op.f("ck_dashboard_tiles_refresh_minutes")
        ),
        _org_fk("dashboard_tiles"),
        _user_fk("dashboard_tiles", "user_id", "CASCADE"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_dashboard_tiles_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_tiles")),
    )
    op.create_index(
        "ix_dashboard_tiles_org_id_user_id_position",
        "dashboard_tiles",
        ["org_id", "user_id", "position"],
    )
    op.create_index("ix_dashboard_tiles_org_id_shared", "dashboard_tiles", ["org_id", "shared"])
    _protect("dashboard_tiles")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
