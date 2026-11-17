"""M13: Zoho Books connections.

Only the allowed connection types change: ``connections.type`` also accepts ``zoho_books``. No new table, so no new
RLS policy (``connections`` already has one). Credentials live in ``secrets`` (the refresh token) and
``connections.config`` (data centre, organisation id and name, granted scopes: not secrets).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type",
        "connections",
        "type IN ('file', 'google_sheets', 'razorpay', 'shopify', 'zoho_books')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM connections WHERE type = 'zoho_books'")
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type",
        "connections",
        "type IN ('file', 'google_sheets', 'razorpay', 'shopify')",
    )
