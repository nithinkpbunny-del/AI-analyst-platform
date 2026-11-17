"""M12: Shopify connections.

Only the allowed connection types change: ``connections.type`` also accepts ``shopify``. No new table, so no new
RLS policy (``connections`` already has one). Credentials live in ``secrets`` (the offline access token) and
``connections.config`` (the store address and granted scopes, which are not secrets).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type",
        "connections",
        "type IN ('file', 'google_sheets', 'razorpay', 'shopify')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM connections WHERE type = 'shopify'")
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type", "connections", "type IN ('file', 'google_sheets', 'razorpay')"
    )
