"""Reports, long tasks, briefings and shares (BUILD_SPEC 5, R10, X3; M10).

* A **report** is a finished document kept in the app: a long task's result, a briefing, or an answer exported to
  PDF/Excel. ``body`` holds JSON blocks (the same answer shape the Ask screen shows); ``pdf_key``/``xlsx_key`` are the
  rendered files in object storage, made the first time someone exports.
* A **task** is a long analysis a person submitted: it runs in the background with the long-task limits and ends
  with a report that is emailed.
* **Briefing settings** are per person and workspace; a missing row means the defaults (on for Owners at 06:00,
  off for Analysts and Viewers). A **briefing run** is one briefing slot of one person, unique per slot, so the
  scheduler can run twice without sending twice.
* A **share** gives one member of the workspace read access to one answer or report; they still see it only when
  their own connection access covers every source it used.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any, Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

ReportSource = Literal["task", "briefing", "export"]
TaskStatus = Literal["queued", "running", "done", "failed", "cancelled"]
TASK_STATUSES: tuple[TaskStatus, ...] = ("queued", "running", "done", "failed", "cancelled")
Frequency = Literal["daily", "weekly", "alerts_only", "off"]
FREQUENCIES: tuple[Frequency, ...] = ("daily", "weekly", "alerts_only", "off")
RunStatus = Literal["queued", "running", "sent", "skipped", "failed"]
ShareObject = Literal["message", "report"]


def _org() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class Report(IdMixin, TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("source IN ('task', 'briefing', 'export')", name="source"),
        Index("ix_reports_org_id_user_id_created", "org_id", "user_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = _org()
    # Who the report was made for (the task's author, the briefing's recipient).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # The task, briefing run or message it came from.
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality_warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    pdf_key: Mapped[str | None] = mapped_column(String(300))
    xlsx_key: Mapped[str | None] = mapped_column(String(300))


class Task(IdMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint("lens IN ('business', 'marketing', 'finance')", name="lens"),
        Index("ix_tasks_org_id_user_id_created", "org_id", "user_id", "created_at"),
        Index("ix_tasks_org_id_status", "org_id", "status"),
    )

    org_id: Mapped[uuid.UUID] = _org()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    lens: Mapped[str] = mapped_column(String(16), nullable=False, default="business")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    progress_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(40))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BriefingSetting(IdMixin, TimestampMixin, Base):
    __tablename__ = "briefing_settings"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_briefing_settings_org_id_user_id"),
        CheckConstraint("frequency IN ('daily', 'weekly', 'alerts_only', 'off')", name="frequency"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
    )

    org_id: Mapped[uuid.UUID] = _org()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    local_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(6, 0))
    frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")
    # Monday = 0, for the weekly briefing.
    weekday: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    # Empty: the workspace's time zone.
    timezone: Mapped[str | None] = mapped_column(String(64))


class BriefingRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "briefing_runs"
    __table_args__ = (
        # One scheduled run per person and slot (idempotent scheduler); "send me one now" runs are not limited here.
        Index(
            "uq_briefing_runs_org_id_user_id_slot",
            "org_id",
            "user_id",
            "scheduled_for",
            unique=True,
            postgresql_where=text("kind = 'scheduled'"),
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'sent', 'skipped', 'failed')", name="status"
        ),
        CheckConstraint("kind IN ('scheduled', 'test')", name="kind"),
        Index("ix_briefing_runs_org_id_user_id_slot", "org_id", "user_id", "scheduled_for"),
    )

    org_id: Mapped[uuid.UUID] = _org()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(40))
    # Fingerprints of the unusual numbers reported, so the next briefings do not repeat them (BUILD_SPEC 10.2).
    findings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class Share(IdMixin, TimestampMixin, Base):
    __tablename__ = "shares"
    __table_args__ = (
        UniqueConstraint(
            "object_type", "object_id", "shared_with_user_id", name="uq_shares_object_recipient"
        ),
        CheckConstraint("object_type IN ('message', 'report')", name="object_type"),
        Index("ix_shares_org_id_shared_with", "org_id", "shared_with_user_id"),
    )

    org_id: Mapped[uuid.UUID] = _org()
    object_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    shared_with_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    shared_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
