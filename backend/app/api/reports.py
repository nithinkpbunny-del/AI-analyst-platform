"""Reports, exports and sharing (BUILD_SPEC X3, R10; M10).

* ``/reports``: my reports (long-task results and briefings), one report, and its PDF/Excel export.
* ``/conversations/{id}/messages/{id}/export``: an answer of mine as PDF or Excel.
* ``/shares``: share one of my answers or reports with members of the workspace, see who has it, take it back.
* ``/shared``: what others shared with me, and its export.

Sharing respects the recipient's permissions: someone whose connection access does not cover every source an item
used cannot be chosen, and an item stops showing to them if their access shrinks later. Viewers may export only items
shared with them (BUILD_SPEC 6.2). Someone else's item, or one in another workspace, answers 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status
from sqlalchemy import select

from app.api.auth import send_queued
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission, require
from app.email import templates
from app.models import Message, Report, Share, User
from app.reports import service as reports
from app.schemas.reports import (
    Candidate,
    ReportList,
    ReportOut,
    ReportSummary,
    SharedList,
    SharedOut,
    SharedSummary,
    ShareList,
    ShareOut,
    ShareRequest,
)

router = APIRouter(tags=["reports and sharing"])

Viewing = Annotated[Tenant, requires(Permission.SHARED_VIEW)]
Exporting = Annotated[Tenant, requires(Permission.EXPORT)]
Sharing = Annotated[Tenant, requires(Permission.SHARE)]
FormatParam = Annotated[Literal["pdf", "xlsx"], Query(alias="format")]
LIST_LIMIT = 100

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    404: {"description": "Not found (or not yours, or in another workspace)"},
    409: {"description": "A recipient cannot see the data this item uses"},
    422: {"description": "Validation error"},
}


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


def _lead(item: Message | Report) -> str:
    if isinstance(item, Message):
        return str((item.answer_json or {}).get("lead") or item.content)
    body = dict(item.body)
    if body.get("kind") == "briefing":
        return str(body.get("headline") or "")
    return str((body.get("answer") or {}).get("lead") or "")


def _report_out(report: Report, user_id: uuid.UUID) -> ReportOut:
    return ReportOut(
        id=report.id,
        title=report.title,
        source=report.source,
        body=dict(report.body),
        provenance=dict(report.provenance),
        quality_warnings=list(report.quality_warnings),
        created_at=report.created_at,
        mine=report.user_id == user_id,
    )


def _file_response(data: bytes, title: str, fmt: reports.Format) -> Response:
    return Response(
        content=data,
        media_type=reports.CONTENT_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{reports.filename(title, fmt)}"'},
    )


def _audit_export(
    tenant: Tenant, kind: str, object_id: uuid.UUID, fmt: str, shared: bool = False
) -> None:
    audit.write(
        tenant.db,
        action="export.created",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type=kind,
        target_id=object_id,
        details={"format": fmt, "shared": shared},
    )


# -------------------------------------------------------------------------------------------- reports
@router.get(
    "/reports", response_model=ReportList, responses=_ERRORS, summary="My reports, newest first"
)
def list_reports(
    tenant: Viewing, source: Annotated[Literal["task", "briefing"] | None, Query()] = None
) -> ReportList:
    query = select(Report).where(
        Report.org_id == tenant.org_id, Report.user_id == tenant.auth.user.id
    )
    if source is not None:
        query = query.where(Report.source == source)
    found = tenant.db.scalars(query.order_by(Report.created_at.desc()).limit(LIST_LIMIT))
    return ReportList(
        reports=[
            ReportSummary(
                id=r.id, title=r.title, source=r.source, lead=_lead(r), created_at=r.created_at
            )
            for r in found
        ]
    )


@router.get(
    "/reports/{report_id}", response_model=ReportOut, responses=_ERRORS, summary="One of my reports"
)
def get_report(report_id: uuid.UUID, tenant: Viewing) -> ReportOut:
    report = reports.owned_report(tenant.db, tenant.org_id, tenant.auth.user.id, report_id)
    if report is None:
        raise _not_found()
    return _report_out(report, tenant.auth.user.id)


@router.get(
    "/reports/{report_id}/export",
    response_class=Response,
    responses=_ERRORS,
    summary="One of my reports as PDF or Excel",
)
def export_report(
    report_id: uuid.UUID, fmt: FormatParam, request: Request, tenant: Exporting
) -> Response:
    report = reports.owned_report(tenant.db, tenant.org_id, tenant.auth.user.id, report_id)
    if report is None:
        raise _not_found()
    data = reports.report_file(tenant.db, request.app.state.connector_runtime.objects, report, fmt)
    _audit_export(tenant, "report", report.id, fmt)
    tenant.db.commit()
    return _file_response(data, report.title, fmt)


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/export",
    response_class=Response,
    responses=_ERRORS,
    summary="One of my answers as PDF or Excel",
)
def export_message(
    conversation_id: uuid.UUID, message_id: uuid.UUID, fmt: FormatParam, tenant: Exporting
) -> Response:
    message = reports.owned_message(tenant.db, tenant.org_id, tenant.auth.user.id, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise _not_found()
    doc = reports.document_for_message(tenant.db, message)
    data = reports.render(doc, fmt)
    _audit_export(tenant, "message", message.id, fmt)
    tenant.db.commit()
    return _file_response(data, doc.title, fmt)


# --------------------------------------------------------------------------------------------- shares
def _owned(tenant: Tenant, kind: str, object_id: uuid.UUID) -> tuple[Message | Report, str]:
    """An item of mine and its title."""
    if kind == "message":
        message = reports.owned_message(tenant.db, tenant.org_id, tenant.auth.user.id, object_id)
        if message is None:
            raise _not_found()
        return message, reports.question_of(tenant.db, message) or message.content
    report = reports.owned_report(tenant.db, tenant.org_id, tenant.auth.user.id, object_id)
    if report is None:
        raise _not_found()
    return report, report.title


def _share_list(tenant: Tenant, kind: str, item: Message | Report) -> ShareList:
    shares = reports.shares_of(tenant.db, tenant.org_id, kind, item.id)
    users = {
        u.id: u
        for u in tenant.db.scalars(
            select(User).where(User.id.in_([s.shared_with_user_id for s in shares]))
        )
    }
    shared_ids = {s.shared_with_user_id for s in shares}
    return ShareList(
        shares=[
            ShareOut(
                id=s.id,
                user_id=s.shared_with_user_id,
                name=users[s.shared_with_user_id].name if s.shared_with_user_id in users else None,
                email=users[s.shared_with_user_id].email if s.shared_with_user_id in users else "",
                created_at=s.created_at,
            )
            for s in shares
        ],
        candidates=[
            Candidate(
                user_id=c.user.id,
                name=c.user.name,
                email=c.user.email,
                role=c.role,
                can_see=c.can_see,
                shared=c.user.id in shared_ids,
            )
            for c in reports.candidates(
                tenant.db, tenant.org_id, tenant.auth.user.id, dict(item.provenance)
            )
        ],
    )


@router.get(
    "/shares",
    response_model=ShareList,
    responses=_ERRORS,
    summary="Who one of my answers or reports is shared with, and who it can be shared with",
)
def list_shares(
    tenant: Sharing,
    object_type: Annotated[Literal["message", "report"], Query()],
    object_id: Annotated[uuid.UUID, Query()],
) -> ShareList:
    item, _ = _owned(tenant, object_type, object_id)
    return _share_list(tenant, object_type, item)


@router.post(
    "/shares",
    response_model=ShareList,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Share one of my answers or reports with members of the workspace",
)
def create_shares(
    body: ShareRequest, request: Request, background: BackgroundTasks, tenant: Sharing
) -> ShareList:
    item, title = _owned(tenant, body.object_type, body.object_id)
    allowed = {
        c.user.id: c
        for c in reports.candidates(
            tenant.db, tenant.org_id, tenant.auth.user.id, dict(item.provenance)
        )
    }
    wanted = list(dict.fromkeys(body.user_ids))
    unknown = [u for u in wanted if u not in allowed]
    if unknown:
        raise _not_found()
    blocked = [allowed[u] for u in wanted if not allowed[u].can_see]
    if blocked:
        names = ", ".join(c.user.name or c.user.email for c in blocked)
        raise ApiError(
            409,
            "cannot_see_sources",
            f"{names} cannot see all the data this uses, so it cannot be shared with them. An Owner can change "
            "their access under Connections.",
        )
    existing = {
        s.shared_with_user_id
        for s in reports.shares_of(tenant.db, tenant.org_id, body.object_type, item.id)
    }
    sharer = tenant.auth.user.name or tenant.auth.user.email
    what = "an answer" if body.object_type == "message" else "a report"
    base = tenant.service.settings.app_base_url
    for user_id in wanted:
        if user_id in existing:
            continue
        share = Share(
            org_id=tenant.org_id,
            object_type=body.object_type,
            object_id=item.id,
            shared_with_user_id=user_id,
            shared_by=tenant.auth.user.id,
        )
        tenant.db.add(share)
        tenant.db.flush()
        audit.write(
            tenant.db,
            action="share.created",
            at=tenant.service.clock(),
            meta=tenant.service.meta,
            org_id=tenant.org_id,
            user_id=tenant.auth.user.id,
            target_type=body.object_type,
            target_id=item.id,
            details={"shared_with": str(user_id)},
        )
        tenant.service.outbox.append(
            templates.render(
                "shared",
                to=allowed[user_id].user.email,
                subject=f"{sharer} shared {what} with you",
                sharer=sharer,
                what=what,
                title=title[:200],
                org_name=tenant.auth.organization.name,
                link=f"{base}/shared/{share.id}",
            )
        )
    tenant.db.commit()
    send_queued(request, background, tenant.service)
    return _share_list(tenant, body.object_type, item)


@router.delete(
    "/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Stop sharing (the person who shared it, or the item's owner)",
)
def delete_share(share_id: uuid.UUID, tenant: Sharing) -> Response:
    share = tenant.db.scalar(
        select(Share).where(Share.id == share_id, Share.org_id == tenant.org_id)
    )
    if share is None:
        raise _not_found()
    me = tenant.auth.user.id
    owner = None
    if share.object_type == "message":
        message = tenant.db.get(Message, share.object_id)
        owner = reports.message_owner(tenant.db, message) if message else None
    else:
        report = tenant.db.get(Report, share.object_id)
        owner = report.user_id if report else None
    if me not in (share.shared_by, owner):
        raise _not_found()
    audit.write(
        tenant.db,
        action="share.removed",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=me,
        target_type=share.object_type,
        target_id=share.object_id,
        details={"shared_with": str(share.shared_with_user_id)},
    )
    tenant.db.delete(share)
    tenant.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------------------- shared
def _sharer(tenant: Tenant, share: Share) -> str | None:
    if share.shared_by is None:
        return None
    user = tenant.db.get(User, share.shared_by)
    return (user.name or user.email) if user else None


@router.get(
    "/shared",
    response_model=SharedList,
    responses=_ERRORS,
    summary="Answers and reports shared with me",
)
def list_shared(tenant: Viewing) -> SharedList:
    shares = list(
        tenant.db.scalars(
            select(Share)
            .where(Share.org_id == tenant.org_id, Share.shared_with_user_id == tenant.auth.user.id)
            .order_by(Share.created_at.desc())
            .limit(LIST_LIMIT)
        )
    )
    items = []
    for share in shares:
        found = reports.shared_item(tenant.db, tenant.org_id, tenant.auth.user.id, share.id)
        if found is None:
            continue
        if found.message is not None:
            title = reports.question_of(tenant.db, found.message) or found.message.content
            lead = _lead(found.message)
        else:
            assert found.report is not None
            title, lead = found.report.title, _lead(found.report)
        items.append(
            SharedSummary(
                id=share.id,
                object_type=share.object_type,
                title=title[:200],
                lead=lead,
                shared_by=_sharer(tenant, share),
                created_at=share.created_at,
            )
        )
    return SharedList(items=items)


def _shared(tenant: Tenant, share_id: uuid.UUID) -> reports.SharedItem:
    found = reports.shared_item(tenant.db, tenant.org_id, tenant.auth.user.id, share_id)
    if found is None:
        raise _not_found()
    return found


@router.get(
    "/shared/{share_id}",
    response_model=SharedOut,
    responses=_ERRORS,
    summary="One item shared with me",
)
def get_shared(share_id: uuid.UUID, tenant: Viewing) -> SharedOut:
    found = _shared(tenant, share_id)
    if found.message is not None:
        message = found.message
        return SharedOut(
            id=found.share.id,
            object_type="message",
            title=(reports.question_of(tenant.db, message) or message.content)[:200],
            question=reports.question_of(tenant.db, message),
            answer=dict(message.answer_json),
            body=None,
            provenance=dict(message.provenance),
            quality_warnings=list(message.quality_warnings),
            shared_by=_sharer(tenant, found.share),
            created_at=found.share.created_at,
        )
    report = found.report
    assert report is not None
    body = dict(report.body)
    briefing = body.get("kind") == "briefing"
    return SharedOut(
        id=found.share.id,
        object_type="report",
        title=report.title,
        question=None if briefing else body.get("question"),
        answer=None if briefing else dict(body.get("answer") or {}),
        body=body if briefing else None,
        provenance=dict(report.provenance),
        quality_warnings=list(report.quality_warnings),
        shared_by=_sharer(tenant, found.share),
        created_at=found.share.created_at,
    )


@router.get(
    "/shared/{share_id}/export",
    response_class=Response,
    responses=_ERRORS,
    summary="An item shared with me as PDF or Excel (Viewers may export these)",
)
def export_shared(
    share_id: uuid.UUID, fmt: FormatParam, request: Request, tenant: Viewing
) -> Response:
    found = _shared(tenant, share_id)
    require(tenant.auth, Permission.EXPORT, resource=found)
    if found.message is not None:
        doc = reports.document_for_message(tenant.db, found.message)
        data, title = reports.render(doc, fmt), doc.title
    else:
        assert found.report is not None
        data = reports.report_file(
            tenant.db, request.app.state.connector_runtime.objects, found.report, fmt
        )
        title = found.report.title
    _audit_export(tenant, found.share.object_type, found.share.object_id, fmt, shared=True)
    tenant.db.commit()
    return _file_response(data, title, fmt)
