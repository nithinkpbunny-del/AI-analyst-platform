"""Which dashboard tiles a person sees (BUILD_SPEC 6.2 "Dashboard: pin/edit own tiles; Viewers view shared").

Their own tiles (Owners and Analysts pin them), then tiles others chose to show on everyone's dashboard. A tile
from someone else is listed only when the person may see every table it reads; otherwise its title could tell
them about data they have no access to.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DbSession

from app.agent.access import NoAccess, access_context, visible_dictionary
from app.dictionary import service as dictionary_service
from app.models import DashboardTile

MAX_TILES = 30


def tiles_for(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> list[DashboardTile]:
    """Own tiles in their order, then others' shared tiles (newest first). Not yet checked for access."""
    rows = list(
        db.scalars(
            select(DashboardTile).where(
                DashboardTile.org_id == org_id,
                or_(DashboardTile.user_id == user_id, DashboardTile.shared.is_(True)),
            )
        )
    )
    mine = sorted(
        (t for t in rows if t.user_id == user_id), key=lambda t: (t.position, t.created_at)
    )
    others = sorted(
        (t for t in rows if t.user_id != user_id), key=lambda t: t.created_at, reverse=True
    )
    return mine + others


def visible_tables(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> set[str]:
    """Table keys of the dictionary the person may see (empty when there is none)."""
    try:
        access = access_context(db, org_id, user_id)
    except NoAccess:
        return set()
    active = dictionary_service.active(db, org_id)
    if active.dictionary is None:
        return set()
    return set(visible_dictionary(active.dictionary, access).tables)


def can_see(tile: DashboardTile, user_id: uuid.UUID, tables: set[str]) -> bool:
    if tile.user_id == user_id:
        return True
    return tile.shared and all(t in tables for t in (tile.spec or {}).get("tables") or [])
