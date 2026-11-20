"""M18: ``billing_events`` (the hooks a payment provider will use later; nothing is charged), with RLS and the
``tenant_isolation`` policy.

Grants for ``app_tenant``: read and add only, so a workspace request can never change or remove its billing history
(like ``usage_events``). Events are deleted with the workspace (``ON DELETE CASCADE``).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "billing_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=True),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("delivery", sa.String(length=12), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('plan_changed', 'usage_warning', 'usage_limit_reached', 'usage_summary')",
            name=op.f("ck_billing_events_kind"),
        ),
        sa.CheckConstraint(
            "delivery IN ('not_sent', 'sent', 'failed')", name=op.f("ck_billing_events_delivery")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_billing_events_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_billing_events_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_events")),
        sa.UniqueConstraint("org_id", "kind", "period", name="uq_billing_events_org_id_kind_period"),
    )
    op.create_index(op.f("ix_billing_events_org_id"), "billing_events", ["org_id"])
    op.create_index(
        "ix_billing_events_org_id_created_at", "billing_events", ["org_id", "created_at"]
    )
    op.execute(f"GRANT SELECT, INSERT ON billing_events TO {TENANT_ROLE}")
    op.execute("ALTER TABLE billing_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON billing_events TO {TENANT_ROLE} "
        f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON billing_events")
    op.drop_table("billing_events")
