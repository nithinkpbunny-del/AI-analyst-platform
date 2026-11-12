"""M8 Ask: ``conversations``, ``messages`` and ``saved_questions``, each with RLS and the ``tenant_isolation`` policy.

Grants for ``app_tenant``: read, add, update and delete on all three (a person deletes their own conversations and
saved questions; answers are updated while the background job works on them).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
JSONB = postgresql.JSONB(astext_type=sa.Text())
LENS_CHECK = "lens IN ('business', 'marketing', 'finance')"


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


def _protect(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {TENANT_ROLE}")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} TO {TENANT_ROLE} "
        f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
    )


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("lens", sa.String(length=16), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(LENS_CHECK, name=op.f("ck_conversations_lens")),
        _org_fk("conversations"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_conversations_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index("ix_conversations_org_id", "conversations", ["org_id"])
    op.create_index(
        "ix_conversations_org_id_user_id_last",
        "conversations",
        ["org_id", "user_id", "last_message_at"],
    )
    _protect("conversations")

    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("lens", sa.String(length=16), nullable=False),
        sa.Column("answer_json", JSONB, nullable=False),
        sa.Column("progress_json", JSONB, nullable=False),
        sa.Column("tool_trace", JSONB, nullable=False),
        sa.Column("provenance", JSONB, nullable=False),
        sa.Column("quality_warnings", JSONB, nullable=False),
        sa.Column("token_usage", JSONB, nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("dictionary_version", sa.Integer(), nullable=True),
        sa.Column("reused_from", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_messages_role")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name=op.f("ck_messages_status")
        ),
        _org_fk("messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_messages_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index("ix_messages_org_id", "messages", ["org_id"])
    op.create_index(
        "ix_messages_conversation_id_created_at", "messages", ["conversation_id", "created_at"]
    )
    _protect("messages")

    op.create_table(
        "saved_questions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.Column("lens", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(LENS_CHECK, name=op.f("ck_saved_questions_lens")),
        _org_fk("saved_questions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_saved_questions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_questions")),
    )
    op.create_index("ix_saved_questions_org_id", "saved_questions", ["org_id"])
    op.create_index(
        "ix_saved_questions_org_id_user_id", "saved_questions", ["org_id", "user_id"]
    )
    _protect("saved_questions")


def downgrade() -> None:
    for table in ("saved_questions", "messages", "conversations"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
