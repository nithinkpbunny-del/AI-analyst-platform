"""M6 fix: a new file version that differs from the one in use waits for confirmation (DECISIONS D58).

``file_versions.status`` gains ``needs_confirmation`` (and room for it), and ``file_versions.review_json`` holds
how the version differs (sheets or columns gone, types changed).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "file_versions", "status", type_=sa.String(length=24), existing_type=sa.String(length=16)
    )
    op.drop_constraint("ck_file_versions_status", "file_versions", type_="check")
    op.create_check_constraint(
        "ck_file_versions_status",
        "file_versions",
        "status IN ('processing', 'ready', 'failed', 'needs_confirmation')",
    )
    op.add_column(
        "file_versions",
        sa.Column(
            "review_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("file_versions", "review_json")
    op.execute("UPDATE file_versions SET status = 'failed' WHERE status = 'needs_confirmation'")
    op.drop_constraint("ck_file_versions_status", "file_versions", type_="check")
    op.create_check_constraint(
        "ck_file_versions_status", "file_versions", "status IN ('processing', 'ready', 'failed')"
    )
    op.alter_column(
        "file_versions", "status", type_=sa.String(length=16), existing_type=sa.String(length=24)
    )
