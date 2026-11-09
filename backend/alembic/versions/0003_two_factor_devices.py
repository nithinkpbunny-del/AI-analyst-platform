"""M3 two-step verification, Google sign-in and devices: two_factor, two_factor_codes, login_challenges,
known_devices, and the "This wasn't me" link purpose on user_tokens.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PURPOSES_M2 = "purpose IN ('verify_email', 'reset_password')"
_PURPOSES_M3 = "purpose IN ('verify_email', 'reset_password', 'secure_account')"


def upgrade() -> None:
    op.create_table(
        "known_devices",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("device_id_hash", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_known_devices_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_known_devices")),
        sa.UniqueConstraint("user_id", "device_id_hash", name=op.f("uq_known_devices_user_id")),
    )
    op.create_index(op.f("ix_known_devices_user_id"), "known_devices", ["user_id"], unique=False)
    op.create_table(
        "login_challenges",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("auth_method", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("codes_sent", sa.Integer(), nullable=False),
        sa.Column("last_code_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "auth_method IN ('password', 'google')", name=op.f("ck_login_challenges_auth_method")
        ),
        sa.CheckConstraint(
            "kind IN ('two_factor', 'two_factor_setup', 'google_handoff', 'google_signup')",
            name=op.f("ck_login_challenges_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_login_challenges_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_login_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_challenges")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_challenges_token_hash")),
    )
    op.create_index(
        op.f("ix_login_challenges_user_id"), "login_challenges", ["user_id"], unique=False
    )
    op.create_table(
        "two_factor",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("recovery_codes_hash", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_method", sa.String(length=8), nullable=True),
        sa.Column("pending_totp_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "method IS NULL OR method IN ('totp', 'email')", name=op.f("ck_two_factor_method")
        ),
        sa.CheckConstraint(
            "pending_method IS NULL OR pending_method IN ('totp', 'email')",
            name=op.f("ck_two_factor_pending_method"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_two_factor_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_two_factor")),
    )
    op.create_table(
        "two_factor_codes",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('login', 'setup', 'step_up')", name=op.f("ck_two_factor_codes_purpose")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_two_factor_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_two_factor_codes")),
    )
    op.create_index(
        op.f("ix_two_factor_codes_user_id"), "two_factor_codes", ["user_id"], unique=False
    )
    op.drop_constraint(op.f("ck_user_tokens_purpose"), "user_tokens", type_="check")
    op.create_check_constraint("purpose", "user_tokens", _PURPOSES_M3)


def downgrade() -> None:
    op.execute("DELETE FROM user_tokens WHERE purpose = 'secure_account'")
    op.drop_constraint(op.f("ck_user_tokens_purpose"), "user_tokens", type_="check")
    op.create_check_constraint("purpose", "user_tokens", _PURPOSES_M2)
    op.drop_index(op.f("ix_two_factor_codes_user_id"), table_name="two_factor_codes")
    op.drop_table("two_factor_codes")
    op.drop_table("two_factor")
    op.drop_index(op.f("ix_login_challenges_user_id"), table_name="login_challenges")
    op.drop_table("login_challenges")
    op.drop_index(op.f("ix_known_devices_user_id"), table_name="known_devices")
    op.drop_table("known_devices")
