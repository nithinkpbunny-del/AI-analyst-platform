"""The workspace's data as a ZIP (BUILD_SPEC 6.6, DPDP right to access; Owners only).

What is in it (JSON files, plus the activity log as CSV): the workspace, its members, its connections and their tables
(metadata and column descriptions only), the business dictionary's versions, every conversation with its answers,
saved questions, long tasks, reports and briefings, briefing settings and AI usage.

What is not in it, on purpose: secrets of any kind (connection credentials, AI keys; not even encrypted), cached
copies of the data sources (they are copies of the customer's own systems and expire within a day) and uploaded files
(the Owner has the originals; they stay downloadable from the connection while it exists).
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.audit.query import AuditFilters, export_csv
from app.models import (
    BriefingRun,
    BriefingSetting,
    Connection,
    Conversation,
    Dataset,
    DictionaryVersion,
    Membership,
    Message,
    Organization,
    Report,
    SavedQuestion,
    Share,
    Task,
    UsageEvent,
    User,
)

README = """Your workspace's data from Analyst

Made: {made}
Workspace: {name}

Files:
- workspace.json: name, industry, time zone, plan and thresholds.
- members.json: people, their roles and status.
- connections.json: connections and their tables (names, columns, row counts). No credentials.
- dictionary.json: every version of the business dictionary.
- conversations.json: questions and answers, with the sources behind each figure.
- saved_questions.json, tasks.json, reports.json (long-task reports and briefings), shares.json.
- briefings.json: briefing settings and what was sent when.
- usage.json: AI calls (tokens, estimated cost), no content.
- activity_log.csv: the activity log.

Not included: passwords, connection credentials and AI keys (never exported); cached copies of your data sources
(they expire within a day); uploaded files (you have the originals).
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode()


def build(db: DbSession, org_id: uuid.UUID, now: datetime) -> bytes:
    org = db.get(Organization, org_id)
    assert org is not None
    files: dict[str, Any] = {
        "workspace.json": {
            "id": str(org.id),
            "name": org.name,
            "industry": org.industry_template,
            "timezone": org.timezone,
            "currency": org.currency,
            "plan": org.plan,
            "monthly_question_limit": org.monthly_question_limit,
            "impact_threshold_pct": float(org.impact_threshold_pct),
            "impact_threshold_inr": float(org.impact_threshold_inr),
            "created_at": _iso(org.created_at),
            "deletion_requested_at": _iso(org.deletion_requested_at),
        },
        "members.json": [
            {
                "name": user.name,
                "email": user.email,
                "role": m.role,
                "status": m.status,
                "connection_access": m.connection_access,
                "since": _iso(m.created_at),
            }
            for m, user in db.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(Membership.org_id == org_id)
                .order_by(Membership.created_at)
            ).all()
        ],
        "connections.json": [
            {
                "id": str(c.id),
                "type": c.type,
                "name": c.name,
                "status": c.status,
                "created_at": _iso(c.created_at),
                "last_success_at": _iso(c.last_success_at),
                "tables": [
                    {
                        "name": d.name,
                        "source": d.external_ref,
                        "rows": d.row_count_estimate,
                        "columns": d.schema_json,
                    }
                    for d in db.scalars(select(Dataset).where(Dataset.connection_id == c.id))
                ],
            }
            for c in db.scalars(select(Connection).where(Connection.org_id == org_id))
        ],
        "dictionary.json": [
            {
                "version": v.version,
                "status": v.status,
                "content": v.content,
                "created_at": _iso(v.created_at),
            }
            for v in db.scalars(
                select(DictionaryVersion)
                .where(DictionaryVersion.org_id == org_id)
                .order_by(DictionaryVersion.version)
            )
        ],
        "conversations.json": [
            {
                "id": str(c.id),
                "person": str(c.user_id),
                "title": c.title,
                "lens": c.lens,
                "created_at": _iso(c.created_at),
                "messages": [
                    {
                        "role": m.role,
                        "text": m.content,
                        "status": m.status,
                        "answer": m.answer_json or None,
                        "sources": m.provenance or None,
                        "at": _iso(m.created_at),
                    }
                    for m in db.scalars(
                        select(Message)
                        .where(Message.conversation_id == c.id)
                        .order_by(Message.created_at)
                    )
                ],
            }
            for c in db.scalars(
                select(Conversation)
                .where(Conversation.org_id == org_id)
                .order_by(Conversation.created_at)
            )
        ],
        "saved_questions.json": [
            {"person": str(q.user_id), "text": q.text, "lens": q.lens, "at": _iso(q.created_at)}
            for q in db.scalars(select(SavedQuestion).where(SavedQuestion.org_id == org_id))
        ],
        "tasks.json": [
            {
                "person": str(t.user_id),
                "prompt": t.prompt,
                "status": t.status,
                "report": str(t.result_report_id) if t.result_report_id else None,
                "at": _iso(t.created_at),
                "finished_at": _iso(t.finished_at),
            }
            for t in db.scalars(select(Task).where(Task.org_id == org_id))
        ],
        "reports.json": [
            {
                "id": str(r.id),
                "person": str(r.user_id) if r.user_id else None,
                "title": r.title,
                "source": r.source,
                "body": r.body,
                "sources": r.provenance,
                "at": _iso(r.created_at),
            }
            for r in db.scalars(select(Report).where(Report.org_id == org_id))
        ],
        "shares.json": [
            {
                "type": s.object_type,
                "item": str(s.object_id),
                "shared_with": str(s.shared_with_user_id),
                "shared_by": str(s.shared_by) if s.shared_by else None,
                "at": _iso(s.created_at),
            }
            for s in db.scalars(select(Share).where(Share.org_id == org_id))
        ],
        "briefings.json": {
            "settings": [
                {
                    "person": str(s.user_id),
                    "enabled": s.enabled,
                    "time": s.local_time.isoformat(timespec="minutes"),
                    "frequency": s.frequency,
                    "language": s.language,
                    "timezone": s.timezone,
                }
                for s in db.scalars(select(BriefingSetting).where(BriefingSetting.org_id == org_id))
            ],
            "sent": [
                {
                    "person": str(r.user_id),
                    "slot": _iso(r.scheduled_for),
                    "status": r.status,
                    "sent_at": _iso(r.sent_at),
                    "report": str(r.report_id) if r.report_id else None,
                }
                for r in db.scalars(select(BriefingRun).where(BriefingRun.org_id == org_id))
            ],
        },
        "usage.json": [
            {
                "person": str(u.user_id) if u.user_id else None,
                "kind": u.kind,
                "provider": u.provider,
                "model": u.model,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "est_cost_usd": float(u.est_cost_usd) if u.est_cost_usd is not None else None,
                "key_source": u.key_source,
                "at": _iso(u.created_at),
            }
            for u in db.scalars(select(UsageEvent).where(UsageEvent.org_id == org_id))
        ],
    }
    csv_text, _, _ = export_csv(
        db, org_id=org_id, timezone=org.timezone, filters=AuditFilters(), only_user_id=None
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt", README.format(made=now.isoformat(timespec="seconds"), name=org.name)
        )
        for name, content in files.items():
            archive.writestr(name, _dump(content))
        archive.writestr("activity_log.csv", csv_text)
    return out.getvalue()
