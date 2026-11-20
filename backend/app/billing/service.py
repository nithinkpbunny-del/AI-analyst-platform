"""The plan change flow: preview what a change does, then confirm it (BUILD_SPEC R7, M18; D121).

The rules are M5's: Managed needs the platform's AI to be set up on the server, BYOK needs a saved key. A change
takes effect at once, keeps all history and the month's usage, and charges nothing (billing is not switched on).
``preview`` only reads; ``blockers`` are reasons a change cannot be made, ``effects`` are plain facts for the person
to read before confirming. The codes are shown by the screen in its own words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.billing import limits, plans
from app.config import Settings
from app.llm import keys
from app.llm.usage import questions_used
from app.models import Organization


@dataclass(frozen=True)
class Preview:
    current: str
    target: str
    allowed: bool
    blockers: list[str] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)


def preview(
    db: DbSession, org: Organization, target: str, *, settings: Settings, now: datetime
) -> Preview:
    blockers: list[str] = []
    effects: list[dict[str, Any]] = []
    if target not in plans.PLAN_KEYS:
        return Preview(org.plan, target, False, ["unknown_plan"])
    if target == org.plan:
        return Preview(org.plan, target, False, ["same_plan"])
    current = limits.status(db, org, now)
    if target == "byok":
        row = keys.own_key(db, org.id)
        if row is None:
            blockers.append("key_missing")
        else:
            effects.append({"code": "uses_own_key", "provider": row.provider, "last4": row.last4})
        effects.append({"code": "no_platform_limit"})
        effects.append({"code": "provider_bills_you"})
    else:
        if not keys.platform_key(settings):
            blockers.append("managed_unavailable")
        limit = org.monthly_question_limit
        effects.append({"code": "monthly_limit", "limit": limit})
        # What the month already holds counts at once under the new plan (M5 counts runs on the platform's key).
        used = questions_used(db, org.id, current.month)
        effects.append({"code": "month_so_far", "used": used, "limit": limit})
        if used >= limit:
            effects.append({"code": "already_at_limit", "resets_on": current.resets_on.isoformat()})
        elif used >= limit * limits.WARN_AT:
            effects.append({"code": "near_limit", "used": used, "limit": limit})
    effects += [{"code": "effective_now"}, {"code": "history_kept"}, {"code": "not_charged"}]
    return Preview(org.plan, target, not blockers, blockers, effects)
