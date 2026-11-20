"""The plans a workspace can be on (BUILD_SPEC R7, 17 "Plans"; DECISIONS D117).

Only the two M5 plans exist. Their limits are M5's: Managed AI has a monthly question limit (the workspace's own
``monthly_question_limit``, ``MANAGED_MONTHLY_QUESTION_LIMIT`` for new ones), BYOK has no platform limit. Prices are
``None``: "coming soon" until billing exists, so nothing here can be mistaken for a charge.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Organization

PLAN_KEYS = ("managed", "byok")


@dataclass(frozen=True)
class PlanInfo:
    key: str
    name: str
    summary: str
    monthly_questions: int | None  # None: no platform limit
    needs_own_key: bool
    price_inr_per_month: int | None  # None: not priced yet ("coming soon")


def catalog(org: Organization) -> list[PlanInfo]:
    return [
        PlanInfo(
            key="managed",
            name="Managed AI",
            summary="We provide the AI. A monthly number of questions and long analyses is included.",
            monthly_questions=org.monthly_question_limit,
            needs_own_key=False,
            price_inr_per_month=None,
        ),
        PlanInfo(
            key="byok",
            name="Your own AI key",
            summary="You paste your own Gemini or Claude key and pay your AI provider directly. "
            "No platform question limit.",
            monthly_questions=None,
            needs_own_key=True,
            price_inr_per_month=None,
        ),
    ]


def plan_name(org: Organization, key: str) -> str:
    return next((p.name for p in catalog(org) if p.key == key), key)
