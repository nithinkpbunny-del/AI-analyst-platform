"""M16: the Tally bridge: ``tally_devices``, ``tally_syncs`` and ``tally_records``, each with RLS and the
``tenant_isolation`` policy; ``connections.type`` also accepts ``tally``.

Grants for ``app_tenant``: read, add, update, delete (devices are revoked, syncs completed, rows replaced and
removed by full reads).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ROLE = "app_tenant"
_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"
JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = ("tally_records", "tally_syncs", "tally_devices")
TYPES_BEFORE = "'file', 'google_sheets', 'razorpay', 'shopify', 'zoho_books', 'sql_database'"


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


def _connection_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["connection_id"],
        ["connections.id"],
        name=op.f(f"fk_{table}_connection_id_connections"),
        ondelete="CASCADE",
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
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type", "connections", f"type IN ({TYPES_BEFORE}, 'tally')"
    )

    op.create_table(
        "tally_devices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("bridge_version", sa.String(length=32), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paired_by", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_json", JSONB, server_default="{}", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active', 'revoked')", name=op.f("ck_tally_devices_status")),
        _org_fk("tally_devices"),
        _connection_fk("tally_devices"),
        sa.ForeignKeyConstraint(
            ["paired_by"],
            ["users.id"],
            name=op.f("fk_tally_devices_paired_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_devices")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_tally_devices_token_hash")),
    )
    op.create_index(
        "uq_tally_devices_one_active",
        "tally_devices",
        ["connection_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    _protect("tally_devices")

    op.create_table(
        "tally_syncs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("sync_ref", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("company_guid", sa.String(length=64), nullable=False),
        sa.Column("batches_json", JSONB, nullable=False),
        sa.Column("records_json", JSONB, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("mode IN ('full', 'incremental')", name=op.f("ck_tally_syncs_mode")),
        sa.CheckConstraint(
            "status IN ('receiving', 'complete')", name=op.f("ck_tally_syncs_status")
        ),
        _org_fk("tally_syncs"),
        _connection_fk("tally_syncs"),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["tally_devices.id"],
            name=op.f("fk_tally_syncs_device_id_tally_devices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_syncs")),
        sa.UniqueConstraint("device_id", "sync_ref", name="uq_tally_syncs_device_id_sync_ref"),
    )
    op.create_index(
        "ix_tally_syncs_connection_id_created_at", "tally_syncs", ["connection_id", "created_at"]
    )
    _protect("tally_syncs")

    op.create_table(
        "tally_records",
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("dataset", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=220), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("parent_key", sa.String(length=200), nullable=True),
        sa.Column("fields", JSONB, nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _org_fk("tally_records"),
        _connection_fk("tally_records"),
        sa.PrimaryKeyConstraint("connection_id", "dataset", "key", name=op.f("pk_tally_records")),
    )
    op.create_index(
        "ix_tally_records_parent", "tally_records", ["connection_id", "dataset", "parent_key"]
    )
    op.create_index(
        "ix_tally_records_sync", "tally_records", ["connection_id", "dataset", "sync_id"]
    )
    _protect("tally_records")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
    op.execute("DELETE FROM connections WHERE type = 'tally'")
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint("ck_connections_type", "connections", f"type IN ({TYPES_BEFORE})")
