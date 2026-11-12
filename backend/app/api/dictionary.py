"""``/dictionary``: the business dictionary (BUILD_SPEC 9, R2, R13; M7).

* Everyone who may use connections (Owners, Analysts) reads it. Tables of connections a person may not see are
  left out for them, like the connections themselves (DECISIONS D50).
* Owners (``dictionary.edit``) review the draft, edit it, confirm it, ask for a new draft and replace a connection.
* Drafting runs in the background (Celery); the response says ``drafting`` and the web app polls.
* Until an Owner confirms, answers carry the note "dictionary not confirmed" (``service.NOT_CONFIRMED_NOTE``).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.connectors import access
from app.connectors.registry import ConnectorRuntime
from app.connectors.service import remove_connection
from app.db.tenant import scope_to_org
from app.dictionary import edits, model, service
from app.dictionary.model import Dictionary
from app.models import Connection, DictionaryVersion, User
from app.schemas.dictionary import (
    DictionaryOut,
    DraftQueuedOut,
    DraftRequest,
    ReplaceOut,
    ReplaceRequest,
    VersionContentOut,
    VersionOut,
)
from app.tasks.queue import JobQueue

router = APIRouter(prefix="/dictionary", tags=["dictionary"])

ViewDictionary = Annotated[Tenant, requires(Permission.CONNECTIONS_VIEW)]
EditDictionary = Annotated[Tenant, requires(Permission.DICTIONARY_EDIT)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    404: {"description": "Not found (or not visible to you, or in another workspace)"},
    409: {"description": "Not possible in the dictionary's current state"},
    422: {"description": "Validation error"},
}

_MESSAGES = {
    "no_draft": "There is no draft to work on. Ask for a new draft first.",
    "already_drafting": "A draft is being made already. It will appear here in a moment.",
    "review_pending": "Some items still need your check. Look at the ones marked, then confirm.",
    "not_read": "The new connection has not been read yet. Wait until it shows its tables, then try again.",
    "no_dictionary": "There is no dictionary to move yet. Wait for the first draft.",
    "same_connection": "Choose two different connections.",
    "divide_needs_rupees": "Dividing by 100 only makes sense for amounts in rupees (INR).",
    "unknown_table": "A metric points at a table that does not exist.",
    "unknown_join": "Joins can be removed here, not added: they are found by testing the data.",
}


def _runtime(request: Request) -> ConnectorRuntime:
    runtime: ConnectorRuntime = request.app.state.connector_runtime
    return runtime


def _jobs(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


def _error(status_code: int, code: str) -> ApiError:
    return ApiError(status_code, code, _MESSAGES[code])


def _audit(
    tenant: Tenant, action: str, version: DictionaryVersion | None, details: dict[str, Any]
) -> None:
    audit.write(
        tenant.db,
        action=action,
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="dictionary" if version is not None else None,
        target_id=version.id if version is not None else None,
        details=details,
    )


# -------------------------------------------------------------------------------------------- shaping
def _visible(tenant: Tenant) -> set[str] | None:
    """Connection ids the caller may see; ``None`` for everyone (Owners)."""
    if access.sees_everything(tenant.auth):
        return None
    return {str(i) for i in access.visible_connection_ids(tenant.db, tenant.auth)}


def _restrict(content: Dictionary, visible: set[str] | None) -> Dictionary:
    if visible is None:
        return content
    shown = {
        k: e
        for k, e in content.datasets.items()
        if e.connection_id is None or e.connection_id in visible
    }
    metrics = {k: m for k, m in content.metrics.items() if m.dataset is None or m.dataset in shown}
    relationships = [
        r
        for r in content.relationships
        if r.left.partition(".")[0] in shown and r.right.partition(".")[0] in shown
    ]
    return content.model_copy(
        update={"datasets": shown, "metrics": metrics, "relationships": relationships}
    )


def _version_out(tenant: Tenant, row: DictionaryVersion) -> VersionOut:
    name = None
    if row.confirmed_by is not None:
        name = tenant.db.scalar(select(User.name).where(User.id == row.confirmed_by))
    return VersionOut(
        id=row.id,
        version=row.version,
        status=row.status,
        origin=row.origin,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
        confirmed_by_name=name,
        review_items=model.parse(row.content).review_items() if row.status == "draft" else 0,
        diff=row.diff_json,
        notes=row.notes_json,
    )


def _content_out(tenant: Tenant, row: DictionaryVersion) -> VersionContentOut:
    base = _version_out(tenant, row)
    content = _restrict(model.parse(row.content), _visible(tenant))
    return VersionContentOut(**base.model_dump(), content=content)


def _state(request: Request, tenant: Tenant) -> DictionaryOut:
    org_id = tenant.org_id
    owner = tenant.auth.role == "owner"
    confirmed = service.latest_confirmed(tenant.db, org_id)
    draft = service.current_draft(tenant.db, org_id)
    shown = confirmed if confirmed is not None else (None if owner else draft)
    versions = list(
        tenant.db.scalars(
            select(DictionaryVersion)
            .where(DictionaryVersion.org_id == org_id, DictionaryVersion.status != "draft")
            .order_by(DictionaryVersion.version.desc())
            .limit(30)
        )
    )
    return DictionaryOut(
        confirmed=confirmed is not None,
        drafting=service.drafting(_runtime(request), org_id),
        active=_content_out(tenant, shown) if shown is not None else None,
        draft=_content_out(tenant, draft) if owner and draft is not None else None,
        versions=[_version_out(tenant, v) for v in versions] if owner else [],
    )


def _draft(tenant: Tenant, *, lock: bool = False) -> DictionaryVersion:
    query = select(DictionaryVersion).where(
        DictionaryVersion.org_id == tenant.org_id, DictionaryVersion.status == "draft"
    )
    draft = tenant.db.scalar(query.with_for_update() if lock else query)
    if draft is None:
        raise _error(409, "no_draft")
    return draft


# ------------------------------------------------------------------------------------------- routes
@router.get(
    "",
    response_model=DictionaryOut,
    responses=_ERRORS,
    summary="The business dictionary: the version answers use, the Owner's draft and the history",
)
def get_dictionary(request: Request, tenant: ViewDictionary) -> DictionaryOut:
    return _state(request, tenant)


@router.put(
    "/draft",
    response_model=VersionContentOut,
    responses=_ERRORS,
    summary="Save the Owner's edits to the draft (meanings, roles, units, metrics, kept joins)",
)
def save_draft(body: Dictionary, request: Request, tenant: EditDictionary) -> VersionContentOut:
    draft = _draft(tenant, lock=True)
    try:
        edited, summary = edits.apply(model.parse(draft.content), body)
    except edits.EditError as error:
        raise _error(422, error.code) from error
    service.revalidate(_runtime(request), tenant.org_id, edited)
    scope_to_org(tenant.db, tenant.org_id)
    draft.content = model.dump(edited)
    _audit(
        tenant,
        "dictionary.edited",
        draft,
        {
            "version": draft.version,
            "columns": summary.columns,
            "metrics": summary.metrics,
            "joins_removed": summary.joins_removed,
        },
    )
    tenant.db.commit()
    return _content_out(tenant, draft)


@router.post(
    "/draft",
    response_model=DraftQueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Ask for a new draft (profile the data again; the AI describes only what is new)",
)
def request_draft(body: DraftRequest, request: Request, tenant: EditDictionary) -> DraftQueuedOut:
    if not service.mark_queued(_runtime(request), tenant.org_id):
        raise _error(409, "already_drafting")
    _audit(tenant, "dictionary.draft_requested", None, {"fresh_ai": body.fresh_ai})
    tenant.db.commit()
    _jobs(request).draft_dictionary(
        org_id=tenant.org_id, user_id=tenant.auth.user.id, force=True, fresh_ai=body.fresh_ai
    )
    return DraftQueuedOut()


@router.delete(
    "/draft",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Throw the draft away (the confirmed dictionary stays as it is)",
)
def discard_draft(tenant: EditDictionary) -> Response:
    draft = _draft(tenant, lock=True)
    _audit(tenant, "dictionary.draft_discarded", draft, {"version": draft.version})
    tenant.db.delete(draft)
    tenant.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/draft/confirm",
    response_model=VersionContentOut,
    responses=_ERRORS,
    summary="Confirm the draft: it becomes the dictionary answers use",
)
def confirm_draft(request: Request, tenant: EditDictionary) -> VersionContentOut:
    _draft(tenant, lock=True)
    try:
        confirmed = service.confirm(
            tenant.db, tenant.org_id, user_id=tenant.auth.user.id, at=tenant.service.clock()
        )
    except service.ReviewPending as error:
        raise _error(409, "review_pending") from error
    assert confirmed is not None  # a draft exists (checked above)
    _audit(tenant, "dictionary.confirmed", confirmed, {"version": confirmed.version})
    tenant.db.commit()
    return _content_out(tenant, confirmed)


@router.get(
    "/versions/{version_id}",
    response_model=VersionContentOut,
    responses=_ERRORS,
    summary="One earlier or current version of the dictionary",
)
def get_version(version_id: uuid.UUID, tenant: ViewDictionary) -> VersionContentOut:
    return _content_out(tenant, _version(tenant, version_id))


def _version(tenant: Tenant, version_id: uuid.UUID) -> DictionaryVersion:
    row = tenant.db.scalar(
        select(DictionaryVersion).where(
            DictionaryVersion.id == version_id, DictionaryVersion.org_id == tenant.org_id
        )
    )
    # Drafts are the Owner's working copy: others see confirmed versions only.
    if row is None or (row.status == "draft" and tenant.auth.role != "owner"):
        raise access.not_found()
    return row


@router.get(
    "/export.yaml",
    response_class=PlainTextResponse,
    responses=_ERRORS,
    summary="The dictionary as YAML (the confirmed version, or one chosen by id)",
)
def export_yaml(
    tenant: ViewDictionary, version_id: Annotated[uuid.UUID | None, Query()] = None
) -> PlainTextResponse:
    row: DictionaryVersion | None
    if version_id is not None:
        row = _version(tenant, version_id)
    else:
        row = service.latest_confirmed(tenant.db, tenant.org_id) or (
            service.current_draft(tenant.db, tenant.org_id) if tenant.auth.role == "owner" else None
        )
        if row is None:
            raise access.not_found()
    content = _restrict(model.parse(row.content), _visible(tenant))
    return PlainTextResponse(
        model.to_yaml(content),
        media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="dictionary-v{row.version}.yaml"'},
    )


@router.post(
    "/replace",
    response_model=ReplaceOut,
    responses=_ERRORS,
    summary="Move the dictionary's mappings from one connection to another kind of source (R2)",
)
def replace_connection(
    body: ReplaceRequest, request: Request, tenant: EditDictionary
) -> ReplaceOut:
    if body.from_connection_id == body.to_connection_id:
        raise _error(422, "same_connection")
    old = access.visible_connection(tenant.db, tenant.auth, body.from_connection_id, lock=True)
    new = access.visible_connection(tenant.db, tenant.auth, body.to_connection_id)
    runtime = _runtime(request)
    tenant.db.commit()  # nothing stays open while the stored data is read
    scope_to_org(tenant.db, tenant.org_id)
    try:
        outcome = service.replace_connection(
            runtime,
            org_id=tenant.org_id,
            user_id=tenant.auth.user.id,
            from_connection_id=old.id,
            to_connection_id=new.id,
        )
    except service.ReplaceError as error:
        raise _error(409, error.code) from error
    scope_to_org(tenant.db, tenant.org_id)
    old = access.visible_connection(tenant.db, tenant.auth, body.from_connection_id, lock=True)
    _audit(
        tenant,
        "dictionary.replaced",
        None,
        {
            "from": old.name,
            "to": new.name,
            "tables_matched": len(outcome.matched),
            "tables_unmatched": len(outcome.unmatched),
            "removed_old": body.remove_old,
        },
    )
    if body.remove_old:
        _audit_removed(tenant, old)
        remove_connection(tenant.db, runtime, old)
    tenant.db.commit()
    draft = _draft(tenant)
    return ReplaceOut(
        matched=outcome.matched,
        unmatched=outcome.unmatched,
        removed_old=body.remove_old,
        draft=_content_out(tenant, draft),
    )


def _audit_removed(tenant: Tenant, connection: Connection) -> None:
    audit.write(
        tenant.db,
        action="connection.removed",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="connection",
        target_id=connection.id,
        details={"type": connection.type, "name": connection.name, "replaced": True},
    )
