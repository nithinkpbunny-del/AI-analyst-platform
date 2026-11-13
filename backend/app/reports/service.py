"""Answers and reports as documents, their PDF/Excel files, and sharing (BUILD_SPEC X3, R10; M10).

* ``owned_message`` / ``owned_report``: an item that belongs to the caller (their conversation, or a report made for
  them). ``shared_item``: an item shared with the caller. Anything else is "not found" (P3).
* ``document_for_*``: the ``Document`` the PDF and Excel writers render.
* ``files``: PDF or Excel bytes. A report's files are kept in object storage (``pdf_key``/``xlsx_key``) once made; an
  answer's are made on each export (nothing extra is kept, BUILD_SPEC 6.6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.data.objects import ObjectStore
from app.exports import document as documents
from app.exports import excel, pdf
from app.models import Conversation, Message, Organization, Report, Share, User
from app.reports.access import Permit, connections_of, permit

Format = Literal["pdf", "xlsx"]
FORMATS: tuple[Format, ...] = ("pdf", "xlsx")
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_RECIPIENTS = 20


# ------------------------------------------------------------------------------------------- lookups
def owned_message(
    db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    """A finished answer in one of the caller's conversations."""
    return db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == message_id,
            Message.org_id == org_id,
            Message.role == "assistant",
            Message.status == "done",
            Conversation.user_id == user_id,
        )
    )


def owned_report(
    db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID, report_id: uuid.UUID
) -> Report | None:
    return db.scalar(
        select(Report).where(
            Report.id == report_id, Report.org_id == org_id, Report.user_id == user_id
        )
    )


def message_owner(db: DbSession, message: Message) -> uuid.UUID | None:
    conversation = db.get(Conversation, message.conversation_id)
    return conversation.user_id if conversation else None


def question_of(db: DbSession, message: Message) -> str:
    """The question an answer answers (the person's message just before it)."""
    found = db.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return found or ""


@dataclass(frozen=True)
class SharedItem:
    share: Share
    message: Message | None
    report: Report | None

    @property
    def provenance(self) -> dict[str, Any]:
        item = self.message or self.report
        return dict(item.provenance) if item is not None else {}

    def is_shared_with(self, user_id: uuid.UUID) -> bool:
        """``SharedResource`` (``app.auth.permissions``): Viewers may export only items shared with them."""
        return self.share.shared_with_user_id == user_id


def shared_item(
    db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID, share_id: uuid.UUID
) -> SharedItem | None:
    """An item shared with the caller that they may still see (their connection access covers its sources)."""
    share = db.scalar(
        select(Share).where(
            Share.id == share_id, Share.org_id == org_id, Share.shared_with_user_id == user_id
        )
    )
    if share is None:
        return None
    item = _object(db, org_id, share.object_type, share.object_id)
    if item is None:
        return None
    caller = permit(db, org_id, user_id)
    if caller is None or not caller.covers(connections_of(dict(item.provenance))):
        return None
    if isinstance(item, Message):
        return SharedItem(share, item, None)
    return SharedItem(share, None, item)


def _object(
    db: DbSession, org_id: uuid.UUID, kind: str, object_id: uuid.UUID
) -> Message | Report | None:
    if kind == "message":
        message = db.scalar(
            select(Message).where(
                Message.id == object_id,
                Message.org_id == org_id,
                Message.role == "assistant",
                Message.status == "done",
            )
        )
        return message
    return db.scalar(select(Report).where(Report.id == object_id, Report.org_id == org_id))


# ----------------------------------------------------------------------------------------- documents
def _org(db: DbSession, org_id: uuid.UUID) -> Organization:
    org = db.get(Organization, org_id)
    assert org is not None
    return org


def document_for_message(db: DbSession, message: Message) -> documents.Document:
    org = _org(db, message.org_id)
    question = question_of(db, message)
    return documents.from_answer(
        title=_title(question) or "Answer",
        subtitle="Answer from Analyst",
        org_name=org.name,
        timezone=org.timezone,
        created_at=message.finished_at or message.created_at,
        question=question,
        answer=dict(message.answer_json),
        provenance=dict(message.provenance),
        quality=list(message.quality_warnings),
    )


def document_for_report(db: DbSession, report: Report) -> documents.Document:
    org = _org(db, report.org_id)
    body = dict(report.body)
    if body.get("kind") == "briefing":
        return documents.from_briefing(
            title=report.title,
            org_name=org.name,
            timezone=org.timezone,
            created_at=report.created_at,
            body=body,
            provenance=dict(report.provenance),
            quality=list(report.quality_warnings),
        )
    return documents.from_answer(
        title=report.title,
        subtitle="Long analysis" if report.source == "task" else "Report",
        org_name=org.name,
        timezone=org.timezone,
        created_at=report.created_at,
        question=body.get("question"),
        answer=dict(body.get("answer") or {}),
        provenance=dict(report.provenance),
        quality=list(report.quality_warnings),
    )


def _title(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render(document: documents.Document, fmt: Format) -> bytes:
    return pdf.render(document) if fmt == "pdf" else excel.render(document)


def report_file(db: DbSession, objects: ObjectStore, report: Report, fmt: Format) -> bytes:
    """A report's PDF or Excel file, made once and then read from storage."""
    key = report.pdf_key if fmt == "pdf" else report.xlsx_key
    if key and objects.exists(key):
        return objects.get(key)
    data = render(document_for_report(db, report), fmt)
    key = f"orgs/{report.org_id}/reports/{report.id}.{fmt}"
    objects.put(key, data)
    if fmt == "pdf":
        report.pdf_key = key
    else:
        report.xlsx_key = key
    return data


def filename(title: str, fmt: Format) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else " " for c in title)
    safe = "-".join(safe.split())[:60].strip("-").lower() or "report"
    return f"{safe}.{fmt}"


def delete_report_files(objects: ObjectStore, report: Report) -> None:
    for key in (report.pdf_key, report.xlsx_key):
        if key:
            objects.delete(key)


# -------------------------------------------------------------------------------------------- shares
@dataclass(frozen=True)
class Candidate:
    user: User
    role: str
    can_see: bool


def candidates(
    db: DbSession, org_id: uuid.UUID, owner_id: uuid.UUID, provenance: dict[str, Any]
) -> list[Candidate]:
    """Active members (other than the owner) and whether their access covers the item's sources."""
    from app.models import Membership

    needed = connections_of(provenance)
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.org_id == org_id,
            Membership.status == "active",
            Membership.user_id != owner_id,
        )
        .order_by(User.name, User.email)
    ).all()
    out = []
    for membership, user in rows:
        member: Permit | None = permit(db, org_id, membership.user_id)
        out.append(Candidate(user, membership.role, bool(member and member.covers(needed))))
    return out


def shares_of(db: DbSession, org_id: uuid.UUID, kind: str, object_id: uuid.UUID) -> list[Share]:
    return list(
        db.scalars(
            select(Share)
            .where(Share.org_id == org_id, Share.object_type == kind, Share.object_id == object_id)
            .order_by(Share.created_at)
        )
    )
