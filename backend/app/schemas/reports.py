"""Request and response models for long tasks, reports, sharing, briefings and Today (M10)."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import _clean_text, _Strict

LensName = Literal["business", "marketing", "finance"]


# ---------------------------------------------------------------------------------------------- tasks
class TaskRequest(_Strict):
    prompt: str = Field(min_length=5, max_length=4000)
    lens: LensName = "business"

    @field_validator("prompt")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("Describe the analysis in a few words.")
        return cleaned


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    prompt: str
    lens: str
    status: str
    progress: list[dict[str, Any]]
    error_code: str | None
    report_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested: bool


class TaskList(BaseModel):
    tasks: list[TaskOut]
    running: int
    limit: int


# -------------------------------------------------------------------------------------------- reports
class ReportSummary(BaseModel):
    id: uuid.UUID
    title: str
    source: str
    lead: str
    created_at: datetime


class ReportList(BaseModel):
    reports: list[ReportSummary]


class ReportOut(BaseModel):
    id: uuid.UUID
    title: str
    source: str
    body: dict[str, Any]
    provenance: dict[str, Any]
    quality_warnings: list[dict[str, Any]]
    created_at: datetime
    mine: bool


# --------------------------------------------------------------------------------------------- shares
ObjectType = Literal["message", "report"]


class ShareRequest(_Strict):
    object_type: ObjectType
    object_id: uuid.UUID
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class ShareOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str | None
    email: str
    created_at: datetime


class Candidate(BaseModel):
    user_id: uuid.UUID
    name: str | None
    email: str
    role: str
    can_see: bool
    shared: bool


class ShareList(BaseModel):
    shares: list[ShareOut]
    candidates: list[Candidate]


class SharedSummary(BaseModel):
    id: uuid.UUID  # the share
    object_type: str
    title: str
    lead: str
    shared_by: str | None
    created_at: datetime


class SharedList(BaseModel):
    items: list[SharedSummary]


class SharedOut(BaseModel):
    id: uuid.UUID
    object_type: str
    title: str
    question: str | None
    answer: dict[str, Any] | None  # an answer, or a long task's report
    body: dict[str, Any] | None  # a briefing
    provenance: dict[str, Any]
    quality_warnings: list[dict[str, Any]]
    shared_by: str | None
    created_at: datetime


# ------------------------------------------------------------------------------------------ briefings
Frequency = Literal["daily", "weekly", "alerts_only", "off"]


class BriefingSettingsOut(BaseModel):
    enabled: bool
    local_time: str  # "06:00"
    frequency: str
    weekday: int
    language: str
    timezone: str
    default: bool
    next_at: datetime | None


class BriefingSettingsRequest(_Strict):
    enabled: bool | None = None
    local_time: time | None = None
    frequency: Frequency | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    # Answers and briefings are in English for now (DECISIONS D41); other languages arrive later.
    language: Literal["en"] | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        from zoneinfo import available_timezones

        if value is not None and value not in available_timezones():
            raise ValueError("Choose a time zone from the list.")
        return value

    @field_validator("local_time")
    @classmethod
    def _whole_minutes(cls, value: time | None) -> time | None:
        return value.replace(second=0, microsecond=0) if value is not None else None


class BriefingRunOut(BaseModel):
    id: uuid.UUID
    scheduled_for: datetime
    kind: str
    status: str
    sent_at: datetime | None
    report_id: uuid.UUID | None
    headline: str | None
    error_code: str | None


class BriefingRunList(BaseModel):
    runs: list[BriefingRunOut]


# ---------------------------------------------------------------------------------------------- today
class ChecklistOut(BaseModel):
    company: bool
    connect: bool
    dictionary: bool
    ask: bool
    done: bool


class TodayOut(BaseModel):
    name: str | None
    briefing: ReportOut | None
    suggestions: list[str]
    health_check: str
    checklist: ChecklistOut
    tasks: list[TaskOut]
    shared: list[SharedSummary]
    goals: list[dict[str, Any]] = Field(default_factory=list)
    # M18: an Owner on the Managed plan at 80% or 100% of the month's questions (None for everyone else).
    usage_warning: dict[str, Any] | None = None


def clean_title(value: str) -> str:
    return _clean_text(value)
