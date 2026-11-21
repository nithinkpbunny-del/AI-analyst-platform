"""One briefing: the body of the background job ``send_briefing`` (BUILD_SPEC 13).

1. The run (``briefing_runs`` row) was added by the scheduler or by "Send me a briefing now".
2. Facts are computed by code with the recipient's permissions (``facts.gather``); unusual numbers already reported
   to them in the last 3 days (growth signals: 7 days) are left out.
3. The text is written by the AI in one metered call (kind ``briefing``; it does not count towards the Managed
   question limit, D42) or, when AI cannot be used, by code (``writer``).
4. It is stored as a ``report`` (source ``briefing``) and emailed: HTML for phones plus plain text, every figure
   with a numbered mark that links to the report in the app, and a link to the briefing settings.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.briefings import facts as briefing_facts
from app.briefings import schedule, writer
from app.connectors.registry import ConnectorRuntime
from app.db.tenant import scope_to_org
from app.email import templates
from app.exports import document as documents
from app.exports.document import BRIEFING_HEADINGS
from app.llm import keys, usage
from app.llm.base import LLMFactory, LLMResponse, close_client
from app.models import BriefingRun, BriefingSetting, Membership, Organization, Report, User
from app.reports import service as reports

logger = logging.getLogger(__name__)

ORDER = ("alerts", "unusual", "suggested", "growth", "goals", "risks", "tasks", "health")


def _session(rt: ConnectorRuntime, org_id: uuid.UUID) -> DbSession:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    return db


def title_for(day: datetime, timezone: str) -> str:
    local = day.astimezone(ZoneInfo(timezone))
    return f"Morning briefing, {local.day} {local:%b %Y}"


def settings_for(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> schedule.Effective:
    org = db.get(Organization, org_id)
    membership = db.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    setting = db.scalar(
        select(BriefingSetting).where(
            BriefingSetting.org_id == org_id, BriefingSetting.user_id == user_id
        )
    )
    return schedule.effective(
        setting,
        membership.role if membership else "viewer",
        org.timezone if org else "Asia/Kolkata",
    )


def _reported(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> set[str]:
    """Fingerprints sent in the last days: unusual numbers for 3 days, growth signals for 7 (BUILD_SPEC 10.2)."""
    found: set[str] = set()
    for run in db.scalars(
        select(BriefingRun).where(
            BriefingRun.org_id == org_id,
            BriefingRun.user_id == user_id,
            BriefingRun.status == "sent",
            BriefingRun.sent_at >= now - timedelta(days=briefing_facts.GROWTH_SUPPRESS_DAYS),
        )
    ):
        recent = run.sent_at is not None and run.sent_at >= now - timedelta(
            days=briefing_facts.SUPPRESS_DAYS
        )
        for fingerprint in run.findings or []:
            if fingerprint.startswith("growth|") or recent:
                found.add(fingerprint)
    return found


def _previous_sent(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> datetime | None:
    return db.scalar(
        select(BriefingRun.sent_at)
        .where(
            BriefingRun.org_id == org_id,
            BriefingRun.user_id == user_id,
            BriefingRun.status == "sent",
        )
        .order_by(BriefingRun.sent_at.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------------------------------- the job
def send_briefing(
    rt: ConnectorRuntime, factory: LLMFactory, org_id: uuid.UUID, run_id: uuid.UUID
) -> str:
    """Returns the final status. Safe to call twice: a run that is not ``queued`` is left alone."""
    with _session(rt, org_id) as db:
        run = db.get(BriefingRun, run_id)
        if run is None or run.status != "queued":
            db.commit()
            return run.status if run else "missing"
        run.status = "running"
        user_id = run.user_id
        kind = run.kind
        settings = settings_for(db, org_id, user_id)
        reported = _reported(db, org_id, user_id, rt.clock())
        since = _previous_sent(db, org_id, user_id)
        db.commit()
    try:
        return _send(rt, factory, org_id, run_id, user_id, kind, settings, reported, since)
    except Exception:
        logger.exception("a briefing failed")
        _end(rt, org_id, run_id, "failed", error="internal")
        return "failed"


def _end(
    rt: ConnectorRuntime,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
    report_id: uuid.UUID | None = None,
    findings: list[str] | None = None,
) -> None:
    with _session(rt, org_id) as db:
        run = db.get(BriefingRun, run_id)
        if run is not None:
            run.status = status
            run.error_code = error
            if report_id is not None:
                run.report_id = report_id
            if findings is not None:
                run.findings = findings
            if status == "sent":
                run.sent_at = rt.clock()
        db.commit()


def _send(
    rt: ConnectorRuntime,
    factory: LLMFactory,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    settings: schedule.Effective,
    reported: set[str],
    since: datetime | None,
) -> str:
    try:
        facts = briefing_facts.gather(rt, org_id, user_id, reported=reported, since=since)
    except briefing_facts.NoBriefing as nothing:
        _end(rt, org_id, run_id, "skipped", error=nothing.code)
        return "skipped"
    if (
        kind == "scheduled"
        and settings.frequency == "alerts_only"
        and not (facts.alerts or facts.unusual)
    ):
        _end(rt, org_id, run_id, "skipped", error="nothing_to_report")
        return "skipped"
    written = _write(rt, factory, org_id, user_id, run_id, facts, settings.language)
    body = _body(facts, written)
    with _session(rt, org_id) as db:
        org = db.get(Organization, org_id)
        user = db.get(User, user_id)
        assert org is not None
        report = Report(
            org_id=org_id,
            user_id=user_id,
            title=title_for(rt.clock(), settings.timezone),
            source="briefing",
            source_id=run_id,
            body=body,
            provenance=provenance_of(facts, body),
            quality_warnings=[
                {"table": key, "name": facts.ctx.loaded.get(key, {}).get("name", key), **q}
                for key, items in facts.ctx.quality.items()
                for q in items
            ],
        )
        db.add(report)
        db.flush()
        message = _email(rt, db, org, user, report) if user is not None else None
        db.commit()
        report_id = report.id
    if message is not None and rt.email is not None:
        rt.email.send(message)
    _end(rt, org_id, run_id, "sent", report_id=report_id, findings=facts.fingerprints)
    return "sent"


def _write(
    rt: ConnectorRuntime,
    factory: LLMFactory,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    facts: briefing_facts.Facts,
    language: str,
) -> writer.Written:
    key = writer.cache_key(facts, language)
    cached = rt.redis.get(key)
    if cached:
        try:
            raw = json.loads(cached)
            return writer.Written(**raw)
        except (TypeError, ValueError):
            pass
    with _session(rt, org_id) as db:
        org = db.get(Organization, org_id)
        resolved = None
        if org is not None:
            try:
                resolved = keys.resolve(
                    db,
                    org,
                    settings=rt.settings,
                    factory=factory,
                    secrets=rt.secrets,
                    now=rt.clock(),
                    check_limit=False,
                )
            except keys.AiUnavailable as error:
                logger.info("briefing: no AI (%s); code writes it", error.code)
        db.commit()
    written = None
    if resolved is not None:

        def meter(response: LLMResponse) -> None:
            with _session(rt, org_id) as db:
                usage.record(
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    run_id=run_id,
                    kind="briefing",
                    provider=response.provider,
                    model=response.model,
                    usage=response.usage,
                    key_source=resolved.key_source,
                    at=rt.clock(),
                )
                db.commit()

        try:
            written = writer.write_with_ai(resolved.client, facts, on_response=meter)
        finally:
            close_client(resolved.client)
    if written is None:
        written = writer.write_with_code(facts)
    else:
        rt.redis.set(key, json.dumps(written.__dict__), ex=writer.CACHE_SECONDS)
    # Finished long tasks are listed by code (titles, no figures).
    written.sections["tasks"] = [f"Your analysis is ready: {t['title']}" for t in facts.tasks]
    # The goals' early warning is never lost: when the AI left them out, code writes them (X7).
    if facts.goals and not written.sections.get("goals"):
        written.sections["goals"] = writer.goal_sentences(facts.goals)
    # Each point once: an alert is not repeated under "Upcoming risks", nor any item twice (M19).
    written.sections = writer.without_repeats(written.sections, facts)
    return written


def _body(facts: briefing_facts.Facts, written: writer.Written) -> dict[str, Any]:
    sections = [
        {"key": key, "items": written.sections.get(key) or []}
        for key in ORDER
        if written.sections.get(key)
    ]
    return {
        "kind": "briefing",
        "subject": written.subject,
        "headline": written.headline,
        "key_figure": written.key_figure,
        "data_as_of": facts.as_of.isoformat() if facts.as_of else None,
        "written_by": written.by,
        "sections": sections,
        "kpis": facts.kpis,
        # Structured findings for the Today screen's suggested questions.
        "unusual": facts.unusual,
        "growth": facts.growth,
        "tasks": facts.tasks,
        "goals": facts.goals,
        "notable": facts.notable,
        "notes": [*facts.notes, *written.notes],
    }


def provenance_of(facts: briefing_facts.Facts, body: dict[str, Any]) -> dict[str, Any]:
    """Provenance of every result; cited ones keep the rows they showed (exports include them)."""
    texts = [str(body.get("headline") or "")] + [i for s in body["sections"] for i in s["items"]]
    cited = {c for t in texts for c in documents.CITATION.findall(t) for c in c.split(",")}
    cited = {c.strip() for c in cited}
    out: dict[str, Any] = {}
    for rid, prov in facts.store.provenance.items():
        item = prov.as_json()
        data = facts.store.data.get(rid) or {}
        if rid in cited and data.get("columns"):
            item["data"] = {
                "columns": data["columns"],
                "rows": list(data.get("rows") or [])[:50],
                "total_rows": data.get("total_rows"),
            }
        out[rid] = item
    return out


# --------------------------------------------------------------------------------------------- email
def _email_html(doc: documents.Document, text: str, link: str, key: str | None = None) -> Markup:
    out = []
    marked = False
    for piece, numbers in doc.cite(text):
        html = escape(piece)
        if key and not marked and escape(key) in html:
            html = html.replace(
                escape(key),
                '<span style="background:rgba(242,194,48,0.55);padding:0 2px;">'
                + escape(key)
                + "</span>",
                1,
            )
            marked = True
        out.append(html)
        for n in numbers:
            out.append(
                f'<sup><a href="{escape(link)}" style="color:#3A36A8;text-decoration:none;">{n}</a></sup>'
            )
    return Markup("".join(out))  # noqa: S704 - every piece above is escaped


def _email(
    rt: ConnectorRuntime, db: DbSession, org: Organization, user: User, report: Report
) -> Any:
    doc = reports.document_for_report(db, report)
    link = f"{rt.settings.app_base_url}/reports/{report.id}"
    body = dict(report.body)
    sections = [
        {
            "heading": BRIEFING_HEADINGS.get(s["key"], s["key"]),
            "items": [_email_html(doc, i, link) for i in s["items"]],
            "plain": [doc.plain(i) for i in s["items"]],
        }
        for s in body["sections"]
    ]
    return templates.render(
        "briefing",
        to=user.email,
        subject=str(body.get("subject") or report.title)[:120],
        name=user.name or "there",
        org_name=org.name,
        title=report.title,
        headline=_email_html(doc, str(body.get("headline") or ""), link, body.get("key_figure")),
        headline_plain=doc.plain(str(body.get("headline") or "")),
        data_as_of=body.get("data_as_of"),
        sections=sections,
        notes=doc.notes,
        link=link,
        today_link=f"{rt.settings.app_base_url}/today",
        settings_link=f"{rt.settings.app_base_url}/briefings",
    )
