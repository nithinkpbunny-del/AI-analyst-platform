"""Drafting, saving and confirming the business dictionary (BUILD_SPEC 9; M7).

``run_draft`` is the body of the background job (Celery ``app.tasks.jobs.draft_dictionary``):

1. profile every dataset from its newest stored copy (plain code) and keep the profile on the dataset;
2. if the structure is the same as the dictionary being continued, stop (new rows need no new draft);
3. draft in code (``draft.build``), carrying over everything the Owner or the AI already settled;
4. ask the AI to describe only what is new, from the profile alone, in as few small calls as possible; the same
   input is never sent twice (cached on the version) and nothing is sent if the AI is not available (the code
   draft still stands and says so);
5. test joins on the stored rows and metric expressions in DuckDB on a sample; drop AI metrics that do not run;
6. save it as the workspace's draft (a new one, or the existing draft updated).

Nothing here holds a database transaction open while the AI answers (DECISIONS D44, M5 notes).
Confirming (``confirm``) makes the draft the version answers use. Until then ``active`` says "not confirmed"
(``NOT_CONFIRMED_NOTE``) and M8's answers carry that note.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.connectors.base import ConnectorError
from app.connectors.registry import ConnectorRuntime
from app.data.cache import DatasetCache
from app.db.tenant import scope_to_org
from app.dictionary import ai as ai_step
from app.dictionary import links, replace, validate
from app.dictionary.draft import Built, DatasetInput, build
from app.dictionary.model import INDUSTRIES, Dictionary, Relationship, dump, parse
from app.dictionary.profiler import profile_frame, structure_hash
from app.dictionary.units import to_units
from app.llm import keys, usage
from app.llm.base import LLMError, LLMFactory, UserMessage, close_client
from app.models import Connection, Dataset, DictionaryVersion, Organization

logger = logging.getLogger(__name__)

NOT_CONFIRMED_NOTE = "dictionary not confirmed"
DRAFTING_KEY = "dictionary:drafting:"
DRAFTING_TTL_SECONDS = 1800
SAMPLE_ROWS = validate.SAMPLE_ROWS
_KEYSET_TYPES = ("text", "integer")


class ReviewPending(Exception):
    """The draft still has items the Owner has not checked, or metrics that do not run."""

    def __init__(self, items: int) -> None:
        super().__init__(f"{items} items to review")
        self.items = items


@dataclass
class DraftOutcome:
    status: str  # drafted | unchanged | nothing | busy
    version_id: uuid.UUID | None = None
    ai: str = "skipped"
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Collected:
    """What is kept of a table while drafting: a sample for metrics and the values of key-like columns."""

    sample: pd.DataFrame
    keysets: dict[str, links.KeySet]


# ------------------------------------------------------------------------------------------------ reading
def _session(rt: ConnectorRuntime, org_id: uuid.UUID) -> DbSession:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    return db


def drafting(rt: ConnectorRuntime, org_id: uuid.UUID) -> bool:
    return bool(rt.redis.exists(f"{DRAFTING_KEY}{org_id}"))


def mark_queued(rt: ConnectorRuntime, org_id: uuid.UUID) -> bool:
    """Reserve the drafting slot; ``False`` when a draft is already queued or running."""
    return bool(rt.redis.set(f"{DRAFTING_KEY}{org_id}", "queued", nx=True, ex=DRAFTING_TTL_SECONDS))


def release(rt: ConnectorRuntime, org_id: uuid.UUID) -> None:
    rt.redis.delete(f"{DRAFTING_KEY}{org_id}")


def latest_confirmed(db: DbSession, org_id: uuid.UUID) -> DictionaryVersion | None:
    return db.scalar(
        select(DictionaryVersion)
        .where(DictionaryVersion.org_id == org_id, DictionaryVersion.status == "confirmed")
        .order_by(DictionaryVersion.version.desc())
        .limit(1)
    )


def current_draft(db: DbSession, org_id: uuid.UUID) -> DictionaryVersion | None:
    return db.scalar(
        select(DictionaryVersion).where(
            DictionaryVersion.org_id == org_id, DictionaryVersion.status == "draft"
        )
    )


@dataclass(frozen=True)
class ActiveDictionary:
    """The dictionary answers should use, and whether the Owner has confirmed it (for M8)."""

    dictionary: Dictionary | None
    version: int | None
    confirmed: bool

    @property
    def note(self) -> str | None:
        return None if self.confirmed else NOT_CONFIRMED_NOTE


def active(db: DbSession, org_id: uuid.UUID) -> ActiveDictionary:
    """The newest confirmed version; before any confirmation, the draft (with the "not confirmed" note)."""
    confirmed = latest_confirmed(db, org_id)
    if confirmed is not None:
        return ActiveDictionary(parse(confirmed.content), confirmed.version, True)
    draft = current_draft(db, org_id)
    if draft is not None:
        return ActiveDictionary(parse(draft.content), draft.version, False)
    return ActiveDictionary(None, None, False)


@dataclass
class Gathered:
    org_name: str
    industry: str | None
    inputs: list[DatasetInput]
    profiles: dict[str, dict[str, Any]]  # by dataset id
    collected: dict[str, Collected]  # by dataset id
    skipped: list[str]


def _collect(frame: pd.DataFrame, profile: dict[str, Any]) -> Collected:
    keysets: dict[str, links.KeySet] = {}
    for column in profile["columns"]:
        if column["type"] in _KEYSET_TYPES and column["role"] in ("key", "dimension"):
            found = links.collect(frame, column["name"])
            if found is not None:
                keysets[column["name"]] = found
    return Collected(sample=frame.head(SAMPLE_ROWS).copy(), keysets=keysets)


def gather(rt: ConnectorRuntime, org_id: uuid.UUID) -> Gathered:
    """Profile every readable dataset of the workspace from its newest stored copy."""
    with _session(rt, org_id) as db:
        org = db.get(Organization, org_id)
        rows = db.execute(
            select(Dataset, Connection)
            .join(Connection, Connection.id == Dataset.connection_id)
            .where(Dataset.org_id == org_id)
            .order_by(Connection.created_at, Dataset.position)
        ).all()
        listing = [(d.id, d.name, d.schema_json.get("columns", []), c.id, c.name) for d, c in rows]
        org_name = org.name if org else "My business"
        industry = org.industry_template if org else None
        db.commit()
    cache = DatasetCache(rt, org_id)
    gathered = Gathered(org_name, industry, [], {}, {}, [])
    profiled: dict[uuid.UUID, dict[str, Any]] = {}
    for dataset_id, name, columns, connection_id, connection_name in listing:
        if not columns:
            continue
        try:
            cached = cache.latest(dataset_id) or cache.get(dataset_id)
        except ConnectorError as error:
            logger.info("dictionary: %s could not be read (%s)", name, error.kind)
            gathered.skipped.append(name)
            continue
        profile = profile_frame(cached.frame, columns, name=name)
        profiled[dataset_id] = profile
        gathered.profiles[str(dataset_id)] = profile
        gathered.collected[str(dataset_id)] = _collect(cached.frame, profile)
        gathered.inputs.append(
            DatasetInput(str(dataset_id), str(connection_id), connection_name, name, profile)
        )
    if profiled:
        with _session(rt, org_id) as db:
            for dataset_id, profile in profiled.items():
                dataset = db.get(Dataset, dataset_id)
                if dataset is not None:
                    dataset.profile_json = profile
            db.commit()
    return gathered


def profile_hash_of(inputs: list[DatasetInput]) -> str:
    return structure_hash(
        [{"dataset_id": i.dataset_id, "name": i.name, "profile": i.profile} for i in inputs]
    )


# ----------------------------------------------------------------------------------------------- the AI
@dataclass
class AiResult:
    status: str  # ok | cached | skipped | unavailable:<code> | failed:<kind> | unreadable
    answers: list[dict[str, Any]] = field(default_factory=list)
    input_hash: str | None = None


def _run_ai(
    rt: ConnectorRuntime,
    factory: LLMFactory,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    payloads: list[dict[str, Any]],
    *,
    fresh: bool,
) -> AiResult:
    if not payloads:
        return AiResult("skipped")
    digest = ai_step.input_hash(payloads, "v1")
    if not fresh:
        with _session(rt, org_id) as db:
            cached = db.scalar(
                select(DictionaryVersion)
                .where(
                    DictionaryVersion.org_id == org_id,
                    DictionaryVersion.ai_input_hash == digest,
                    DictionaryVersion.ai_json.is_not(None),
                )
                .order_by(DictionaryVersion.version.desc())
                .limit(1)
            )
            answers = list((cached.ai_json or {}).get("answers", [])) if cached else []
            db.commit()
        if answers:
            return AiResult("cached", answers, digest)
    with _session(rt, org_id) as db:
        org = db.get(Organization, org_id)
        if org is None:  # pragma: no cover - the caller read the workspace a moment ago
            return AiResult("skipped")
        try:
            resolved = keys.resolve(
                db,
                org,
                settings=rt.settings,
                factory=factory,
                secrets=rt.secrets,
                now=rt.clock(),
                check_limit=False,  # drafting a dictionary is not a question (D42, D59)
            )
        except keys.AiUnavailable as error:
            db.commit()
            return AiResult(f"unavailable:{error.code}")
        db.commit()
    run_id = uuid.uuid4()
    answers = []
    status = "ok"
    try:
        for payload in payloads:
            try:
                response = resolved.client.generate(
                    ai_step.SYSTEM,
                    [UserMessage(_to_text(payload))],
                    max_tokens=rt.settings.dictionary_ai_max_output_tokens,
                )
            except LLMError as error:
                logger.warning("dictionary AI call failed: %s %s", error.provider, error.kind)
                status = f"failed:{error.kind}"
                break
            with _session(rt, org_id) as db:
                usage.record(
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    run_id=run_id,
                    kind="dictionary",
                    provider=response.provider,
                    model=response.model,
                    usage=response.usage,
                    key_source=resolved.key_source,
                    at=rt.clock(),
                )
                db.commit()
            answer = ai_step.parse_answer(response.text)
            if answer is None:
                status = "unreadable"
                break
            answers.append(answer)
    finally:
        close_client(resolved.client)
    if status != "ok":
        # Whatever was understood before the failure is still used; the status tells the Owner.
        return AiResult(status, answers, None)
    return AiResult("ok", answers, digest)


def _to_text(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------------------- finishing a draft
def finish_content(built: Built, gathered: Gathered, *, ai: AiResult) -> dict[str, Any]:
    """Joins and metrics: add the AI's answers, test them on the stored rows, drop what does not hold."""
    dictionary = built.dictionary
    carried = list(dictionary.relationships)
    metrics_before = set(dictionary.metrics)
    if ai.answers:
        ai_step.apply_answers(built, ai.answers)
    proposed = dictionary.relationships[len(carried) :]
    keysets: links.KeySets = {}
    samples: dict[str, pd.DataFrame] = {}
    for key, entry in dictionary.datasets.items():
        collected = gathered.collected.get(entry.dataset_id or "")
        if collected is None:
            continue
        samples[key] = to_units(collected.sample, entry)
        for column, keyset in collected.keysets.items():
            keysets[(key, column)] = keyset
    # A join that was settled earlier is measured again on today's rows; one that no longer holds goes.
    kept: list[Relationship] = []
    dropped_joins = 0
    for relationship in carried:
        if not links.testable(relationship, keysets):
            kept.append(relationship)
            continue
        again = links.evaluate(relationship, keysets)
        if again is None:
            dropped_joins += 1
        else:
            kept.append(again.model_copy(update={"source": relationship.source}))
    additions = links.discover(dictionary, keysets)
    for relationship in proposed:
        checked = links.evaluate(relationship, keysets)
        if checked is None:
            dropped_joins += 1
        else:
            additions.append(checked)
    dictionary.relationships = links.merge(kept, additions)
    links.link_columns(dictionary)
    validate.check_metrics(dictionary, samples)
    dropped: list[dict[str, str]] = []
    for key in sorted(set(dictionary.metrics) - metrics_before):
        metric = dictionary.metrics[key]
        if metric.source == "ai" and metric.valid is False:
            dropped.append({"name": key, "error": (metric.error or "").split(":")[0]})
            del dictionary.metrics[key]
    return {"dropped_metrics": dropped, "dropped_joins": dropped_joins}


# --------------------------------------------------------------------------------------------- the draft
def save_draft(
    db: DbSession,
    org_id: uuid.UUID,
    *,
    dictionary: Dictionary,
    diff: list[dict[str, Any]],
    profile_hash: str | None,
    origin: str,
    notes: dict[str, Any],
    ai: AiResult | None,
    user_id: uuid.UUID | None,
) -> DictionaryVersion:
    content = dump(dictionary)
    draft = current_draft(db, org_id)
    if draft is not None:
        draft.content = content
        draft.diff_json = [*draft.diff_json, *diff]
        draft.profile_hash = profile_hash
        draft.notes_json = notes
        if ai is not None and ai.input_hash:
            draft.ai_input_hash, draft.ai_json = ai.input_hash, {"answers": ai.answers}
        db.flush()
        return draft
    top = db.scalar(
        select(func.max(DictionaryVersion.version)).where(DictionaryVersion.org_id == org_id)
    )
    draft = DictionaryVersion(
        org_id=org_id,
        version=(top or 0) + 1,
        status="draft",
        origin=origin,
        content=content,
        diff_json=diff,
        profile_hash=profile_hash,
        ai_input_hash=ai.input_hash if ai else None,
        ai_json={"answers": ai.answers} if ai and ai.input_hash else None,
        notes_json=notes,
        created_by=user_id,
    )
    db.add(draft)
    db.flush()
    return draft


def run_draft(
    rt: ConnectorRuntime,
    factory: LLMFactory,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    force: bool = False,
    fresh_ai: bool = False,
) -> DraftOutcome:
    """The drafting job. Safe to run twice: the second run finds an unchanged structure and stops."""
    rt.redis.set(f"{DRAFTING_KEY}{org_id}", "running", ex=DRAFTING_TTL_SECONDS)
    try:
        return _run_draft(rt, factory, org_id, user_id, force, fresh_ai)
    finally:
        release(rt, org_id)


def _run_draft(
    rt: ConnectorRuntime,
    factory: LLMFactory,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    force: bool,
    fresh_ai: bool,
) -> DraftOutcome:
    gathered = gather(rt, org_id)
    if not gathered.inputs:
        return DraftOutcome("nothing")
    digest = profile_hash_of(gathered.inputs)
    with _session(rt, org_id) as db:
        draft = current_draft(db, org_id)
        confirmed = latest_confirmed(db, org_id)
        base = draft or confirmed
        if base is not None and base.profile_hash == digest and not force:
            db.commit()
            return DraftOutcome("unchanged", base.id)
        baseline = parse(base.content) if base else None
        moves: list[dict[str, Any]] = []
        if baseline is not None:
            baseline = replace.rebind_orphans(baseline, gathered.inputs).dictionary
            over = replace.take_over_orphans(baseline, {i.dataset_id for i in gathered.inputs})
            baseline, moves = over.dictionary, over.moves
        origin = "initial" if base is None else ("redraft" if force else "schema_change")
        db.commit()
    built = build(gathered.inputs, baseline, company_name=gathered.org_name)
    built.diff[:0] = moves
    if baseline is None and gathered.industry in INDUSTRIES:
        built.dictionary.company.industry = gathered.industry  # type: ignore[assignment]
    by_key = {key: e.dataset_id or "" for key, e in built.dictionary.datasets.items()}
    profiles = {
        key: gathered.profiles[did] for key, did in by_key.items() if did in gathered.profiles
    }
    payloads = ai_step.build_payloads(
        built,
        profiles,
        tables_per_call=rt.settings.dictionary_ai_tables_per_call,
        force_all=fresh_ai and baseline is not None,
    )
    ai = _run_ai(rt, factory, org_id, user_id, payloads, fresh=fresh_ai)
    notes = finish_content(built, gathered, ai=ai)
    notes["ai"] = ai.status
    if gathered.skipped:
        notes["unreadable_tables"] = gathered.skipped
    with _session(rt, org_id) as db:
        saved = save_draft(
            db,
            org_id,
            dictionary=built.dictionary,
            diff=built.diff if baseline is not None else [],
            profile_hash=digest,
            origin=origin,
            notes=notes,
            ai=ai,
            user_id=user_id,
        )
        db.commit()
        version_id = saved.id
    if origin == "schema_change" and confirmed is not None and built.diff:
        notify_owners(rt, org_id, len(built.diff))
    return DraftOutcome("drafted", version_id, ai.status, notes)


def notify_owners(rt: ConnectorRuntime, org_id: uuid.UUID, changes: int) -> None:
    """Email the workspace's Owners that a change in the data made a new draft to check (BUILD_SPEC 9.6)."""
    if rt.email is None:
        return
    from app.email import templates
    from app.models import Membership, Organization, User

    with _session(rt, org_id) as db:
        org = db.get(Organization, org_id)
        owners = list(
            db.scalars(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    Membership.org_id == org_id,
                    Membership.role == "owner",
                    Membership.status == "active",
                    User.is_active.is_(True),
                )
            )
        )
        db.commit()
    if org is None:
        return
    summary = "1 change" if changes == 1 else f"{changes} changes"
    for owner in owners:
        rt.email.send(
            templates.render(
                "dictionary_draft",
                to=owner.email,
                org_name=org.name,
                summary=summary,
                link=f"{rt.settings.app_base_url}/dictionary",
            )
        )


# ------------------------------------------------------------------------------------------ confirming
def confirm(
    db: DbSession, org_id: uuid.UUID, *, user_id: uuid.UUID, at: datetime
) -> DictionaryVersion | None:
    """Make the draft the dictionary answers use. ``None``: there is no draft. Raises ``ReviewPending``."""
    draft = current_draft(db, org_id)
    if draft is None:
        return None
    pending = parse(draft.content).review_items()
    if pending:
        raise ReviewPending(pending)
    previous = latest_confirmed(db, org_id)
    if previous is not None:
        previous.status = "superseded"
    draft.status = "confirmed"
    draft.confirmed_by = user_id
    draft.confirmed_at = at
    db.flush()
    return draft


# ------------------------------------------------------------------------------------- replacing a source
class ReplaceError(Exception):
    """Why a connection cannot be replaced yet. ``code`` is stable for the API."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class ReplaceOutcome:
    version_id: uuid.UUID
    matched: list[tuple[str, str]]
    unmatched: list[str]


def replace_connection(
    rt: ConnectorRuntime,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    from_connection_id: uuid.UUID,
    to_connection_id: uuid.UUID,
) -> ReplaceOutcome:
    """Move the dictionary's mappings from one connection to another and save the result as the draft (R2).

    The old connection's tables are not part of the new draft; removing the old connection is the caller's job."""
    gathered = gather(rt, org_id)
    new_inputs = [i for i in gathered.inputs if i.connection_id == str(to_connection_id)]
    if not new_inputs:
        raise ReplaceError("not_read")
    with _session(rt, org_id) as db:
        base = current_draft(db, org_id) or latest_confirmed(db, org_id)
        content = base.content if base else None
        db.commit()
    if content is None:
        raise ReplaceError("no_dictionary")
    rebound = replace.rebind(
        parse(content), from_connection_id=str(from_connection_id), to_inputs=new_inputs
    )
    inputs = [i for i in gathered.inputs if i.connection_id != str(from_connection_id)]
    built = build(
        inputs,
        rebound.dictionary,
        company_name=gathered.org_name,
        flag_datasets=frozenset(rebound.flag_datasets),
        replaced_connection_id=str(from_connection_id),
    )
    notes = finish_content(built, gathered, ai=AiResult("skipped"))
    notes["ai"] = "skipped"
    with _session(rt, org_id) as db:
        saved = save_draft(
            db,
            org_id,
            dictionary=built.dictionary,
            diff=built.diff,
            profile_hash=profile_hash_of(inputs),
            origin="replacement",
            notes=notes,
            ai=None,
            user_id=user_id,
        )
        db.commit()
        version_id = saved.id
    return ReplaceOutcome(version_id, rebound.matched, rebound.unmatched)


def revalidate(rt: ConnectorRuntime, org_id: uuid.UUID, dictionary: Dictionary) -> None:
    """Run every metric again on the stored rows (after the Owner edited some). Metrics whose table has no stored
    copy right now keep their earlier result."""
    cache = DatasetCache(rt, org_id)
    samples: dict[str, pd.DataFrame] = {}
    for metric_key in dictionary.metrics:
        try:
            _, table = validate.expand(metric_key, dictionary)
        except validate.MetricError:
            continue
        entry = dictionary.datasets.get(table) if table else None
        if table is None or entry is None or table in samples or not entry.dataset_id:
            continue
        cached = cache.latest(uuid.UUID(entry.dataset_id))
        if cached is not None:
            samples[table] = to_units(cached.frame.head(SAMPLE_ROWS), entry)
    validate.check_metrics(dictionary, samples, keep_unchecked=True)
