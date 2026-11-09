"""M5 secrets, LLM layer, plans, usage: the ``secrets``, ``llm_keys`` and ``usage_events`` tables, each with
Row-Level Security and the ``tenant_isolation`` policy (DECISIONS D34).

Grants for the ``app_tenant`` role:

* ``secrets``: read, add and delete; never updated in place (replacing a key writes a new secret).
* ``llm_keys``: read, add, update and delete (one per workspace).
* ``usage_events``: read and add only, so a workspace request can never change or remove its usage history.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"

GRANTS = {
    "secrets": "SELECT, INSERT, DELETE",
    "llm_keys": "SELECT, INSERT, UPDATE, DELETE",
    "usage_events": "SELECT, INSERT",
}


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.SmallInteger(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_secrets_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_secrets")),
    )
    op.create_index(op.f("ix_secrets_org_id"), "secrets", ["org_id"], unique=False)

    op.create_table(
        "llm_keys",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("secret_ref", sa.UUID(), nullable=False),
        sa.Column("last4", sa.String(length=4), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("provider IN ('gemini', 'claude')", name=op.f("ck_llm_keys_provider")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_llm_keys_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["secret_ref"],
            ["secrets.id"],
            name=op.f("fk_llm_keys_secret_ref_secrets"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_llm_keys_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_keys")),
        sa.UniqueConstraint("org_id", name=op.f("uq_llm_keys_org_id")),
    )

    op.create_table(
        "usage_events",
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("est_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("key_source", sa.String(length=16), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('question', 'task', 'briefing', 'check')", name=op.f("ck_usage_events_kind")
        ),
        sa.CheckConstraint(
            "key_source IN ('managed', 'byok')", name=op.f("ck_usage_events_key_source")
        ),
        sa.CheckConstraint(
            "provider IN ('gemini', 'claude')", name=op.f("ck_usage_events_provider")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_usage_events_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_usage_events_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index(
        "ix_usage_events_org_id_created_at", "usage_events", ["org_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_usage_events_user_id"), "usage_events", ["user_id"], unique=False)

    for table, privileges in GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO {TENANT_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {TENANT_ROLE} "
            f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
        )


def downgrade() -> None:
    for table in GRANTS:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_index(op.f("ix_usage_events_user_id"), table_name="usage_events")
    op.drop_index("ix_usage_events_org_id_created_at", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("llm_keys")
    op.drop_index(op.f("ix_secrets_org_id"), table_name="secrets")
    op.drop_table("secrets")
