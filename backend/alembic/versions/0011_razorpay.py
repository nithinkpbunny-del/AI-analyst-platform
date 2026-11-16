"""M11: Razorpay connections.

Only the allowed connection types change: ``connections.type`` also accepts ``razorpay``. No new table, so no new
RLS policy (``connections`` already has one). Credentials live in ``secrets`` (the key secret) and
``connections.config`` (the key id, which is not a secret).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type", "connections", "type IN ('file', 'google_sheets', 'razorpay')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM connections WHERE type = 'razorpay'")
    op.drop_constraint("ck_connections_type", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_type", "connections", "type IN ('file', 'google_sheets')"
    )
