"""When briefings are due (BUILD_SPEC 13.1, R10).

A beat job runs every 5 minutes. For each active member it works out the person's next slot from their settings (or
the defaults: on for Owners at 06:00 daily, off for others, in the workspace's time zone) and, when a slot falls in
the window, adds a ``briefing_runs`` row. The row is unique per person and slot, so a second scheduler pass (or two
beat processes) never sends twice; only a newly added row is queued.

Frequencies: **daily**; **weekly** (on the chosen weekday); **alerts_only** (computed daily, sent only when there is
an alert or an unusual number); **off**.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DbSession

from app.db.ids import uuid7
from app.models import BriefingRun, BriefingSetting, Membership, Organization, User

DEFAULT_TIME = time(6, 0)
# A slot is due from its time until this long after it (the worker or beat may have been down for a moment).
CATCH_UP = timedelta(minutes=30)
LEAD = timedelta(minutes=5)


@dataclass(frozen=True)
class Effective:
    """A person's briefing settings, with the defaults filled in."""

    enabled: bool
    local_time: time
    frequency: str
    weekday: int
    language: str
    timezone: str
    default: bool


def effective(setting: BriefingSetting | None, role: str, org_timezone: str) -> Effective:
    if setting is None:
        return Effective(
            enabled=role == "owner",
            local_time=DEFAULT_TIME,
            frequency="daily",
            weekday=0,
            language="en",
            timezone=org_timezone,
            default=True,
        )
    return Effective(
        enabled=setting.enabled and setting.frequency != "off",
        local_time=setting.local_time,
        frequency=setting.frequency,
        weekday=setting.weekday,
        language=setting.language,
        timezone=setting.timezone or org_timezone,
        default=False,
    )


def slot_on(day: datetime, settings: Effective) -> datetime | None:
    """The UTC moment of the slot on ``day``'s local date, or ``None`` when there is none that day."""
    zone = ZoneInfo(settings.timezone)
    local_day = day.astimezone(zone).date()
    if not settings.enabled:
        return None
    if settings.frequency == "weekly" and local_day.weekday() != settings.weekday:
        return None
    return datetime.combine(local_day, settings.local_time, tzinfo=zone).astimezone(ZoneInfo("UTC"))


def due_slot(now: datetime, settings: Effective) -> datetime | None:
    """The slot that is due at ``now`` (from ``LEAD`` before it until ``CATCH_UP`` after it), if any."""
    for day in (now - timedelta(days=1), now, now + timedelta(days=1)):
        slot = slot_on(day, settings)
        if slot is not None and slot - LEAD <= now < slot + CATCH_UP:
            return slot
    return None


def next_slot(now: datetime, settings: Effective) -> datetime | None:
    """The next slot after ``now`` (for the settings screen), within the next 8 days."""
    for offset in range(9):
        slot = slot_on(now + timedelta(days=offset), settings)
        if slot is not None and slot > now:
            return slot
    return None


def plan(db: DbSession, now: datetime) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Add a run for every person whose slot is due; returns the new runs as (org id, run id).

    ``db`` is unscoped (the scheduler looks across workspaces, like the snapshot purge); each run is still written
    with its workspace's id and handled by a scoped job."""
    rows = db.execute(
        select(Membership, Organization, BriefingSetting)
        .join(Organization, Organization.id == Membership.org_id)
        .join(User, User.id == Membership.user_id)
        .outerjoin(
            BriefingSetting,
            (BriefingSetting.org_id == Membership.org_id)
            & (BriefingSetting.user_id == Membership.user_id),
        )
        .where(
            Membership.status == "active",
            User.is_active.is_(True),
            User.email_verified_at.is_not(None),
            Organization.deletion_requested_at.is_(None),
        )
    ).all()
    created: list[tuple[uuid.UUID, uuid.UUID]] = []
    for membership, org, setting in rows:
        settings = effective(setting, membership.role, org.timezone)
        slot = due_slot(now, settings)
        if slot is None:
            continue
        run_id = db.scalar(
            insert(BriefingRun)
            .values(
                id=uuid7(),
                org_id=org.id,
                user_id=membership.user_id,
                scheduled_for=slot,
                kind="scheduled",
                status="queued",
                findings=[],
            )
            .on_conflict_do_nothing(
                index_elements=["org_id", "user_id", "scheduled_for"],
                index_where=text("kind = 'scheduled'"),
            )
            .returning(BriefingRun.id)
        )
        if run_id is not None:
            created.append((org.id, run_id))
    db.commit()
    return created
