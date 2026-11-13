"""Long tasks (BUILD_SPEC R10 "Long tasks", 13.5, 6.5): the body of the background job ``run_task``.

1. The API stored the task (``queued``) after the same checks as a question (rate limit, Managed monthly limit, AI
   available) plus at most ``CONCURRENT_LONG_TASKS_PER_ORG`` tasks queued or running per workspace.
2. Here: the agent runs with the long-task limits (``AGENT_MAX_STEPS_LONG_TASK`` steps, a larger token budget, the
   long source timeout), with the author's permissions, writing its progress to the task. Every model call is
   metered as kind ``task``. Before each model call it checks whether the author cancelled.
3. A finished task becomes a ``report`` (the answer, its provenance and data notes) with its PDF in object storage,
   and the author gets an email with the PDF attached and a link to the report in the app.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.agent import service as agent_service
from app.connectors.registry import ConnectorRuntime
from app.db.tenant import scope_to_org
from app.email import templates
from app.email.sender import Attachment
from app.exports import document as documents
from app.llm.base import LLMFactory
from app.models import Report, Task, User
from app.reports import service as reports

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(hours=2)
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
ACTIVE = ("queued", "running")

FAILURE_REASONS = {
    "limit:steps": "It needed more steps than a long task may take. Split it into smaller questions.",
    "limit:tokens": "It read more data than one long task may use. Narrow the period or the tables.",
    "no_dictionary": "The business dictionary is not ready yet. Connect data and let Analyst draft it first.",
    "no_sources": "None of the data you may see could be used. Ask an Owner about your access to connections.",
    "no_access": "You are no longer a member of this workspace.",
    "timeout": "It ran for too long and was stopped.",
}


def reason(code: str | None) -> str:
    code = code or "internal"
    if code in FAILURE_REASONS:
        return FAILURE_REASONS[code]
    if code.startswith(("ai:", "llm:")):
        return "The AI service could not be used. Check Settings, then try again later."
    return "Something went wrong while it ran. Try again; if it happens again, narrow the question."


def _session(rt: ConnectorRuntime, org_id: uuid.UUID) -> DbSession:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    return db


def title_of(prompt: str, limit: int = 120) -> str:
    text = " ".join(prompt.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def active_count(db: DbSession, org_id: uuid.UUID) -> int:
    return len(
        list(db.scalars(select(Task.id).where(Task.org_id == org_id, Task.status.in_(ACTIVE))))
    )


def mark_stale(task: Task, now: datetime) -> bool:
    """A task whose worker died stays "running" forever: after ``STALE_AFTER`` it is reported as failed."""
    started = task.started_at or task.created_at
    if task.status in ACTIVE and started and now - started > STALE_AFTER:
        task.status = "failed"
        task.error_code = "timeout"
        task.finished_at = now
        return True
    return False


# ---------------------------------------------------------------------------------------------- the job
def run_task(
    rt: ConnectorRuntime, factory: LLMFactory, org_id: uuid.UUID, task_id: uuid.UUID
) -> str:
    """Returns the final status. Safe to call twice: a task that is not ``queued`` is left alone."""
    with _session(rt, org_id) as db:
        task = db.get(Task, task_id)
        if task is None or task.status != "queued":
            db.commit()
            return task.status if task else "missing"
        if task.cancel_requested:
            task.status = "cancelled"
            task.finished_at = rt.clock()
            db.commit()
            return "cancelled"
        task.status = "running"
        task.started_at = rt.clock()
        task.progress_json = [{"code": "reading_question"}]
        spec = agent_service.RunSpec(
            org_id, task.user_id, task.lens, task.prompt, [], kind="task", long=True
        )
        db.commit()
    try:
        return _run(rt, factory, org_id, task_id, spec)
    except Exception:
        logger.exception("a long task failed")
        _finish(rt, org_id, task_id, "failed", error="internal")
        return "failed"


def _progress(rt: ConnectorRuntime, org_id: uuid.UUID, task_id: uuid.UUID) -> Any:
    steps: list[dict[str, Any]] = [{"code": "reading_question"}]

    def report(step: dict[str, Any]) -> None:
        clean = {k: v for k, v in step.items() if v not in (None, "")}
        if steps and steps[-1] == clean:
            return
        if clean.get("code") in ("thinking", "working") and steps[-1].get("code") in (
            "thinking",
            "working",
        ):
            return
        steps.append(clean)
        with _session(rt, org_id) as db:
            task = db.get(Task, task_id)
            if task is not None:
                task.progress_json = list(steps)
            db.commit()

    return report


def _cancelled(rt: ConnectorRuntime, org_id: uuid.UUID, task_id: uuid.UUID) -> Any:
    def check() -> bool:
        with _session(rt, org_id) as db:
            flag = db.scalar(select(Task.cancel_requested).where(Task.id == task_id))
            db.commit()
        return bool(flag)

    return check


def _run(
    rt: ConnectorRuntime,
    factory: LLMFactory,
    org_id: uuid.UUID,
    task_id: uuid.UUID,
    spec: agent_service.RunSpec,
) -> str:
    try:
        run = agent_service.run_agent(
            rt,
            factory,
            spec,
            progress=_progress(rt, org_id, task_id),
            should_stop=_cancelled(rt, org_id, task_id),
        )
    except agent_service.RunRefused as refused:
        _finish(rt, org_id, task_id, "failed", error=refused.code)
        _email_failure(rt, org_id, task_id)
        return "failed"
    if run.outcome.error == "cancelled":
        _finish(rt, org_id, task_id, "cancelled", run=run)
        return "cancelled"
    if run.answer_json is None:
        _finish(rt, org_id, task_id, "failed", error=run.outcome.error or "no_answer", run=run)
        _email_failure(rt, org_id, task_id)
        return "failed"

    with _session(rt, org_id) as db:
        task = db.get(Task, task_id)
        assert task is not None
        report = Report(
            org_id=org_id,
            user_id=task.user_id,
            title=task.title,
            source="task",
            source_id=task.id,
            body={"kind": "answer", "question": task.prompt, "answer": run.answer_json},
            provenance=run.provenance,
            quality_warnings=run.quality,
        )
        db.add(report)
        db.flush()
        pdf: bytes | None = None
        try:
            pdf = reports.report_file(db, rt.objects, report, "pdf")
        except Exception:  # the report stays readable in the app even if its PDF cannot be made
            logger.exception("a long task's PDF could not be made")
        task.result_report_id = report.id
        db.commit()
        report_id = report.id
    _finish(rt, org_id, task_id, "done", run=run)
    _email_done(rt, org_id, task_id, report_id, pdf)
    return "done"


def _finish(
    rt: ConnectorRuntime,
    org_id: uuid.UUID,
    task_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
    run: agent_service.AgentRun | None = None,
) -> None:
    with _session(rt, org_id) as db:
        task = db.get(Task, task_id)
        if task is not None:
            task.status = status
            task.error_code = (error or "")[:40] or None
            task.finished_at = rt.clock()
            task.progress_json = [*task.progress_json, {"code": status}]
            if run is not None:
                task.run_id = run.run_id
                task.token_usage = run.token_usage
        db.commit()


def _recipient(db: DbSession, task: Task) -> User | None:
    return db.get(User, task.user_id)


def _email_done(
    rt: ConnectorRuntime,
    org_id: uuid.UUID,
    task_id: uuid.UUID,
    report_id: uuid.UUID,
    pdf: bytes | None,
) -> None:
    if rt.email is None:
        return
    with _session(rt, org_id) as db:
        task = db.get(Task, task_id)
        report = db.get(Report, report_id)
        user = _recipient(db, task) if task else None
        if task is None or report is None or user is None:
            db.commit()
            return
        doc = reports.document_for_report(db, report)
        attach = pdf is not None and len(pdf) <= MAX_ATTACHMENT_BYTES
        message = templates.render(
            "task_done",
            to=user.email,
            subject=f"Your analysis is ready: {title_of(task.title, 80)}",
            prompt=task.prompt,
            lead=_plain(doc, doc.lead),
            evidence=[_plain(doc, e) for s in doc.sections[:1] for e in s.items][:4],
            attached=attach,
            link=f"{rt.settings.app_base_url}/reports/{report.id}",
        )
        if attach and pdf is not None:
            message = templates.with_attachments(
                message, Attachment(reports.filename(task.title, "pdf"), "application/pdf", pdf)
            )
        task.emailed_at = rt.clock()
        db.commit()
    rt.email.send(message)


def _plain(doc: documents.Document, text: str) -> str:
    """Email text without source marks (the report in the app has them)."""
    return documents.CITATION.sub("", text).strip()


def _email_failure(rt: ConnectorRuntime, org_id: uuid.UUID, task_id: uuid.UUID) -> None:
    if rt.email is None:
        return
    with _session(rt, org_id) as db:
        task = db.get(Task, task_id)
        user = _recipient(db, task) if task else None
        if task is None or user is None:
            db.commit()
            return
        message = templates.render(
            "task_failed",
            to=user.email,
            prompt=task.prompt,
            reason=reason(task.error_code),
            link=f"{rt.settings.app_base_url}/tasks",
        )
        task.emailed_at = rt.clock()
        db.commit()
    rt.email.send(message)
