"""Goals (BUILD_SPEC X7, 10.1 ``goal_progress``; M15): progress, projections and status on the demo company, the
agent tool, who may see a goal, and goals in the morning briefing. Expected values are computed here with pandas,
independently of the SQL the library runs."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest

from app.agent import provenance
from app.agent.tools import ToolContext, execute
from app.analytics.common import AnalyticsError, Period
from app.analytics.goals import GoalSpec, goal_progress, status_of
from app.briefings import facts as briefing_facts
from app.briefings import writer
from app.goals import service as goal_service
from app.llm.base import ToolCall
from tests import analytics_data as demo

SEPT = Period(date(2026, 9, 1), date(2026, 9, 30))
MID_SEPT = date(2026, 9, 16)  # as of 15 September: 15 of 30 days


def _revenue(start: date, end: date, region: str | None = None) -> float:
    orders = demo.frame("shopify_orders")
    day = pd.to_datetime(orders["created_at"]).dt.date
    rows = orders[(day >= start) & (day <= end)]
    if region:
        rows = rows[rows["shipping_region"] == region]
    return float(rows["total_price"].sum() - rows["total_refunds"].sum())


def _row(goal: GoalSpec, today: date = MID_SEPT) -> dict[str, Any]:
    result = goal_progress(demo.analysis("shopify_orders"), [goal], today=today)
    return dict(result.table.iloc[0])


class TestProgress:
    def test_actual_so_far_and_run_rate(self) -> None:
        row = _row(GoalSpec("revenue", 5_000_000, SEPT))
        actual = _revenue(date(2026, 9, 1), date(2026, 9, 15))
        assert row["as_of"] == "2026-09-15"
        assert row["elapsed_days"] == 15
        assert row["actual"] == pytest.approx(actual, abs=0.01)
        assert row["run_rate_projection"] == pytest.approx(actual * 30 / 15, abs=0.01)
        assert row["needed_per_day"] == pytest.approx((5_000_000 - actual) / 15, abs=0.01)

    def test_seasonal_projection_follows_last_year(self) -> None:
        row = _row(GoalSpec("revenue", 5_000_000, SEPT))
        actual = _revenue(date(2026, 9, 1), date(2026, 9, 15))
        last_so_far = _revenue(date(2025, 9, 1), date(2025, 9, 15))
        last_whole = _revenue(date(2025, 9, 1), date(2025, 9, 30))
        assert row["method"] == "seasonal"
        assert row["projection"] == pytest.approx(actual * last_whole / last_so_far, abs=0.01)

    def test_run_rate_when_last_year_is_missing(self) -> None:
        # The demo data starts in January 2025: a January 2025 goal has no year before it.
        goal = GoalSpec("revenue", 1_000_000, Period(date(2025, 1, 1), date(2025, 1, 31)))
        row = _row(goal, today=date(2025, 1, 11))
        assert row["method"] == "run_rate"
        assert row["projection"] == pytest.approx(row["run_rate_projection"])

    def test_status_comes_from_the_projection(self) -> None:
        projection = _row(GoalSpec("revenue", 5_000_000, SEPT))["projection"]
        assert _row(GoalSpec("revenue", projection * 0.99, SEPT))["status"] == "on_track"
        assert _row(GoalSpec("revenue", projection * 1.05, SEPT))["status"] == "at_risk"
        assert _row(GoalSpec("revenue", projection * 1.5, SEPT))["status"] == "behind"

    def test_a_finished_period_is_achieved_or_missed(self) -> None:
        august = Period(date(2026, 8, 1), date(2026, 8, 31))
        total = _revenue(august.start, august.end)
        hit = _row(GoalSpec("revenue", total - 1, august))
        miss = _row(GoalSpec("revenue", total + 1, august))
        assert (hit["status"], hit["method"]) == ("achieved", "final")
        assert miss["status"] == "missed"
        assert hit["projection"] == pytest.approx(total, abs=0.01)

    def test_a_goal_already_reached_is_achieved_early(self) -> None:
        assert _row(GoalSpec("revenue", 1_000, SEPT))["status"] == "achieved"

    def test_at_most_goals(self) -> None:
        discounts = _row(GoalSpec("discounts", 10_000_000, SEPT, "at_most"))
        assert discounts["status"] == "on_track"
        assert discounts["needed_per_day"] is None
        # A cap already passed is missed before the period ends.
        assert _row(GoalSpec("discounts", 1_000, SEPT, "at_most"))["status"] == "missed"

    def test_an_average_is_projected_as_its_value_so_far(self) -> None:
        row = _row(GoalSpec("aov", 1_000, SEPT))
        assert row["method"] == "current_value"
        assert row["projection"] == row["actual"]
        assert pd.isna(row["run_rate_projection"]) or row["run_rate_projection"] is None

    def test_filters(self) -> None:
        row = _row(GoalSpec("revenue", 700_000, SEPT, filters={"shipping_region": "South"}))
        assert row["actual"] == pytest.approx(
            _revenue(date(2026, 9, 1), date(2026, 9, 15), "South"), abs=0.01
        )

    def test_a_period_after_the_data(self) -> None:
        goal = GoalSpec("revenue", 5_000_000, Period(date(2026, 11, 1), date(2026, 11, 30)))
        result = goal_progress(demo.analysis("shopify_orders"), [goal], today=date(2026, 11, 20))
        row = dict(result.table.iloc[0])
        assert row["status"] == "not_started"
        assert result.notes == ["data_ends:2026-09-30:2026-11-01 to 2026-11-30"]

    def test_running_goal_with_old_data_says_so(self) -> None:
        goal = GoalSpec("revenue", 5_000_000, Period(date(2026, 9, 1), date(2026, 10, 31)))
        result = goal_progress(demo.analysis("shopify_orders"), [goal], today=date(2026, 10, 10))
        assert result.notes == ["data_ends:2026-09-30:2026-09-01 to 2026-10-31"]
        assert dict(result.table.iloc[0])["as_of"] == "2026-09-30"

    def test_unknown_metric_and_no_goal(self) -> None:
        with pytest.raises(AnalyticsError) as error:
            goal_progress(demo.analysis(), [GoalSpec("nope", 1, SEPT)], today=MID_SEPT)
        assert error.value.code == "unknown_metric"
        with pytest.raises(AnalyticsError):
            goal_progress(demo.analysis(), [], today=MID_SEPT)

    @pytest.mark.parametrize(
        ("direction", "target", "actual", "projection", "finished", "additive", "expected"),
        [
            ("at_least", 100, 50, 100, False, True, "on_track"),
            ("at_least", 100, 50, 91, False, True, "at_risk"),
            ("at_least", 100, 50, 89, False, True, "behind"),
            ("at_least", 100, 100, 200, False, True, "achieved"),
            ("at_least", 100, 99, 99, True, True, "missed"),
            ("at_most", 100, 50, 100, False, True, "on_track"),
            ("at_most", 100, 50, 109, False, True, "at_risk"),
            ("at_most", 100, 50, 111, False, True, "behind"),
            ("at_most", 100, 101, 150, False, True, "missed"),
            ("at_most", 100, 101, 101, False, False, "at_risk"),
            ("at_least", 100, None, None, False, True, "no_data"),
        ],
    )
    def test_status_rules(
        self,
        direction: str,
        target: float,
        actual: float | None,
        projection: float | None,
        finished: bool,
        additive: bool,
        expected: str,
    ) -> None:
        got = status_of(direction, target, actual, projection, finished=finished, additive=additive)
        assert got == expected


def _ctx(goals: list[GoalSpec], hidden: tuple[str, ...] = ()) -> ToolContext:
    ctx = demo.agent_context(hidden=hidden)
    ctx.today = MID_SEPT
    ctx.goals = goals
    return ctx


class TestAgentTool:
    def test_all_goals_with_provenance(self) -> None:
        goals = [
            GoalSpec("revenue", 5_000_000, SEPT, goal_id="g1"),
            GoalSpec("orders", 3_000, SEPT, goal_id="g2"),
        ]
        ctx = _ctx(goals)
        result = execute(ctx, ToolCall("t1", "goal_progress", {}))
        assert not result.is_error
        content = result.content
        assert isinstance(content, dict)
        rows = [dict(zip(content["columns"], r, strict=False)) for r in content["rows"]]
        assert [r["goal_id"] for r in rows] == ["g1", "g2"]
        prov = ctx.store.provenance[content["result_id"]]
        assert prov.tool == "goal_progress"
        assert prov.tables[0]["table"] == "shopify_orders"

    def test_named_goals_only(self) -> None:
        ctx = _ctx(
            [GoalSpec("revenue", 1, SEPT, goal_id="g1"), GoalSpec("orders", 1, SEPT, goal_id="g2")]
        )
        content = execute(ctx, ToolCall("t1", "goal_progress", {"goal_ids": ["g2"]})).content
        assert isinstance(content, dict)
        assert len(content["rows"]) == 1

    def test_ad_hoc_target_takes_the_metrics_direction(self) -> None:
        ctx = _ctx([])
        args = {"metric": "discounts", "target": 50_000, "period": SEPT.as_json()}
        content = execute(ctx, ToolCall("t1", "goal_progress", args)).content
        assert isinstance(content, dict)
        row = dict(zip(content["columns"], content["rows"][0], strict=False))
        assert row["direction"] == "at_most"

    def test_no_goals_is_an_error_the_model_can_act_on(self) -> None:
        result = execute(_ctx([]), ToolCall("t1", "goal_progress", {}))
        assert result.is_error
        assert isinstance(result.content, dict)
        assert result.content["error"] == "no_goal"
        bad = execute(_ctx([]), ToolCall("t2", "goal_progress", {"metric": "revenue"}))
        assert isinstance(bad.content, dict)
        assert bad.content["error"] == "bad_goal"

    def test_list_sources_lists_the_goals(self) -> None:
        ctx = _ctx([GoalSpec("revenue", 5_000_000, SEPT, goal_id="g1", name="September sales")])
        content = execute(ctx, ToolCall("t1", "list_sources", {})).content
        assert isinstance(content, dict)
        assert content["goals"][0]["name"] == "September sales"

    def test_a_hidden_table_cannot_be_reached_ad_hoc(self) -> None:
        ctx = _ctx([], hidden=("shopify_orders",))
        args = {"metric": "revenue", "target": 1, "period": SEPT.as_json()}
        result = execute(ctx, ToolCall("t1", "goal_progress", args))
        assert result.is_error
        assert isinstance(result.content, dict)
        assert result.content["error"] == "unknown_metric"


class TestVisibility:
    def test_goals_of_tables_one_cannot_see_are_left_out(self) -> None:
        open_ctx = demo.agent_context()
        hidden_ctx = demo.agent_context(hidden=("shopify_orders",))
        goal = GoalSpec("revenue", 1, SEPT, goal_id="g1")
        assert goal_service.visible_specs([goal], open_ctx.visible) == [goal]
        assert goal_service.visible_specs([goal], hidden_ctx.visible) == []

    def test_filters_must_be_visible_columns(self) -> None:
        d = demo.agent_context().visible.dictionary
        assert goal_service.filters_visible(d, "revenue", {"shipping_region": "South"})
        joined = f"shopify_customers.{next(iter(d.datasets['shopify_customers'].columns))}"
        assert goal_service.filters_visible(d, "revenue", {joined: "x"})
        assert not goal_service.filters_visible(d, "revenue", {"no_such_column": "x"})
        assert not goal_service.filters_visible(d, "revenue", {"secret_table.col": "x"})
        hidden = demo.agent_context(hidden=("shopify_customers",)).visible.dictionary
        assert not goal_service.filters_visible(hidden, "revenue", {joined: "x"})

    def test_current_goals(self) -> None:
        today = date(2026, 9, 16)
        running = GoalSpec("revenue", 1, SEPT)
        ended = GoalSpec("revenue", 1, Period(date(2026, 8, 1), today - timedelta(days=5)))
        old = GoalSpec("revenue", 1, Period(date(2026, 7, 1), date(2026, 7, 31)))
        later = GoalSpec("revenue", 1, Period(date(2026, 10, 1), date(2026, 10, 31)))
        assert goal_service.current([running, ended, old, later], today) == [running, ended]


@pytest.fixture(scope="module")
def facts() -> briefing_facts.Facts:
    ctx = demo.agent_context()
    ctx.today = MID_SEPT
    ctx.goals = [
        GoalSpec("revenue", 50_000_000, SEPT, goal_id="behind", name="Big September"),
        GoalSpec("orders", 10, SEPT, goal_id="done"),
        GoalSpec("revenue", 1, Period(date(2026, 1, 1), date(2026, 1, 31)), goal_id="old"),
    ]
    return briefing_facts.compute(ctx, ctx.access, ctx.visible, reported=set())


class TestBriefing:
    def test_running_goals_off_track_first(self, facts: briefing_facts.Facts) -> None:
        assert [g["goal_id"] for g in facts.goals] == ["behind", "done"]
        assert facts.goals[0]["status"] == "behind"
        assert facts.goals_off_track == [facts.goals[0]]
        assert facts.notable
        assert facts.compact()["goals"] == facts.goals

    def test_code_writes_a_cited_early_warning(self, facts: briefing_facts.Facts) -> None:
        written = writer.write_with_code(facts)
        lines = written.sections["goals"]
        assert lines[0].startswith("Big September (₹5.00 Cr")
        assert "Behind" in lines[0] and "a day is needed" in lines[0]
        assert "Achieved" in lines[1]
        for line in lines:
            check = provenance.NumberCheck()
            provenance.check_text(line, facts.store, check)
            assert check.unsupported == [], line

    def test_period_text(self) -> None:
        assert writer.period_text("2026-09-01", "2026-09-30") == "September 2026"
        assert writer.period_text("2026-07-01", "2026-09-30") == "1 Jul to 30 Sep 2026"
