"""The Ask screen's API (BUILD_SPEC 7.1, X2; M8): conversations, questions, answers and saved questions.

Asking stores the question and an empty answer (``queued``) and hands the work to the background queue
(``answer_question``); the web app polls the answer, which lists its progress steps until it is ``done`` or
``failed``. Before anything is queued: the per-person rate limit (``QUESTIONS_PER_USER_PER_MINUTE``), the
Managed plan's monthly limit and whether AI can be used at all (BUILD_SPEC 7.8).

An identical first question by the same person within ``CACHE_TTL_SECONDS`` reuses the answer just given (no AI
call, nothing counted; the data is within its reuse window anyway).

History is per person: someone else's conversation answers 404, like one in another workspace (P3).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.agent import service as agent_service
from app.api.ai import limit_api_error, unavailable_api_error
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.connectors.registry import ConnectorRuntime
from app.dictionary import service as dictionary_service
from app.llm import keys
from app.llm.usage import UsageLimitReached
from app.models import Conversation, Message, SavedQuestion
from app.schemas.chat import (
    AskRequest,
    ConversationDetailOut,
    ConversationList,
    ConversationOut,
    ConversationPatch,
    FollowUpRequest,
    MessageOut,
    SavedQuestionList,
    SavedQuestionOut,
    SavedQuestionRequest,
)
from app.tasks.queue import JobQueue

router = APIRouter(tags=["ask"])

Ask = Annotated[Tenant, requires(Permission.ASK)]
Saved = Annotated[Tenant, requires(Permission.SAVED_QUESTIONS)]

TITLE_LENGTH = 80
LIST_LIMIT = 100
SAVED_LIMIT = 200

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    404: {"description": "Not found (or not yours, or in another workspace)"},
    409: {"description": "An answer is still being made, or AI is not set up for this workspace"},
    422: {"description": "Validation error"},
    429: {"description": "Too many questions, or the monthly limit is reached"},
}


# ---------------------------------------------------------------------------------------------- helpers
def _runtime(request: Request) -> ConnectorRuntime:
    runtime: ConnectorRuntime = request.app.state.connector_runtime
    return runtime


def _jobs(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


def _conversation(
    tenant: Tenant, conversation_id: uuid.UUID, *, lock: bool = False
) -> Conversation:
    query = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.org_id == tenant.org_id,
        Conversation.user_id == tenant.auth.user.id,
    )
    found = tenant.db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise _not_found()
    return found


def _messages(tenant: Tenant, conversation: Conversation) -> list[Message]:
    return list(
        tenant.db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id, Message.org_id == tenant.org_id)
            .order_by(Message.created_at, Message.id)
        )
    )


def _message_out(message: Message) -> MessageOut:
    answer = message.answer_json if message.role == "assistant" and message.answer_json else None
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        status=message.status,
        lens=message.lens,
        created_at=message.created_at,
        finished_at=message.finished_at,
        answer=answer,
        progress=list(message.progress_json),
        tool_trace=list(message.tool_trace),
        provenance=dict(message.provenance),
        quality_warnings=list(message.quality_warnings),
        error_code=message.error_code,
        reused=message.reused_from is not None,
    )


def _conversation_out(conversation: Conversation) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        lens=conversation.lens,
        created_at=conversation.created_at,
        last_message_at=conversation.last_message_at,
    )


def _detail(tenant: Tenant, conversation: Conversation) -> ConversationDetailOut:
    messages = _messages(tenant, conversation)
    now = tenant.service.clock()
    stale = [
        agent_service.mark_stale(tenant.db, m, now) for m in messages
    ]  # every one, not just the first
    if any(stale):
        tenant.db.commit()
    return ConversationDetailOut(
        **_conversation_out(conversation).model_dump(),
        messages=[_message_out(m) for m in messages],
    )


def _check_can_ask(request: Request, tenant: Tenant) -> None:
    """Rate limit, monthly limit and AI availability, before anything is stored (BUILD_SPEC 6.5, 7.8)."""
    settings = _runtime(request).settings
    now = tenant.service.clock()
    limiter = FixedWindowLimiter(
        request.app.state.redis,
        prefix="questions",
        limit=settings.questions_per_user_per_minute,
        window=timedelta(minutes=1),
    )
    if not limiter.hit(str(tenant.auth.user.id), now):
        raise ApiError(
            429,
            "too_many_questions",
            "You have asked many questions in the last minute. Wait a moment and ask again.",
        )
    try:
        keys.check_available(tenant.db, tenant.auth.organization, settings=settings, now=now)
    except UsageLimitReached as error:
        raise limit_api_error(error) from error
    except keys.AiUnavailable as error:
        raise unavailable_api_error(error) from error


def _reusable(request: Request, tenant: Tenant, question: str, lens: str) -> Message | None:
    active = dictionary_service.active(tenant.db, tenant.org_id)
    key = agent_service.reuse_key(
        tenant.org_id, tenant.auth.user.id, lens, question, active.version
    )
    raw = request.app.state.redis.get(key)
    if not raw:
        return None
    try:
        message_id = uuid.UUID(raw.decode() if isinstance(raw, bytes) else str(raw))
    except ValueError:
        return None
    found = tenant.db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == message_id,
            Message.org_id == tenant.org_id,
            Message.status == "done",
            Conversation.user_id == tenant.auth.user.id,
        )
    )
    return found


def _ask(
    request: Request,
    tenant: Tenant,
    conversation: Conversation,
    question: str,
    *,
    first: bool,
) -> None:
    now = tenant.service.clock()
    latest = tenant.db.scalar(
        select(Message.created_at)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if latest is not None and latest >= now:
        # Keep the conversation's order even when two questions share a clock tick.
        now = latest + timedelta(microseconds=1)
    tenant.db.add(
        Message(
            org_id=tenant.org_id,
            conversation_id=conversation.id,
            user_id=tenant.auth.user.id,
            role="user",
            content=question,
            status="done",
            lens=conversation.lens,
            created_at=now,
            updated_at=now,
        )
    )
    reused = _reusable(request, tenant, question, conversation.lens) if first else None
    answer = Message(
        org_id=tenant.org_id,
        conversation_id=conversation.id,
        user_id=tenant.auth.user.id,
        role="assistant",
        content="",
        status="queued",
        lens=conversation.lens,
        progress_json=[],
        created_at=now + timedelta(microseconds=1),
        updated_at=now,
    )
    if reused is not None:
        answer.status = "done"
        answer.content = reused.content
        answer.answer_json = reused.answer_json
        answer.tool_trace = reused.tool_trace
        answer.provenance = reused.provenance
        answer.quality_warnings = reused.quality_warnings
        answer.dictionary_version = reused.dictionary_version
        answer.reused_from = reused.id
        answer.started_at = answer.finished_at = now
        answer.token_usage = {"calls": 0}
    tenant.db.add(answer)
    conversation.last_message_at = now
    tenant.db.flush()
    if reused is not None:
        audit.write(
            tenant.db,
            action="question.asked",
            at=now,
            meta=tenant.service.meta,
            org_id=tenant.org_id,
            user_id=tenant.auth.user.id,
            target_type="message",
            target_id=answer.id,
            details={
                "question_sha256": agent_service.question_hash(question),
                "lens": conversation.lens,
                "datasets": sorted(
                    {
                        t.get("table")
                        for p in reused.provenance.values()
                        for t in p.get("tables", [])
                    }
                    - {None}
                ),
                "status": "done",
                "reused": True,
            },
        )
    tenant.db.commit()
    if reused is None:
        meta = tenant.service.meta
        _jobs(request).answer_question(
            agent_service.Asked(tenant.org_id, answer.id, meta.ip, meta.user_agent)
        )
        # An inline queue (tests, scripts) has already written the answer in another session.
        tenant.db.expire_all()


# ------------------------------------------------------------------------------------- conversations
@router.get("/conversations", response_model=ConversationList, responses=_ERRORS)
def list_conversations(tenant: Ask) -> ConversationList:
    found = tenant.db.scalars(
        select(Conversation)
        .where(Conversation.org_id == tenant.org_id, Conversation.user_id == tenant.auth.user.id)
        .order_by(Conversation.last_message_at.desc())
        .limit(LIST_LIMIT)
    )
    return ConversationList(conversations=[_conversation_out(c) for c in found])


@router.post(
    "/conversations",
    response_model=ConversationDetailOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Ask a question in a new conversation; the answer is made in the background",
)
def start_conversation(body: AskRequest, request: Request, tenant: Ask) -> ConversationDetailOut:
    _check_can_ask(request, tenant)
    now = tenant.service.clock()
    title = (
        body.question
        if len(body.question) <= TITLE_LENGTH
        else body.question[: TITLE_LENGTH - 1] + "…"
    )
    conversation = Conversation(
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        title=title,
        lens=body.lens,
        last_message_at=now,
    )
    tenant.db.add(conversation)
    tenant.db.flush()
    _ask(request, tenant, conversation, body.question, first=True)
    return _detail(tenant, conversation)


@router.get(
    "/conversations/{conversation_id}", response_model=ConversationDetailOut, responses=_ERRORS
)
def get_conversation(conversation_id: uuid.UUID, tenant: Ask) -> ConversationDetailOut:
    return _detail(tenant, _conversation(tenant, conversation_id))


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationDetailOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Ask a follow-up question (the last turns are its context)",
)
def follow_up(
    conversation_id: uuid.UUID, body: FollowUpRequest, request: Request, tenant: Ask
) -> ConversationDetailOut:
    conversation = _conversation(tenant, conversation_id, lock=True)
    now = tenant.service.clock()
    for message in _messages(tenant, conversation):
        agent_service.mark_stale(tenant.db, message, now)
        if message.status in ("queued", "running"):
            raise ApiError(
                409, "answer_in_progress", "Wait for the answer to finish before asking again."
            )
    _check_can_ask(request, tenant)
    _ask(request, tenant, conversation, body.question, first=False)
    return _detail(tenant, conversation)


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}",
    response_model=MessageOut,
    responses=_ERRORS,
    summary="One message (the web app polls an answer until it is done)",
)
def get_message(conversation_id: uuid.UUID, message_id: uuid.UUID, tenant: Ask) -> MessageOut:
    conversation = _conversation(tenant, conversation_id)
    message = tenant.db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation.id,
            Message.org_id == tenant.org_id,
        )
    )
    if message is None:
        raise _not_found()
    if agent_service.mark_stale(tenant.db, message, tenant.service.clock()):
        tenant.db.commit()
    return _message_out(message)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut, responses=_ERRORS)
def update_conversation(
    conversation_id: uuid.UUID, body: ConversationPatch, tenant: Ask
) -> ConversationOut:
    conversation = _conversation(tenant, conversation_id, lock=True)
    if body.title is not None:
        conversation.title = " ".join(body.title.split())[:200] or conversation.title
    if body.lens is not None:
        conversation.lens = body.lens
    tenant.db.commit()
    return _conversation_out(conversation)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
)
def delete_conversation(conversation_id: uuid.UUID, tenant: Ask) -> Response:
    conversation = _conversation(tenant, conversation_id, lock=True)
    tenant.db.delete(conversation)
    tenant.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------------------------ saved questions
def _saved_out(item: SavedQuestion) -> SavedQuestionOut:
    return SavedQuestionOut(
        id=item.id,
        text=item.text,
        lens=item.lens,
        created_at=item.created_at,
    )


@router.get("/saved-questions", response_model=SavedQuestionList, responses=_ERRORS)
def list_saved(tenant: Saved) -> SavedQuestionList:
    found = tenant.db.scalars(
        select(SavedQuestion)
        .where(SavedQuestion.org_id == tenant.org_id, SavedQuestion.user_id == tenant.auth.user.id)
        .order_by(SavedQuestion.created_at.desc())
        .limit(SAVED_LIMIT)
    )
    return SavedQuestionList(questions=[_saved_out(q) for q in found])


@router.post(
    "/saved-questions",
    response_model=SavedQuestionOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
def save_question(
    body: SavedQuestionRequest, tenant: Saved, response: Response
) -> SavedQuestionOut:
    existing = tenant.db.scalar(
        select(SavedQuestion).where(
            SavedQuestion.org_id == tenant.org_id,
            SavedQuestion.user_id == tenant.auth.user.id,
            SavedQuestion.text == body.text,
            SavedQuestion.lens == body.lens,
        )
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _saved_out(existing)
    count = len(
        list(
            tenant.db.scalars(
                select(SavedQuestion.id).where(
                    SavedQuestion.org_id == tenant.org_id,
                    SavedQuestion.user_id == tenant.auth.user.id,
                )
            )
        )
    )
    if count >= SAVED_LIMIT:
        raise ApiError(409, "too_many_saved", f"You can keep up to {SAVED_LIMIT} saved questions.")
    item = SavedQuestion(
        org_id=tenant.org_id, user_id=tenant.auth.user.id, text=body.text, lens=body.lens
    )
    tenant.db.add(item)
    tenant.db.commit()
    return _saved_out(item)


@router.delete(
    "/saved-questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS
)
def delete_saved(question_id: uuid.UUID, tenant: Saved) -> Response:
    item = tenant.db.scalar(
        select(SavedQuestion).where(
            SavedQuestion.id == question_id,
            SavedQuestion.org_id == tenant.org_id,
            SavedQuestion.user_id == tenant.auth.user.id,
        )
    )
    if item is None:
        raise _not_found()
    tenant.db.delete(item)
    tenant.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
