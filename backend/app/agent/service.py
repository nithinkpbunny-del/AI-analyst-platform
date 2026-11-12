"""Answering one question: the body of the background job ``app.tasks.jobs.answer_question`` (BUILD_SPEC 7.1).

1. The API stored the question and an empty answer (``queued``) and checked the monthly limit and rate limits.
2. Here: the asking person's ``AccessContext`` and the dictionary they may see (confirmed version, or the draft
   with the "dictionary not confirmed" note); the AI key (``keys.resolve``, Managed or BYOK).
3. The agent loop, with progress steps written to the answer as they happen (the Ask screen polls them).
4. The answer's rules (number check already done in the loop; ₹ impact; data notes from code), then the answer,
   tool trace, provenance, quality warnings and token usage are stored; ``question.asked`` goes to the activity
   log with a hash of the text and the datasets used (never the answer).

Database sessions are short: nothing is held open while the model or a source works (M5 notes). Every model call
writes one ``usage_events`` row (kind ``question``, one ``run_id`` per question).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.agent import answer as answers
from app.agent import loop, prompts
from app.agent.access import NoAccess, VisibleDictionary, access_context, visible_dictionary
from app.agent.provenance import ResultStore
from app.agent.tools import ToolContext
from app.audit import writer as audit
from app.connectors.registry import ConnectorRuntime
from app.data.cache import DatasetCache
from app.data.workspace import Workspace
from app.db.tenant import scope_to_org
from app.dictionary import service as dictionary_service
from app.llm import keys, usage
from app.llm.base import LLMFactory, LLMResponse, close_client
from app.llm.usage import UsageLimitReached
from app.models import Conversation, Message, Organization

logger = logging.getLogger(__name__)

CONTEXT_TURNS = 8
MAX_CHARTS = 2
REUSE_KEY = "answer:reuse:"
STALE_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class Asked:
    """What the API hands the job (all plain values, so it can travel through Celery)."""

    org_id: uuid.UUID
    message_id: uuid.UUID
    ip: str | None = None
    user_agent: str | None = None


def _session(rt: ConnectorRuntime, org_id: uuid.UUID) -> DbSession:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    return db


def question_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()


def reuse_key(
    org_id: uuid.UUID, user_id: uuid.UUID, lens: str, question: str, dictionary_version: int | None
) -> str:
    """Identical first questions by the same person, same lens and dictionary, within the data's reuse window
    (``CACHE_TTL_SECONDS``) get the same answer without a new AI call."""
    raw = f"{org_id}|{user_id}|{lens}|{dictionary_version}|{question_hash(question)}"
    return REUSE_KEY + hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- the job
def answer_question(rt: ConnectorRuntime, factory: LLMFactory, asked: Asked) -> str:
    """Returns the final status (``done`` / ``failed``). Safe to call twice: a finished answer is left alone."""
    with _session(rt, asked.org_id) as db:
        message = db.get(Message, asked.message_id)
        if message is None or message.status in ("done", "failed"):
            db.commit()
            return message.status if message else "missing"
        conversation = db.get(Conversation, message.conversation_id)
        if conversation is None:  # pragma: no cover - the conversation cascades its messages
            db.commit()
            return "missing"
        question, turns = _question_and_turns(db, message)
        user_id = conversation.user_id
        lens = message.lens
        message.status = "running"
        message.started_at = rt.clock()
        message.progress_json = [{"code": "reading_question"}]
        db.commit()
    try:
        return _answer(
            rt, factory, asked, user_id=user_id, lens=lens, question=question, turns=turns
        )
    except Exception:
        logger.exception("answering a question failed")
        _fail(rt, asked, "internal")
        return "failed"


def _question_and_turns(db: DbSession, answer: Message) -> tuple[str, list[tuple[str, str]]]:
    earlier = list(
        db.scalars(
            select(Message)
            .where(
                Message.conversation_id == answer.conversation_id,
                Message.created_at <= answer.created_at,
                Message.id != answer.id,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(CONTEXT_TURNS * 2 + 1)
        )
    )
    earlier.reverse()
    question = earlier[-1].content if earlier and earlier[-1].role == "user" else ""
    turns: list[tuple[str, str]] = []
    for item in earlier[:-1]:
        if item.role == "user":
            turns.append(("user", item.content))
        elif item.status == "done":
            turns.append(("assistant", loop.answer_text(item.answer_json)))
    return question, turns[-CONTEXT_TURNS * 2 :]


def _progress(rt: ConnectorRuntime, asked: Asked) -> Any:
    steps: list[dict[str, Any]] = [{"code": "reading_question"}]

    def report(step: dict[str, Any]) -> None:
        clean = {k: v for k, v in step.items() if v not in (None, "")}
        if steps and steps[-1] == clean:
            return
        if (
            clean.get("code") in ("thinking", "working")
            and steps
            and steps[-1].get("code")
            in (
                "thinking",
                "working",
            )
        ):
            return
        steps.append(clean)
        with _session(rt, asked.org_id) as db:
            message = db.get(Message, asked.message_id)
            if message is not None:
                message.progress_json = list(steps)
            db.commit()

    return report


def _fail(rt: ConnectorRuntime, asked: Asked, code: str, **extra: Any) -> None:
    with _session(rt, asked.org_id) as db:
        message = db.get(Message, asked.message_id)
        if message is not None:
            message.status = "failed"
            message.error_code = code[:40]
            message.finished_at = rt.clock()
            for key, value in extra.items():
                setattr(message, key, value)
        db.commit()


def _answer(
    rt: ConnectorRuntime,
    factory: LLMFactory,
    asked: Asked,
    *,
    user_id: uuid.UUID,
    lens: str,
    question: str,
    turns: list[tuple[str, str]],
) -> str:
    settings = rt.settings
    with _session(rt, asked.org_id) as db:
        try:
            access = access_context(db, asked.org_id, user_id)
        except NoAccess:
            db.commit()
            _fail(rt, asked, "no_access")
            return "failed"
        active = dictionary_service.active(db, asked.org_id)
        org = db.get(Organization, asked.org_id)
        resolved = None
        failure = None
        if org is not None:
            try:
                # The API checked the monthly limit when the question was asked (BUILD_SPEC 7.8).
                resolved = keys.resolve(
                    db,
                    org,
                    settings=settings,
                    factory=factory,
                    secrets=rt.secrets,
                    now=rt.clock(),
                    check_limit=False,
                )
            except keys.AiUnavailable as error:
                failure = f"ai:{error.code}"
            except UsageLimitReached:  # pragma: no cover - check_limit is off here
                failure = "limit:month"
        db.commit()
    if active.dictionary is None:
        _fail(rt, asked, "no_dictionary")
        return "failed"
    visible = visible_dictionary(active.dictionary, access)
    if not visible.dictionary.datasets:
        _fail(rt, asked, "no_sources")
        return "failed"
    if resolved is None:
        _fail(rt, asked, failure or "ai:ai_not_configured")
        return "failed"

    run_id = uuid.uuid4()
    store = ResultStore()
    today = rt.clock().astimezone(_zone(access.timezone)).date()
    workspace = Workspace(
        timeout_seconds=settings.source_query_timeout_seconds,
        max_rows=settings.rows_to_llm_per_tool_call,
    )
    ctx = ToolContext(
        access=access,
        visible=visible,
        workspace=workspace,
        cache=DatasetCache(rt, asked.org_id),
        store=store,
        today=today,
        max_rows=settings.rows_to_llm_per_tool_call,
        progress=_progress(rt, asked),
    )
    system, playbooks = prompts.system_prompt(
        access=access,
        visible=visible,
        lens=lens,
        question=question,
        today=today,
        confirmed=active.confirmed,
        max_steps=settings.agent_max_steps,
    )

    def meter(response: LLMResponse) -> None:
        with _session(rt, asked.org_id) as db:
            usage.record(
                db,
                org_id=asked.org_id,
                user_id=user_id,
                run_id=run_id,
                kind="question",
                provider=response.provider,
                model=response.model,
                usage=response.usage,
                key_source=resolved.key_source,
                at=rt.clock(),
            )
            db.commit()

    try:
        outcome = loop.run(
            resolved.client,
            ctx,
            system=system,
            history=loop.history_messages(turns),
            question=question,
            limits=loop.Limits(
                max_steps=settings.agent_max_steps,
                token_budget=settings.token_budget_per_question,
                max_output_tokens=settings.llm_max_output_tokens,
            ),
            on_response=meter,
        )
    finally:
        close_client(resolved.client)
        workspace.close()

    token_usage = {
        "input_tokens": outcome.usage.input_tokens,
        "output_tokens": outcome.usage.output_tokens,
        "calls": outcome.calls,
        "models": outcome.models,
        "provider": outcome.provider,
        "key_source": resolved.key_source,
        "playbooks": playbooks,
    }
    tables_used = _tables_used(ctx)
    if outcome.answer is None:
        _fail(
            rt,
            asked,
            outcome.error or "no_answer",
            run_id=run_id,
            token_usage=token_usage,
            tool_trace=ctx.trace,
            provenance={rid: p.as_json() for rid, p in store.provenance.items()},
            dictionary_version=active.version,
        )
        _audit(rt, asked, user_id, question, lens, tables_used, status="failed")
        return "failed"

    notes = _notes(ctx, visible, outcome, confirmed=active.confirmed)
    removed = answers.impact_rule(
        outcome.answer,
        store,
        asked=answers.asks_for_advice(question),
        threshold_inr=access.impact_threshold_inr,
        threshold_pct=access.impact_threshold_pct,
    )
    if removed:
        notes.append({"code": removed})
    charts = _charts(ctx, outcome.answer.charts)
    answer_json = answers.as_json(outcome.answer, notes=notes, charts=charts)
    quality = [
        {"table": key, "name": ctx.loaded.get(key, {}).get("name", key), **q}
        for key in tables_used
        for q in ctx.quality.get(key, [])
    ]
    first_question = not turns
    with _session(rt, asked.org_id) as db:
        message = db.get(Message, asked.message_id)
        if message is not None:
            message.status = "done"
            message.content = outcome.answer.lead
            message.answer_json = answer_json
            message.tool_trace = ctx.trace
            message.provenance = {rid: p.as_json() for rid, p in store.provenance.items()}
            message.quality_warnings = quality
            message.token_usage = token_usage
            message.run_id = run_id
            message.dictionary_version = active.version
            message.finished_at = rt.clock()
            message.progress_json = [*message.progress_json, {"code": "done"}]
            conversation = db.get(Conversation, message.conversation_id)
            if conversation is not None:
                conversation.last_message_at = rt.clock()
        db.commit()
    if first_question:
        key = reuse_key(asked.org_id, user_id, lens, question, active.version)
        rt.redis.set(key, str(asked.message_id), ex=max(1, settings.cache_ttl_seconds))
    _audit(rt, asked, user_id, question, lens, tables_used, status="done")
    return "done"


def _zone(name: str) -> Any:
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def _tables_used(ctx: ToolContext) -> list[str]:
    used: list[str] = []
    for prov in ctx.store.provenance.values():
        for table in prov.tables:
            key = table.get("table")
            if key and key not in used:
                used.append(key)
    for key in ctx.loaded:
        if key not in used:
            used.append(key)
    return used


def _notes(
    ctx: ToolContext,
    visible: VisibleDictionary,
    outcome: loop.LoopOutcome,
    *,
    confirmed: bool,
) -> list[dict[str, Any]]:
    """Data notes written by code (codes; the web app turns them into sentences, D41)."""
    notes: list[dict[str, Any]] = []
    if not confirmed:
        notes.append({"code": "dictionary_not_confirmed"})
    used_metrics = {str(t.get("input", {}).get("metric")) for t in ctx.trace} | {
        str(m) for t in ctx.trace for m in (t.get("input", {}).get("metrics") or [])
    }
    gone = [
        m for m in visible.unavailable_metrics if m in used_metrics
    ] or visible.unavailable_metrics[:3]
    if gone:
        notes.append({"code": "metrics_unavailable", "metrics": gone})
    for note in ctx.notes:
        code, _, rest = note.partition(":")
        notes.append({"code": code, "detail": rest} if rest else {"code": code})
    for note in outcome.notes:
        code, _, rest = note.partition(":")
        if code == "unknown_charts_dropped":
            continue
        notes.append({"code": code, "detail": rest} if rest else {"code": code})
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in notes:
        marker = repr(sorted(item.items()))
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def _charts(ctx: ToolContext, chosen: list[str]) -> list[dict[str, Any]]:
    ids = [c for c in chosen if c in ctx.charts] or list(ctx.charts)[:1]
    return [ctx.charts[c] for c in ids[:MAX_CHARTS]]


def _audit(
    rt: ConnectorRuntime,
    asked: Asked,
    user_id: uuid.UUID,
    question: str,
    lens: str,
    tables: list[str],
    *,
    status: str,
    reused: bool = False,
) -> None:
    """``question.asked``: a hash of the text and the datasets read, never the answer (BUILD_SPEC 6.7)."""
    with _session(rt, asked.org_id) as db:
        audit.write(
            db,
            action="question.asked",
            at=rt.clock(),
            meta=audit.RequestMeta(asked.ip, asked.user_agent),
            org_id=asked.org_id,
            user_id=user_id,
            target_type="message",
            target_id=asked.message_id,
            details={
                "question_sha256": question_hash(question),
                "lens": lens,
                "datasets": tables[:50],
                "status": status,
                "reused": reused,
            },
        )
        db.commit()


def mark_stale(db: DbSession, message: Message, now: datetime) -> bool:
    """An answer whose job died (worker restarted) would stay "running" forever: after ``STALE_AFTER`` it is
    reported as failed. Returns whether it changed."""
    started = message.started_at or message.created_at
    if message.status in ("queued", "running") and started and now - started > STALE_AFTER:
        message.status = "failed"
        message.error_code = "timeout"
        message.finished_at = now
        return True
    return False
