"""Request and response bodies for the What-if screen (M9)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRequest(_Body):
    lever: Literal["price", "discount", "marketing_spend"]
    change: float = Field(allow_inf_nan=False)
    metric: str = Field(default="", max_length=120)
    scope: dict[str, str] = Field(default_factory=dict, max_length=4)
    horizon: int = Field(default=3, ge=1, le=6)
    assumptions: dict[str, float] = Field(default_factory=dict, max_length=2)


class DimensionOut(BaseModel):
    table: str
    column: str
    label: str


class MetricOptionOut(BaseModel):
    key: str
    label: str
    unit: str | None
    levers: list[str]
    dimensions: list[DimensionOut]


class ChannelSourceOut(BaseModel):
    table: str
    column: str


class OptionsOut(BaseModel):
    metrics: list[MetricOptionOut]
    channels: ChannelSourceOut | None
    industry: str | None
    max_horizon: int


class ValuesOut(BaseModel):
    values: list[str]


class RunOut(BaseModel):
    facts: dict[str, Any]
    columns: list[str]
    rows: list[list[Any]]
    notes: list[str]
    tables: list[dict[str, Any]]
    sql: list[str]
    quality_warnings: list[dict[str, Any]]
