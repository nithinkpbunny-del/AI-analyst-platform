"""M10: ``reports``, ``tasks``, ``briefing_settings``, ``briefing_runs`` and ``shares``, each with RLS and the
``tenant_isolation`` policy.

Grants for ``app_tenant``:
* ``reports``, ``tasks``, ``briefing_settings``: read, add, update, delete (people delete their own; jobs update them).
* ``briefing_runs``: read, add, update (the history of what was sent is not deleted by workspace requests).
* ``shares``: read, add, delete (a share is never edited, only removed).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = ("shares", "briefing_runs", "briefing_settings", "tasks", "reports")


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


def _protect(table: str, grants: str) -> None:
    op.create_index(f"ix_{table}_org_id", table, ["org_id"])
    op.execute(f"GRANT {grants} ON {table} TO {TENANT_ROLE}")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} TO {TENANT_ROLE} "
        f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
    )


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("body", JSONB, nullable=False),
        sa.Column("provenance", JSONB, nullable=False),
        sa.Column("quality_warnings", JSONB, nullable=False),
        sa.Column("pdf_key", sa.String(length=300), nullable=True),
        sa.Column("xlsx_key", sa.String(length=300), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "source IN ('task', 'briefing', 'export')", name=op.f("ck_reports_source")
        ),
        _org_fk("reports"),
        _user_fk("reports", "user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
    )
    op.create_index(
        "ix_reports_org_id_user_id_created", "reports", ["org_id", "user_id", "created_at"]
    )
    _protect("reports", "SELECT, INSERT, UPDATE, DELETE")

    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("lens", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress_json", JSONB, nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("result_report_id", sa.UUID(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("token_usage", JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'cancelled')",
            name=op.f("ck_tasks_status"),
        ),
        sa.CheckConstraint(
            "lens IN ('business', 'marketing', 'finance')", name=op.f("ck_tasks_lens")
        ),
        _org_fk("tasks"),
        _user_fk("tasks", "user_id", "CASCADE"),
        sa.ForeignKeyConstraint(
            ["result_report_id"],
            ["reports.id"],
            name=op.f("fk_tasks_result_report_id_reports"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index("ix_tasks_org_id_user_id_created", "tasks", ["org_id", "user_id", "created_at"])
    op.create_index("ix_tasks_org_id_status", "tasks", ["org_id", "status"])
    _protect("tasks", "SELECT, INSERT, UPDATE, DELETE")

    op.create_table(
        "briefing_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'alerts_only', 'off')",
            name=op.f("ck_briefing_settings_frequency"),
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_briefing_settings_weekday")),
        _org_fk("briefing_settings"),
        _user_fk("briefing_settings", "user_id", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_briefing_settings")),
        sa.UniqueConstraint("org_id", "user_id", name="uq_briefing_settings_org_id_user_id"),
    )
    _protect("briefing_settings", "SELECT, INSERT, UPDATE, DELETE")

    op.create_table(
        "briefing_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_id", sa.UUID(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("findings", JSONB, nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'sent', 'skipped', 'failed')",
            name=op.f("ck_briefing_runs_status"),
        ),
        sa.CheckConstraint("kind IN ('scheduled', 'test')", name=op.f("ck_briefing_runs_kind")),
        _org_fk("briefing_runs"),
        _user_fk("briefing_runs", "user_id", "CASCADE"),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_briefing_runs_report_id_reports"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_briefing_runs")),
    )
    # One scheduled run per person and slot (the scheduler is idempotent); "send me one now" runs are not limited.
    op.create_index(
        "uq_briefing_runs_org_id_user_id_slot",
        "briefing_runs",
        ["org_id", "user_id", "scheduled_for"],
        unique=True,
        postgresql_where=sa.text("kind = 'scheduled'"),
    )
    op.create_index(
        "ix_briefing_runs_org_id_user_id_slot",
        "briefing_runs",
        ["org_id", "user_id", "scheduled_for"],
    )
    _protect("briefing_runs", "SELECT, INSERT, UPDATE")

    op.create_table(
        "shares",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("shared_with_user_id", sa.UUID(), nullable=False),
        sa.Column("shared_by", sa.UUID(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "object_type IN ('message', 'report')", name=op.f("ck_shares_object_type")
        ),
        _org_fk("shares"),
        _user_fk("shares", "shared_with_user_id", "CASCADE"),
        _user_fk("shares", "shared_by", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shares")),
        sa.UniqueConstraint(
            "object_type", "object_id", "shared_with_user_id", name="uq_shares_object_recipient"
        ),
    )
    op.create_index("ix_shares_org_id_shared_with", "shares", ["org_id", "shared_with_user_id"])
    _protect("shares", "SELECT, INSERT, DELETE")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
