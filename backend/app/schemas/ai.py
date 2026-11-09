"""Request and response bodies for the AI plan, BYOK key, usage and connection check (M5).

No response ever contains a key: only ``last4`` and the provider (BUILD_SPEC 6.4).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import _Strict

ProviderName = Literal["gemini", "claude"]
PlanName = Literal["managed", "byok"]
_KEY_CHARS = re.compile(r"[A-Za-z0-9._\-]+")


class OwnKeyOut(BaseModel):
    provider: ProviderName
    last4: str
    added_at: datetime
    added_by_name: str | None


class ManagedOut(BaseModel):
    provider: ProviderName
    available: bool  # the platform key is configured on the server
    monthly_question_limit: int


class AiSettingsOut(BaseModel):
    plan: PlanName
    managed: ManagedOut
    own_key: OwnKeyOut | None


class PlanRequest(_Strict):
    plan: PlanName


class KeyRequest(_Strict):
    provider: ProviderName
    api_key: str = Field(min_length=1, max_length=512)

    @field_validator("api_key")
    @classmethod
    def _plausible_key(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 20 or not _KEY_CHARS.fullmatch(value):
            raise ValueError(
                "This does not look like an API key. Copy the whole key and paste it again."
            )
        return value


class AiCheckOut(BaseModel):
    reply: str
    provider: ProviderName
    model: str
    key_source: PlanName
    input_tokens: int
    output_tokens: int


# --------------------------------------------------------------------------------------------------- usage
class UsageTotalsOut(BaseModel):
    questions: int
    limited_questions: int
    tasks: int
    briefings: int
    checks: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    cost_unknown_calls: int


class PersonUsageOut(BaseModel):
    user_id: uuid.UUID | None
    name: str | None
    email: str | None
    questions: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class MonthUsageOut(BaseModel):
    month: str
    questions: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class UsageOut(BaseModel):
    month: str  # YYYY-MM in the workspace's time zone
    resets_on: date
    is_current_month: bool
    plan: PlanName
    limit: int | None  # None on BYOK: no platform limit
    totals: UsageTotalsOut
    people: list[PersonUsageOut]
    history: list[MonthUsageOut]
