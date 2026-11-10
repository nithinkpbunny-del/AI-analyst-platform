"""M6 connections: ``connections``, ``connection_grants``, ``datasets``, ``snapshots`` and ``file_versions``, each
with Row-Level Security and the ``tenant_isolation`` policy (DECISIONS D34), plus
``memberships.connection_access`` (DECISIONS D50).

Grants for the ``app_tenant`` role:

* ``connections``, ``datasets``, ``snapshots``: read, add, update and delete.
* ``connection_grants``: read, add and delete (a grant is either there or not).
* ``file_versions``: read, add, update (processing status only) and delete. The stored file itself is never
  changed (BUILD_SPEC 6.3, rule 5).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"

GRANTS = {
    "connections": "SELECT, INSERT, UPDATE, DELETE",
    "connection_grants": "SELECT, INSERT, DELETE",
    "datasets": "SELECT, INSERT, UPDATE, DELETE",
    "snapshots": "SELECT, INSERT, UPDATE, DELETE",
    "file_versions": "SELECT, INSERT, UPDATE, DELETE",
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


def _org(table: str) -> list[object]:
    return [
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=f"fk_{table}_org_id_organizations",
            ondelete="CASCADE",
        ),
    ]


def _connection_fk(table: str) -> list[object]:
    return [
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=f"fk_{table}_connection_id_connections",
            ondelete="CASCADE",
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column(
            "connection_access", sa.String(length=16), server_default="all", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_memberships_connection_access", "memberships", "connection_access IN ('all', 'selected')"
    )

    op.create_table(
        "connections",
        sa.Column("id", sa.UUID(), nullable=False),
        *_org("connections"),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret_ref", sa.UUID(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sync_state", sa.String(length=16), nullable=False),
        sa.Column("sync_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("type IN ('file', 'google_sheets')", name="ck_connections_type"),
        sa.CheckConstraint(
            "status IN ('active', 'error', 'needs_reauth', 'disabled')", name="ck_connections_status"
        ),
        sa.CheckConstraint(
            "sync_state IN ('idle', 'queued', 'running')", name="ck_connections_sync_state"
        ),
        sa.ForeignKeyConstraint(
            ["secret_ref"], ["secrets.id"], name="fk_connections_secret_ref_secrets"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_connections_created_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connections"),
    )
    op.create_index("ix_connections_org_id", "connections", ["org_id"])

    op.create_table(
        "connection_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        *_org("connection_grants"),
        *_connection_fk("connection_grants"),
        sa.Column("user_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_connection_grants_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connection_grants"),
        sa.UniqueConstraint(
            "connection_id", "user_id", name="uq_connection_grants_connection_id_user_id"
        ),
    )
    op.create_index("ix_connection_grants_org_id", "connection_grants", ["org_id"])
    op.create_index("ix_connection_grants_user_id", "connection_grants", ["user_id"])

    op.create_table(
        "datasets",
        sa.Column("id", sa.UUID(), nullable=False),
        *_org("datasets"),
        *_connection_fk("datasets"),
        sa.Column("external_ref", sa.String(length=300), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_count_estimate", sa.BigInteger(), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_datasets"),
        sa.UniqueConstraint(
            "connection_id", "external_ref", name="uq_datasets_connection_id_external_ref"
        ),
    )
    op.create_index("ix_datasets_org_id", "datasets", ["org_id"])

    op.create_table(
        "snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        *_org("snapshots"),
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor", sa.String(length=200), nullable=True),
        sa.Column("source_version", sa.String(length=200), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("quality_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_snapshots_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_snapshots"),
    )
    op.create_index("ix_snapshots_org_id", "snapshots", ["org_id"])
    op.create_index("ix_snapshots_dataset_id_fetched_at", "snapshots", ["dataset_id", "fetched_at"])
    op.create_index("ix_snapshots_expires_at", "snapshots", ["expires_at"])

    op.create_table(
        "file_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        *_org("file_versions"),
        *_connection_fk("file_versions"),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('processing', 'ready', 'failed')", name="ck_file_versions_status"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name="fk_file_versions_uploaded_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_file_versions"),
        sa.UniqueConstraint(
            "connection_id", "version", name="uq_file_versions_connection_id_version"
        ),
    )
    op.create_index("ix_file_versions_org_id", "file_versions", ["org_id"])

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
    op.drop_table("file_versions")
    op.drop_table("snapshots")
    op.drop_table("datasets")
    op.drop_table("connection_grants")
    op.drop_table("connections")
    op.drop_constraint("ck_memberships_connection_access", "memberships", type_="check")
    op.drop_column("memberships", "connection_access")
