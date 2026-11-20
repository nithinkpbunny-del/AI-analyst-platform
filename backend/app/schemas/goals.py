"""Request and response models for goals and the pinned dashboard (M15)."""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.auth import _Strict

Direction = Literal["at_least", "at_most"]
Dates = Literal["rolling", "fixed"]
MAX_FILTERS = 3


def _clean_filters(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > MAX_FILTERS:
        raise ValueError(f"At most {MAX_FILTERS} filters.")
    out: dict[str, Any] = {}
    for key, item in value.items():
        name = key.strip()
        if not name or len(name) > 200:
            raise ValueError("A filter needs a column name.")
        if isinstance(item, bool) or not isinstance(item, str | int | float):
            raise ValueError("A filter value is one word or number.")
        text = str(item).strip()
        if not text or len(text) > 200:
            raise ValueError("A filter needs a value.")
        out[name] = text
    return out


def _name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


# ----------------------------------------------------------------------------------------------- goals
class GoalRequest(_Strict):
    metric: str = Field(min_length=1, max_length=120)
    target: float
    period_start: date
    period_end: date
    direction: Direction | None = None  # default: from the metric (refunds, costs: at most)
    name: str | None = Field(default=None, max_length=200)
    filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target")
    @classmethod
    def _target(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0 or value > 1e15:
            raise ValueError("The target is a number above zero.")
        return value

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return _name(value)

    @field_validator("filters")
    @classmethod
    def _filters(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _clean_filters(value)

    @model_validator(mode="after")
    def _period(self) -> GoalRequest:
        if self.period_end < self.period_start:
            raise ValueError("The period ends before it starts.")
        if (self.period_end - self.period_start).days > 366 * 3:
            raise ValueError("A goal's period is at most three years.")
        return self


class GoalUpdate(_Strict):
    target: float | None = None
    period_start: date | None = None
    period_end: date | None = None
    direction: Direction | None = None
    name: str | None = Field(default=None, max_length=200)
    filters: dict[str, Any] | None = None

    @field_validator("target")
    @classmethod
    def _target(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value <= 0 or value > 1e15):
            raise ValueError("The target is a number above zero.")
        return value

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return _name(value)

    @field_validator("filters")
    @classmethod
    def _filters(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _clean_filters(value)


class GoalOut(BaseModel):
    id: uuid.UUID
    name: str | None
    metric: str
    metric_label: str
    unit: str | None
    target: float
    direction: str
    period_start: date
    period_end: date
    filters: dict[str, Any]
    created_by: str | None
    created_at: datetime
    # Computed for the person looking (None until the first refresh).
    progress: dict[str, Any] | None = None


class GoalList(BaseModel):
    goals: list[GoalOut]
    computed_at: datetime | None
    refreshing: bool
    stale: bool
    error: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    quality: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MetricOption(BaseModel):
    key: str
    label: str
    unit: str | None
    good_direction: str | None
    direction: Direction
    additive: bool
    dimensions: list[dict[str, str]]  # {"key": column, "label": words}


class GoalOptions(BaseModel):
    metrics: list[MetricOption]
    today: date


class RefreshRequest(_Strict):
    force: bool = False


# ------------------------------------------------------------------------------------------- dashboard
class PinRequest(_Strict):
    message_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)
    dates: Dates = "rolling"

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        return _name(value)


class TileUpdate(_Strict):
    title: str | None = Field(default=None, max_length=200)
    shared: bool | None = None
    refresh_minutes: Literal[15, 60, 240, 1440] | None = None
    dates: Dates | None = None

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        cleaned = _name(value)
        if value is not None and cleaned is None:
            raise ValueError("A tile needs a title.")
        return cleaned


class TileOut(BaseModel):
    id: uuid.UUID
    title: str
    mine: bool
    owner_name: str | None
    shared: bool
    refresh_minutes: int
    dates: str
    fixed_sql: bool
    question: str
    conversation_id: uuid.UUID | None
    message_id: uuid.UUID | None
    created_at: datetime
    result: dict[str, Any] | None
    refreshing: bool
    stale: bool


class Dashboard(BaseModel):
    tiles: list[TileOut]
    can_edit: bool
    limit: int


class OrderRequest(_Strict):
    tile_ids: list[uuid.UUID] = Field(max_length=100)


class TileRefreshRequest(_Strict):
    tile_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    force: bool = False
