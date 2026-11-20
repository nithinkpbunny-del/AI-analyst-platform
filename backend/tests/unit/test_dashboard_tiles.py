"""Pinned tiles (BUILD_SPEC X8; M15): what pinning keeps from an answer, rolling dates, and re-running a tile for
another person with their permissions. Answers are built by running the real tools on the demo company."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from app.agent.provenance import ResultStore
from app.agent.tools import ToolContext, execute
from app.dashboard import service as dashboard
from app.exports.charts import format_value
from app.llm.base import ToolCall
from app.models import Message
from tests import analytics_data as demo

AUGUST = {"start": "2026-08-01", "end": "2026-08-31"}


def _answer(*, chart: bool = True, sql: bool = False) -> tuple[Message, ToolContext]:
    """An answer like the agent makes: list_sources, kpi_summary (cited, key figure), a trend with a chart."""
    ctx = demo.agent_context()
    execute(ctx, ToolCall("t1", "list_sources", {}))
    kpi = execute(
        ctx,
        ToolCall(
            "t2",
            "kpi_summary",
            {"metrics": ["revenue"], "period": AUGUST, "compare": "previous_period"},
        ),
    ).content
    assert isinstance(kpi, dict)
    rid = kpi["result_id"]
    row = dict(zip(kpi["columns"], kpi["rows"][0], strict=False))
    figure = format_value(row["current"], "INR")
    charts: list[dict[str, Any]] = []
    if chart:
        trend = execute(
            ctx,
            ToolCall(
                "t3",
                "trend",
                {
                    "metric": "revenue",
                    "period": {"start": "2026-06-01", "end": "2026-08-31"},
                    "grain": "month",
                },
            ),
        ).content
        assert isinstance(trend, dict)
        made = execute(
            ctx,
            ToolCall(
                "t4",
                "make_chart",
                {
                    "result_id": trend["result_id"],
                    "x": "bucket",
                    "y": ["value"],
                    "title": "Revenue by month",
                },
            ),
        ).content
        assert isinstance(made, dict)
        charts.append(ctx.charts[made["chart_id"]])
    if sql:
        execute(
            ctx,
            ToolCall(
                "t5",
                "run_sql",
                {
                    "sql": "SELECT count(*) AS n FROM shopify_orders WHERE created_at >= '2026-08-01'"
                },
            ),
        )
    # A failed call is never replayed.
    execute(ctx, ToolCall("t9", "trend", {"metric": "nope", "period": AUGUST}))
    prov: dict[str, Any] = {}
    for key, item in ctx.store.provenance.items():
        data = ctx.store.data[key]
        prov[key] = {
            **item.as_json(),
            "data": {"columns": data.get("columns"), "rows": data.get("rows")},
        }
    message = Message(
        role="assistant",
        status="done",
        lens="business",
        answer_json={
            "lead": f"Revenue in August was {figure} [{rid}].",
            "key_figure": figure,
            "cited": [rid],
            "charts": charts,
        },
        tool_trace=ctx.trace,
        provenance=prov,
    )
    return message, ctx


def _viewer(today: date, hidden: tuple[str, ...] = ()) -> ToolContext:
    ctx = demo.agent_context(hidden=hidden)
    ctx.today = today
    return ctx


class TestPinning:
    def test_keeps_the_calls_needed_and_drops_the_rest(self) -> None:
        message, _ = _answer(sql=True)
        spec = dashboard.build_spec(
            message, question="How was August?", dates="rolling", anchor=date(2026, 9, 5)
        )
        tools = [c["tool"] for c in spec["calls"]]
        assert tools == [
            "kpi_summary",
            "trend",
            "make_chart",
        ]  # no list_sources, no failed call, no unused SQL
        assert spec["tables"] == ["shopify_orders"]
        assert spec["charts"] == ["c1"]
        assert spec["fixed_sql"] is False
        assert spec["anchor"] == "2026-09-05"
        assert spec["question"] == "How was August?"

    def test_finds_the_key_figure_in_its_result(self) -> None:
        message, _ = _answer()
        spec = dashboard.build_spec(message, question="q", dates="fixed", anchor=date(2026, 9, 5))
        assert spec["headline"]["column"] == "current"
        assert spec["headline"]["row"]["metric"] == "revenue"
        assert spec["headline"]["format"] == "inr"

    def test_an_answer_without_results_cannot_be_pinned(self) -> None:
        message = Message(
            answer_json={"lead": "Hello", "cited": [], "charts": []}, tool_trace=[], provenance={}
        )
        with pytest.raises(dashboard.NotPinnable):
            dashboard.build_spec(message, question="q", dates="fixed", anchor=date(2026, 9, 5))


class TestRollingDates:
    @pytest.mark.parametrize(
        ("start", "end", "anchor", "today", "expected"),
        [
            # A calendar month stays "last month".
            ("2026-08-01", "2026-08-31", "2026-09-05", "2026-11-20", ("2026-10-01", "2026-10-31")),
            # A quarter moves by whole quarters.
            ("2026-07-01", "2026-09-30", "2026-11-20", "2027-01-05", ("2026-10-01", "2026-12-31")),
            ("2026-07-01", "2026-09-30", "2026-11-20", "2026-12-28", ("2026-07-01", "2026-09-30")),
            # A year moves by years.
            ("2025-01-01", "2025-12-31", "2026-03-01", "2027-02-01", ("2026-01-01", "2026-12-31")),
            # Monday-to-Sunday weeks move by weeks.
            ("2026-11-09", "2026-11-15", "2026-11-18", "2026-11-26", ("2026-11-16", "2026-11-22")),
            # Anything else moves by days ("the last 7 days").
            ("2026-11-12", "2026-11-18", "2026-11-19", "2026-11-22", ("2026-11-15", "2026-11-21")),
            # Same day: nothing moves.
            ("2026-08-01", "2026-08-31", "2026-09-05", "2026-09-05", ("2026-08-01", "2026-08-31")),
        ],
    )
    def test_shift_period(
        self, start: str, end: str, anchor: str, today: str, expected: tuple[str, str]
    ) -> None:
        got = dashboard.shift_period(
            date.fromisoformat(start),
            date.fromisoformat(end),
            date.fromisoformat(anchor),
            date.fromisoformat(today),
        )
        assert (got[0].isoformat(), got[1].isoformat()) == expected

    def test_shift_inputs_moves_periods_and_explicit_comparisons(self) -> None:
        args = {
            "metrics": ["revenue"],
            "period": AUGUST,
            "compare": {"start": "2026-07-01", "end": "2026-07-31"},
        }
        moved = dashboard.shift_inputs(args, date(2026, 9, 5), date(2026, 10, 2))
        assert moved["period"] == {"start": "2026-09-01", "end": "2026-09-30"}
        assert moved["compare"] == {"start": "2026-08-01", "end": "2026-08-31"}
        assert moved["metrics"] == ["revenue"]
        assert args["period"] == AUGUST  # the stored spec is not changed


class TestReplay:
    def test_fixed_dates_give_the_same_numbers(self) -> None:
        message, ctx = _answer()
        spec = dashboard.build_spec(message, question="q", dates="fixed", anchor=date(2026, 9, 5))
        out = dashboard.replay(_viewer(date(2026, 11, 20)), spec)
        assert out["status"] == "ok"
        original = ctx.store.data["r2"]
        current = dict(zip(original["columns"], original["rows"][0], strict=False))["current"]
        assert out["headline"]["value"] == pytest.approx(current)
        assert out["headline"]["delta_pct"] is not None
        assert out["headline"]["good_direction"] == "up"
        assert [c["title"] for c in out["charts"]] == ["Revenue by month"]
        assert set(out["provenance"]) == {
            out["headline"]["result_id"],
            out["charts"][0]["result_id"],
        }
        assert out["table"] is None  # a chart is shown, so no table

    def test_rolling_dates_follow_the_calendar(self) -> None:
        message, _ = _answer(chart=False)
        spec = dashboard.build_spec(message, question="q", dates="rolling", anchor=date(2026, 9, 5))
        viewer = _viewer(date(2026, 10, 2))
        out = dashboard.replay(viewer, spec)
        rid = out["headline"]["result_id"]
        assert viewer.store.provenance[rid].inputs["period"] == {
            "start": "2026-09-01",
            "end": "2026-09-30",
        }
        orders = demo.frame("shopify_orders")
        day = orders["created_at"].astype(str).str[:7]
        september = orders[day == "2026-09"]
        expected = float(september["total_price"].sum() - september["total_refunds"].sum())
        assert out["headline"]["value"] == pytest.approx(expected, abs=0.01)
        assert out["table"]["rows"]  # no chart: the cited result is shown as a small table
        assert len(out["table"]["rows"]) <= dashboard.MAX_TABLE_ROWS

    def test_someone_who_cannot_see_the_table_gets_nothing(self) -> None:
        message, _ = _answer()
        spec = dashboard.build_spec(message, question="q", dates="fixed", anchor=date(2026, 9, 5))
        hidden = _viewer(date(2026, 11, 20), hidden=("shopify_orders",))
        assert not dashboard.visible_to(spec, hidden)
        assert dashboard.replay(hidden, spec) == {"status": "error", "error": "no_access"}
        assert hidden.store.provenance == {}  # nothing ran

    def test_a_tile_that_no_longer_fits_the_dictionary_says_so(self) -> None:
        message, _ = _answer()
        spec = dashboard.build_spec(message, question="q", dates="fixed", anchor=date(2026, 9, 5))
        viewer = _viewer(date(2026, 11, 20))
        del viewer.visible.dictionary.metrics["revenue"]
        assert dashboard.replay(viewer, spec) == {"status": "error", "error": "out_of_date"}

    def test_sql_dates_are_not_moved(self) -> None:
        message, ctx = _answer(chart=False, sql=True)
        rid = next(k for k, p in ctx.store.provenance.items() if p.tool == "run_sql")
        message.answer_json = {**message.answer_json, "cited": [*message.answer_json["cited"], rid]}
        spec = dashboard.build_spec(message, question="q", dates="rolling", anchor=date(2026, 9, 5))
        assert spec["fixed_sql"] is True
        out = dashboard.replay(_viewer(date(2026, 10, 2)), spec)
        assert out["notes"] == ["fixed_sql_dates"]

    def test_each_tile_gets_its_own_results(self) -> None:
        message, _ = _answer()
        spec = dashboard.build_spec(message, question="q", dates="fixed", anchor=date(2026, 9, 5))
        viewer = _viewer(date(2026, 11, 20))
        first = dashboard.replay(
            replace(viewer, store=ResultStore(), charts={}, frames={}, trace=[], notes=[]), spec
        )
        second = dashboard.replay(
            replace(viewer, store=ResultStore(), charts={}, frames={}, trace=[], notes=[]), spec
        )
        assert first["headline"] == second["headline"]
        assert first["charts"][0]["chart_id"] == second["charts"][0]["chart_id"] == "c1"
