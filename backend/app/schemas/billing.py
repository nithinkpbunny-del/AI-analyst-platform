"""Request and response bodies for the billing hooks (M18). Nothing here holds a price, a card or a key."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.ai import PlanName
from app.schemas.auth import _Strict


class PlanOut(BaseModel):
    key: PlanName
    name: str
    summary: str
    monthly_questions: int | None  # None: no platform limit
    needs_own_key: bool
    price_inr_per_month: int | None  # None: not priced yet ("coming soon")


class LimitOut(BaseModel):
    plan: PlanName
    month: str
    used: int
    limit: int | None
    percent: float | None
    level: Literal["none", "ok", "warning", "reached"]
    resets_on: date


class ProviderOut(BaseModel):
    name: str
    live: bool  # True only for a provider that can really charge: never today
    note: str


class BillingEventOut(BaseModel):
    id: str
    kind: str
    period: str | None
    created_at: datetime
    provider: str
    delivery: str
    details: dict[str, Any]


class BillingOut(BaseModel):
    plan: PlanName
    plans: list[PlanOut]
    limit: LimitOut
    provider: ProviderOut
    events: list[BillingEventOut]


class PlanChangeRequest(_Strict):
    plan: PlanName


class PlanChangeOut(BaseModel):
    current: PlanName
    target: str
    allowed: bool
    blockers: list[str]
    effects: list[dict[str, Any]]


class SummaryLineOut(BaseModel):
    code: str
    description: str
    quantity: float | int | None
    unit: str


class SummaryOut(BaseModel):
    workspace: dict[str, str]
    period: dict[str, str]
    plan: dict[str, str]
    plan_changes: list[dict[str, Any]]
    lines: list[SummaryLineOut]
    currency: str
    amount_due_inr: int
    price_status: str
    status: str
    notes: list[str]
    frozen_at: datetime | None
    # The month has not ended yet in the workspace's time zone (its numbers can still change).
    in_progress: bool = False
