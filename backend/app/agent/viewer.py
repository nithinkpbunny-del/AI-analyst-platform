"""A ``ToolContext`` for one person without a model (M15): goal progress and dashboard tiles run the same analytics
tools as the agent, with that person's permissions (BUILD_SPEC 6.2 "Dashboard tiles run with the recipient's
permissions", P6), but no AI call is made.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.agent.access import NoAccess, access_context, visible_dictionary
from app.agent.provenance import ResultStore
from app.agent.tools import ToolContext
from app.connectors.registry import ConnectorRuntime
from app.data.cache import DatasetCache
from app.data.workspace import Workspace
from app.db.tenant import scope_to_org
from app.dictionary import service as dictionary_service


class NotReady(Exception):
    """Nothing to compute for this person: ``no_access``, ``no_dictionary`` or ``no_sources``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Viewer:
    ctx: ToolContext
    confirmed: bool


@contextmanager
def viewer_context(
    rt: ConnectorRuntime, org_id: uuid.UUID, user_id: uuid.UUID, *, pushdown: bool = False
) -> Iterator[Viewer]:
    """The person's ``ToolContext`` (their connections, their part of the dictionary, the workspace's goals they
    may see), closed afterwards. ``pushdown``: allow ``query_source`` to reach customer databases."""
    from app.agent.service import pushdown_runner
    from app.goals.service import all_specs, visible_specs

    db = rt.session_factory()
    try:
        scope_to_org(db, org_id)
        try:
            access = access_context(db, org_id, user_id)
        except NoAccess as error:
            raise NotReady("no_access") from error
        active = dictionary_service.active(db, org_id)
        specs = all_specs(db, org_id)
        if active.dictionary is None:
            raise NotReady("no_dictionary")
        visible = visible_dictionary(active.dictionary, access)
        if not visible.dictionary.datasets:
            raise NotReady("no_sources")
        goals = visible_specs(specs, visible)
        db.commit()
    finally:
        db.close()
    timeout = rt.settings.source_query_timeout_seconds
    workspace = Workspace(timeout_seconds=timeout, max_rows=rt.settings.rows_to_llm_per_tool_call)
    ctx = ToolContext(
        access=access,
        visible=visible,
        workspace=workspace,
        cache=DatasetCache(rt, org_id),
        store=ResultStore(),
        today=rt.clock().astimezone(ZoneInfo(access.timezone)).date(),
        max_rows=rt.settings.rows_to_llm_per_tool_call,
        goals=goals,
        pushdown=pushdown_runner(
            rt,
            org_id=org_id,
            allowed_connections=access.connection_ids,
            timezone=access.timezone,
            timeout_seconds=timeout,
        )
        if pushdown
        else None,
    )
    try:
        yield Viewer(ctx, active.confirmed)
    finally:
        workspace.close()
