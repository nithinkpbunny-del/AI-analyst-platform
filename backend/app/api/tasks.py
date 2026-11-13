"""Long tasks (BUILD_SPEC R10, 11 "Long tasks"; M10): submit, follow the progress, cancel, open the report.

Submitting runs the same checks as asking a question (rate limit, the Managed plan's monthly limit, AI available),
plus at most ``CONCURRENT_LONG_TASKS_PER_ORG`` tasks queued or running per workspace (429). Tasks are personal:
someone else's task answers 404, like one in another workspace.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.agent import service as agent_service
from app.api.chat import _check_can_ask, _jobs, _runtime
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.models import Task
from app.schemas.reports import TaskList, TaskOut, TaskRequest
from app.tasks import long_tasks

router = APIRouter(prefix="/tasks", tags=["long tasks"])

Ask = Annotated[Tenant, requires(Permission.ASK)]
LIST_LIMIT = 50

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    404: {"description": "Not found (or not yours, or in another workspace)"},
    409: {"description": "AI is not set up, or the task already finished"},
    422: {"description": "Validation error"},
    429: {
        "description": "Too many tasks running, too many questions, or the monthly limit is reached"
    },
}


def task_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        title=task.title,
        prompt=task.prompt,
        lens=task.lens,
        status=task.status,
        progress=list(task.progress_json),
        error_code=task.error_code,
        report_id=task.result_report_id,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        cancel_requested=task.cancel_requested,
    )


def _task(tenant: Tenant, task_id: uuid.UUID, *, lock: bool = False) -> Task:
    query = select(Task).where(
        Task.id == task_id, Task.org_id == tenant.org_id, Task.user_id == tenant.auth.user.id
    )
    found = tenant.db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise ApiError(404, "not_found", "Not found.")
    return found


@router.get("", response_model=TaskList, responses=_ERRORS, summary="My long tasks, newest first")
def list_tasks(request: Request, tenant: Ask) -> TaskList:
    tasks = list(
        tenant.db.scalars(
            select(Task)
            .where(Task.org_id == tenant.org_id, Task.user_id == tenant.auth.user.id)
            .order_by(Task.created_at.desc())
            .limit(LIST_LIMIT)
        )
    )
    now = tenant.service.clock()
    stale = [long_tasks.mark_stale(t, now) for t in tasks]  # every one, not just the first
    if any(stale):
        tenant.db.commit()
    return TaskList(
        tasks=[task_out(t) for t in tasks],
        running=long_tasks.active_count(tenant.db, tenant.org_id),
        limit=_runtime(request).settings.concurrent_long_tasks_per_org,
    )


@router.post(
    "",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Submit a long analysis; it runs in the background and the report is emailed",
)
def submit_task(body: TaskRequest, request: Request, tenant: Ask) -> TaskOut:
    settings = _runtime(request).settings
    for task in tenant.db.scalars(
        select(Task).where(Task.org_id == tenant.org_id, Task.status.in_(long_tasks.ACTIVE))
    ):
        long_tasks.mark_stale(task, tenant.service.clock())
    if long_tasks.active_count(tenant.db, tenant.org_id) >= settings.concurrent_long_tasks_per_org:
        raise ApiError(
            429,
            "too_many_tasks",
            f"This workspace already has {settings.concurrent_long_tasks_per_org} long tasks running. "
            "Wait for one to finish, or cancel one.",
        )
    _check_can_ask(request, tenant)
    task = Task(
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        prompt=body.prompt,
        title=long_tasks.title_of(body.prompt),
        lens=body.lens,
        status="queued",
        progress_json=[],
        token_usage={},
    )
    tenant.db.add(task)
    tenant.db.flush()
    audit.write(
        tenant.db,
        action="task.submitted",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="task",
        target_id=task.id,
        details={"prompt_sha256": agent_service.question_hash(body.prompt), "lens": body.lens},
    )
    tenant.db.commit()
    _jobs(request).run_task(org_id=tenant.org_id, task_id=task.id)
    # An inline queue (tests, scripts) has already run it in another session.
    tenant.db.expire_all()
    return task_out(task)


@router.get("/{task_id}", response_model=TaskOut, responses=_ERRORS, summary="One task (polled)")
def get_task(task_id: uuid.UUID, tenant: Ask) -> TaskOut:
    task = _task(tenant, task_id)
    if long_tasks.mark_stale(task, tenant.service.clock()):
        tenant.db.commit()
    return task_out(task)


@router.post(
    "/{task_id}/cancel",
    response_model=TaskOut,
    responses=_ERRORS,
    summary="Cancel a task; a running one stops before its next AI call",
)
def cancel_task(task_id: uuid.UUID, tenant: Ask) -> TaskOut:
    task = _task(tenant, task_id, lock=True)
    if task.status not in long_tasks.ACTIVE:
        raise ApiError(409, "task_finished", "This task has already finished.")
    task.cancel_requested = True
    if task.status == "queued":
        task.status = "cancelled"
        task.finished_at = tenant.service.clock()
    audit.write(
        tenant.db,
        action="task.cancelled",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="task",
        target_id=task.id,
        details={},
    )
    tenant.db.commit()
    return task_out(task)
