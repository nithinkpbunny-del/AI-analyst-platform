"""What a briefing says, computed by code with the recipient's permissions (BUILD_SPEC 13.2, 10.2, 10.3, P4, P6).

Every figure comes from the same analytics tools the agent uses, through a ``ToolContext`` built for the recipient
(their ``AccessContext`` and the dictionary they may see), so each one has a result id and provenance:

* **KPI snapshot**: the main metrics on the latest day of data against the same weekday a week earlier, and the last
  7 days against the 7 before.
* **Unusual numbers** (10.2): ``detect_anomalies`` over the last 7 days of data, by the dimension that best splits the
  business; anything already reported to this person in the last 3 days is left out.
* **Growth signals** (10.3): ``growth_signals`` over the last 12 months.
* **Upcoming risks**: data-quality warnings of the tables read, connections that stopped working, and data that has
  not moved for days.
* **Finished long tasks** since the previous briefing.
* **Goals** (M15, X7): progress of the goals the recipient may see that are running or ended in the last week,
  with the projection to the end of their period and a status decided by code; a goal at risk or behind is the
  early warning and makes the briefing notable.

"Latest day of data" is the newest day the main table covers, up to yesterday in the recipient's time zone: a demo
workspace whose data ends months ago still gets a briefing about its last days, with a note saying how old it is.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.agent.access import (
    AccessContext,
    NoAccess,
    VisibleDictionary,
    access_context,
    visible_dictionary,
)
from app.agent.provenance import ResultStore
from app.agent.tools import ToolContext, ToolFailure, execute
from app.analytics import growth
from app.analytics.common import AnalyticsError, coverage, metric_info
from app.connectors.registry import ConnectorRuntime
from app.data.cache import DatasetCache
from app.data.workspace import Workspace
from app.db.tenant import scope_to_org
from app.dictionary import labels
from app.dictionary import service as dictionary_service
from app.goals import service as goal_service
from app.llm.base import ToolCall
from app.models import Connection, Task

logger = logging.getLogger(__name__)

MAIN_METRICS = ("revenue", "orders", "aov")
MAX_METRICS = 3
SUPPRESS_DAYS = 3
GROWTH_SUPPRESS_DAYS = 7
STALE_DATA_DAYS = 3
MAX_ITEMS = 3
MAX_GOALS = 5


class NoBriefing(Exception):
    """Nothing to brief on: no access, no dictionary or no visible tables (``code``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Facts:
    org_name: str
    industry: str | None
    timezone: str
    today: date
    as_of: date | None
    store: ResultStore
    ctx: ToolContext
    kpis: list[dict[str, Any]] = field(default_factory=list)
    unusual: list[dict[str, Any]] = field(default_factory=list)
    growth: list[dict[str, Any]] = field(default_factory=list)
    risks: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    goals: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    fingerprints: list[str] = field(default_factory=list)
    impact_threshold_inr: float = 100_000.0
    impact_threshold_pct: float = 5.0

    @property
    def notable(self) -> bool:
        return bool(self.unusual or self.growth or self.alerts or self.goals_off_track)

    @property
    def goals_off_track(self) -> list[dict[str, Any]]:
        """The early warning (X7): running goals whose projection falls short."""
        return [g for g in self.goals if g.get("status") in ("at_risk", "behind")]

    @property
    def alerts(self) -> list[dict[str, Any]]:
        """Urgent items: an unusual drop on the latest day, a connection that stopped, data that stopped moving."""
        out = [
            u
            for u in self.unusual
            if u.get("bad") and self.as_of is not None and u.get("bucket") == self.as_of.isoformat()
        ]
        out += [r for r in self.risks if r.get("kind") in ("connection", "stale")]
        return out

    def compact(self) -> dict[str, Any]:
        """What the briefing writer sees: computed facts with their result ids, no raw rows (BUILD_SPEC 6.6)."""
        return {
            "company": self.org_name,
            "industry": self.industry,
            "today": self.today.isoformat(),
            "data_as_of": self.as_of.isoformat() if self.as_of else None,
            "kpis": self.kpis,
            "alerts": self.alerts,
            "unusual": [u for u in self.unusual if u not in self.alerts],
            "growth": self.growth,
            "risks": [r for r in self.risks if r not in self.alerts],
            "finished_tasks": [{"title": t["title"]} for t in self.tasks],
            "goals": self.goals,
            "notable": self.notable,
            "impact_rule": {
                "threshold_inr": self.impact_threshold_inr,
                "threshold_pct": self.impact_threshold_pct,
            },
        }


def _session(rt: ConnectorRuntime, org_id: uuid.UUID) -> DbSession:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    return db


def gather(
    rt: ConnectorRuntime,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    reported: set[str] | None = None,
    since: datetime | None = None,
) -> Facts:
    """The facts of one person's briefing. ``reported``: fingerprints already sent in the last days (left out).
    ``since``: the previous briefing, for "finished long tasks"."""
    with _session(rt, org_id) as db:
        try:
            access = access_context(db, org_id, user_id)
        except NoAccess as error:
            db.commit()
            raise NoBriefing("no_access") from error
        active = dictionary_service.active(db, org_id)
        goal_specs = goal_service.all_specs(db, org_id)
        broken = [
            {"kind": "connection", "connection": c.name, "status": c.status}
            for c in db.scalars(
                select(Connection).where(
                    Connection.org_id == org_id,
                    Connection.status.in_(("error", "needs_reauth")),
                )
            )
            if c.id in access.connection_ids or access.role == "owner"
        ]
        tasks = [
            {"id": str(t.id), "title": t.title, "report_id": str(t.result_report_id)}
            for t in db.scalars(
                select(Task)
                .where(
                    Task.org_id == org_id,
                    Task.user_id == user_id,
                    Task.status == "done",
                    Task.finished_at >= (since or rt.clock() - timedelta(days=1)),
                )
                .order_by(Task.finished_at.desc())
                .limit(MAX_ITEMS)
            )
        ]
        db.commit()
    if active.dictionary is None:
        raise NoBriefing("no_dictionary")
    visible = visible_dictionary(active.dictionary, access)
    if not visible.dictionary.datasets:
        raise NoBriefing("no_sources")
    today = rt.clock().astimezone(ZoneInfo(access.timezone)).date()
    workspace = Workspace(
        timeout_seconds=rt.settings.source_query_timeout_seconds,
        max_rows=rt.settings.rows_to_llm_per_tool_call,
    )
    ctx = ToolContext(
        access=access,
        visible=visible,
        workspace=workspace,
        cache=DatasetCache(rt, org_id),
        store=ResultStore(),
        today=today,
        max_rows=rt.settings.rows_to_llm_per_tool_call,
        goals=goal_service.visible_specs(goal_specs, visible),
    )
    try:
        facts = compute(ctx, access, visible, reported=reported or set())
    finally:
        workspace.close()
    facts.risks = broken + facts.risks
    facts.tasks = tasks
    if not active.confirmed:
        facts.notes.append({"code": "dictionary_not_confirmed"})
    return facts


def compute(
    ctx: ToolContext,
    access: AccessContext,
    visible: VisibleDictionary,
    *,
    reported: set[str],
) -> Facts:
    """The facts, given a ready ``ToolContext`` (tests pass one over the demo tables)."""
    facts = Facts(
        org_name=access.org_name,
        industry=access.industry,
        timezone=access.timezone,
        today=ctx.today,
        as_of=None,
        store=ctx.store,
        ctx=ctx,
        impact_threshold_inr=access.impact_threshold_inr,
        impact_threshold_pct=access.impact_threshold_pct,
    )
    metrics = main_metrics(ctx)
    if not metrics:
        facts.notes.append({"code": "briefing_no_metrics"})
        return facts
    analysis = ctx.analysis()
    try:
        info = metric_info(ctx.dictionary, metrics[0])
        _, last = coverage(analysis, info.table)
    except (AnalyticsError, ToolFailure) as error:
        logger.info("briefing: main table unavailable (%s)", error)
        facts.notes.append({"code": "skipped", "detail": metrics[0]})
        return facts
    if last is None:
        return facts
    yesterday = ctx.today - timedelta(days=1)
    as_of = min(last, yesterday)
    facts.as_of = as_of
    if (ctx.today - last).days > STALE_DATA_DAYS:
        facts.risks.append(
            {
                "kind": "stale",
                "table": ctx.dictionary.datasets[info.table].name,
                "last_day": last.isoformat(),
            }
        )
        facts.notes.append({"code": "briefing_data_old", "detail": last.isoformat()})
    _kpis(ctx, facts, metrics, as_of)
    _unusual(ctx, facts, metrics[0], as_of, reported)
    _growth(ctx, facts, metrics, as_of, reported)
    _goals(ctx, facts)
    stale_days = {r["last_day"] for r in facts.risks if r.get("kind") == "stale"}
    seen: set[tuple[str, str, str]] = set()
    for table, warnings in ctx.quality.items():
        for warning in warnings[:2]:
            newest = str((warning.get("details") or {}).get("newest") or "")
            if warning.get("kind") == "stale" and newest in stale_days:
                continue  # already an alert: "<table> has no data after <day>" (M19: said once)
            marker = (table, str(warning.get("kind")), str(warning.get("message")))
            if marker in seen:
                continue
            seen.add(marker)
            facts.risks.append(
                {
                    "kind": "quality",
                    "table": ctx.dictionary.datasets[table].name
                    if table in ctx.dictionary.datasets
                    else table,
                    "issue": warning.get("kind"),
                    "message": warning.get("message"),
                }
            )
    return facts


def main_metrics(ctx: ToolContext) -> list[str]:
    """Revenue, orders and average order value when the dictionary has them; else the first metrics with a date."""
    names = [m for m in MAIN_METRICS if m in ctx.dictionary.metrics]
    repeated = labels.duplicates(
        ctx.dictionary
    )  # "sum_total_price" when "revenue" says the same (M19)
    for name, metric in ctx.dictionary.metrics.items():
        if name in repeated:
            continue
        if len(names) >= MAX_METRICS:
            break
        if name in names or metric.valid is False:
            continue
        with contextlib.suppress(AnalyticsError):
            if metric_info(ctx.dictionary, name).time_column:
                names.append(name)
    usable = []
    for name in names:
        with contextlib.suppress(AnalyticsError):
            if metric_info(ctx.dictionary, name).time_column:
                usable.append(name)
    return usable[:MAX_METRICS]


def _call(ctx: ToolContext, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    result = execute(ctx, ToolCall(f"b{len(ctx.trace) + 1}", tool, args))
    content = result.content if isinstance(result.content, dict) else {}
    if result.is_error or "result_id" not in content:
        logger.info("briefing: %s failed (%s)", tool, content.get("error"))
        return None
    return content


def _period(start: date, end: date) -> dict[str, str]:
    return {"start": start.isoformat(), "end": end.isoformat()}


def _kpis(ctx: ToolContext, facts: Facts, metrics: list[str], as_of: date) -> None:
    for label, period, compare in (
        (
            "day",
            _period(as_of, as_of),
            _period(as_of - timedelta(days=7), as_of - timedelta(days=7)),
        ),
        (
            "week",
            _period(as_of - timedelta(days=6), as_of),
            _period(as_of - timedelta(days=13), as_of - timedelta(days=7)),
        ),
    ):
        found = _call(
            ctx, "kpi_summary", {"metrics": metrics, "period": period, "compare": compare}
        )
        if found is None:
            continue
        columns = found["columns"]
        for row in found["rows"]:
            item = dict(zip(columns, row, strict=False))
            facts.kpis.append(
                {
                    "result_id": found["result_id"],
                    "span": label,
                    "metric": item.get("metric"),
                    "label": labels.metric_label(ctx.dictionary, str(item.get("metric"))),
                    "unit": item.get("unit"),
                    "current": item.get("current"),
                    "previous": item.get("previous"),
                    "change_pct": item.get("change_pct"),
                    "good_direction": item.get("good_direction"),
                }
            )


def _unusual(ctx: ToolContext, facts: Facts, metric: str, as_of: date, reported: set[str]) -> None:
    dims = []
    with contextlib.suppress(AnalyticsError):
        info = metric_info(ctx.dictionary, metric)
        dims = growth.default_dimensions(ctx.analysis(), info)
    args: dict[str, Any] = {
        "metric": metric,
        "period": _period(as_of - timedelta(days=6), as_of),
        "grain": "day",
    }
    if dims:
        args["dimension"] = dims[0]
    found = _call(ctx, "detect_anomalies", args)
    if found is None:
        return
    columns = found["columns"]
    good = None
    with contextlib.suppress(AnalyticsError):
        good = metric_info(ctx.dictionary, metric).good_direction
    for row in found["rows"]:
        item = dict(zip(columns, row, strict=False))
        bucket = str(item.get("bucket") or item.get("date") or "")[:10]
        series = str(item.get("series") or item.get("segment") or "all")
        fingerprint = f"unusual|{metric}|{series}|{bucket}"
        if fingerprint in reported:
            continue
        change = item.get("change")
        direction = "up" if isinstance(change, int | float) and change > 0 else "down"
        facts.unusual.append(
            {
                "result_id": found["result_id"],
                "metric": metric,
                "label": labels.metric_label(ctx.dictionary, metric),
                "series": series,
                "dimension": args.get("dimension"),
                "bucket": bucket,
                "value": item.get("value"),
                "expected": item.get("baseline") or item.get("expected"),
                "change": change,
                "change_pct": item.get("change_pct"),
                "direction": direction,
                "bad": good is not None and direction != good,
            }
        )
        facts.fingerprints.append(fingerprint)
        if len(facts.unusual) >= MAX_ITEMS:
            break


def _growth(
    ctx: ToolContext, facts: Facts, metrics: list[str], as_of: date, reported: set[str]
) -> None:
    metric = next((m for m in metrics if m != "aov"), metrics[0])
    start = (as_of.replace(day=1) - timedelta(days=365)).replace(day=1)
    found = _call(ctx, "growth_signals", {"metric": metric, "period": _period(start, as_of)})
    if found is None:
        return
    columns = found["columns"]
    for row in found["rows"]:
        item = dict(zip(columns, row, strict=False))
        if item.get("kind") != "streak":
            continue
        fingerprint = f"growth|{metric}|{item.get('segment')}|{item.get('end_period')}"
        if fingerprint in reported:
            continue
        facts.growth.append(
            {
                "result_id": found["result_id"],
                "metric": metric,
                "label": labels.metric_label(ctx.dictionary, metric),
                "segment": item.get("segment"),
                "periods": item.get("periods"),
                "start_value": item.get("start_value"),
                "end_value": item.get("end_value"),
                "change_pct": item.get("change_pct"),
                "repeat_share_of_increase_pct": item.get("repeat_share_of_increase_pct"),
                "upside_next_period": item.get("upside_next_period"),
                "unit": found.get("facts", {}).get("unit"),
            }
        )
        facts.fingerprints.append(fingerprint)
        if len(facts.growth) >= MAX_ITEMS:
            break


GOAL_FIELDS = (
    "goal_id",
    "name",
    "metric",
    "unit",
    "direction",
    "target",
    "period_start",
    "period_end",
    "as_of",
    "elapsed_pct",
    "actual",
    "projection",
    "projected_pct_of_target",
    "needed_per_day",
    "method",
    "status",
)


def _goals(ctx: ToolContext, facts: Facts) -> None:
    """Goals running today or ended in the last week (at most ``MAX_GOALS``), off-track ones first."""
    chosen = goal_service.current(ctx.goals, ctx.today)[:MAX_GOALS]
    if not chosen:
        return
    payload = goal_service.compute(ctx, chosen)
    order = {"behind": 0, "at_risk": 1, "missed": 2, "on_track": 3, "achieved": 4}
    rows = [
        {
            "result_id": row["result_id"],
            **{k: row.get(k) for k in GOAL_FIELDS},
            "label": labels.metric_label(ctx.dictionary, str(row.get("metric"))),
        }
        for row in payload["goals"].values()
        if row.get("status") in order
    ]
    facts.goals = sorted(rows, key=lambda r: order.get(str(r.get("status")), 9))
