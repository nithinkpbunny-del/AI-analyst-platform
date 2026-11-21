"""M19 (DECISIONS D124, the final D85): a deleted workspace's earlier activity-log entries are kept, anonymised.

The activity log stays append-only. The trigger function now allows exactly one kind of UPDATE, and only when the
transaction asks for it (``SET LOCAL app.audit_anonymise = 'on'``, done by the workspace purge):

* the entry belongs to a workspace that no longer exists (``organizations`` has no row with its ``org_id``);
* who, where and what are removed: ``user_id``, ``ip``, ``user_agent`` and ``target_id`` become NULL and ``details``
  becomes ``{"anonymised": true}``;
* nothing else changes (id, org_id, action, target_type, at).

DELETE and TRUNCATE are still refused, and any other UPDATE too. ``app_tenant`` still has no UPDATE grant, so a
workspace request cannot reach this at all.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANONYMISING_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND current_setting('app.audit_anonymise', true) = 'on'
       AND OLD.org_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM organizations WHERE id = OLD.org_id)
       AND NEW.id = OLD.id
       AND NEW.org_id = OLD.org_id
       AND NEW.action = OLD.action
       AND NEW.target_type IS NOT DISTINCT FROM OLD.target_type
       AND NEW.at = OLD.at
       AND NEW.user_id IS NULL
       AND NEW.ip IS NULL
       AND NEW.user_agent IS NULL
       AND NEW.target_id IS NULL
       AND NEW.details = '{"anonymised": true}'::jsonb
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only (% is not allowed)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% is not allowed)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def upgrade() -> None:
    op.execute(ANONYMISING_FUNCTION)


def downgrade() -> None:
    op.execute(APPEND_ONLY_FUNCTION)
