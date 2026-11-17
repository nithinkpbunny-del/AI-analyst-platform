"""M14: PostgreSQL / MySQL connections.

Only the allowed connection types change: ``connections.type`` also accepts ``sql_database``. No new table, so no new
RLS policy (``connections`` already has one). The password lives in ``secrets``; engine, host, port, database, user,
SSL mode and the exposed tables live in ``connections.config`` (not secrets).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type",
        "connections",
        "type IN ('file', 'google_sheets', 'razorpay', 'shopify', 'zoho_books', 'sql_database')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM connections WHERE type = 'sql_database'")
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type",
        "connections",
        "type IN ('file', 'google_sheets', 'razorpay', 'shopify', 'zoho_books')",
    )
