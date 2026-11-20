"""The agent with the fake LLM over the UrbanCart demo data (BUILD_SPEC 7, 6.5, 7.7, R9, R14). No model is called.

Covers the tools (results, provenance, limits, access), the loop (tool steps, the repair turn for unsupported
figures, the step and token limits, plain-text answers, model errors, a model switch mid-question) and the
rules applied to the answer (number check, ₹ impact).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent import answer as answers
from app.agent import loop, prompts
from app.agent.provenance import ResultStore, figures
from app.agent.tools import SPECS, ToolContext, execute
from app.llm.base import LLMError, ToolCall, ToolResultsMessage, Usage, UserMessage
from app.llm.fake import FakeLLM, FakeTurn
from tests.analytics_data import agent_context

Q3 = {"start": "2026-07-01", "end": "2026-09-30"}
Q2 = {"start": "2026-04-01", "end": "2026-06-30"}


def call(name: str, **args: Any) -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, input=args)


def run_tool(ctx: ToolContext, name: str, **args: Any) -> dict[str, Any]:
    result = execute(ctx, call(name, **args))
    content: dict[str, Any] = result.content
    return content


def give(**fields: Any) -> FakeTurn:
    return FakeTurn(tool_calls=[ToolCall("final", "give_answer", fields)])


def tools_turn(*calls: ToolCall) -> FakeTurn:
    return FakeTurn(tool_calls=list(calls))


@pytest.fixture
def ctx() -> ToolContext:
    return agent_context()


def run(
    client: FakeLLM,
    ctx: ToolContext,
    question: str = "Why did South revenue fall in Q3?",
    **limits: Any,
) -> loop.LoopOutcome:
    return loop.run(
        client,
        ctx,
        system="system",
        history=[],
        question=question,
        limits=loop.Limits(**limits),
    )


# ------------------------------------------------------------------------------------------------ tools
class TestTools:
    def test_list_sources_shows_only_visible_tables_and_metrics(self) -> None:
        ctx = agent_context(hidden=("budget_vs_actual",))
        content = run_tool(ctx, "list_sources")
        tables = {t["table"] for t in content["tables"]}
        assert "budget_vs_actual" not in tables and "shopify_orders" in tables
        metrics = {m["metric"] for m in content["metrics"]}
        assert "revenue" in metrics and "sum_budget_inr" not in metrics
        # Metrics of a table the person may not see are not named anywhere.
        assert content["unavailable_metrics"] == []

    def test_a_hidden_table_cannot_be_reached_by_any_tool(self) -> None:
        ctx = agent_context(hidden=("budget_vs_actual",))
        assert (
            run_tool(ctx, "describe_dataset", table="budget_vs_actual")["error"] == "unknown_table"
        )
        sql = run_tool(ctx, "run_sql", sql="SELECT * FROM budget_vs_actual")
        assert sql["error"] == "unsafe_sql" and "unknown_table" in sql["detail"]
        assert run_tool(ctx, "finance_summary", kind="budget_variance")["error"] == "no_budget_data"
        assert not ctx.workspace.has("budget_vs_actual")

    def test_a_driver_analysis_result_carries_provenance(self, ctx: ToolContext) -> None:
        content = run_tool(
            ctx,
            "driver_analysis",
            metric="revenue",
            period=Q3,
            compare=Q2,
            dimensions=["shipping_region"],
        )
        assert content["result_id"] == "r1"
        assert content["facts"]["by_dimension"]["shipping_region"]["falling"] == ["South"]
        assert content["facts"]["change_pct"] == pytest.approx(5.49, abs=0.01)
        prov = ctx.store.provenance["r1"]
        assert prov.tool == "driver_analysis" and prov.sql
        assert [t["table"] for t in prov.tables] == ["shopify_orders"]
        assert ctx.trace[-1]["result_id"] == "r1" and ctx.trace[-1]["tables"] == ["shopify_orders"]

    def test_run_sql_joins_across_sources_and_caps_rows(self) -> None:
        ctx = agent_context(max_rows=5)
        content = run_tool(
            ctx,
            "run_sql",
            sql="SELECT o.shipping_region, c.segment, count(*) AS n FROM shopify_orders o "
            "JOIN shopify_customers c ON o.customer_id = c.id GROUP BY 1, 2 ORDER BY 1, 2",
        )
        assert len(content["rows"]) == 5 and content["truncated"] and content["total_rows"] == 8
        assert run_tool(ctx, "run_sql", sql="DELETE FROM shopify_orders")["error"] == "unsafe_sql"
        assert (
            run_tool(ctx, "run_sql", sql="SELECT nope FROM shopify_orders")["error"] == "sql_error"
        )

    def test_describe_hides_personal_columns(self, ctx: ToolContext) -> None:
        ref = next(r for r in ctx.visible.tables.values() if r.name == "shopify_customers")
        ref.profile.update(
            {"columns": [{"name": "email", "personal": True, "samples": ["a@b.in"]}]}
        )
        content = run_tool(ctx, "describe_dataset", table="shopify_customers")
        sample = content["sample_rows"]
        email = sample["columns"].index("email")
        assert {row[email] for row in sample["rows"]} == {"(hidden)"}
        column = next(c for c in content["columns"] if c["column"] == "email")
        assert column["examples"] is None

    def test_errors_go_back_to_the_model_with_a_hint(self, ctx: ToolContext) -> None:
        bad = run_tool(ctx, "kpi_summary", metrics=["no_such_metric"], period=Q3)
        assert bad["error"] == "unknown_metric" and bad["hint"]
        assert (
            run_tool(ctx, "kpi_summary", metrics=["revenue"], period={"start": "July"})["error"]
            == "bad_period"
        )
        assert run_tool(ctx, "no_such_tool")["error"] == "unknown_tool"
        assert (
            run_tool(ctx, "make_chart", result_id="r99", type="bar", x="a", y=["b"], title="t")[
                "error"
            ]
            == "unknown_result"
        )

    def test_a_chart_from_a_trend(self, ctx: ToolContext) -> None:
        trend = run_tool(
            ctx, "trend", metric="revenue", period=Q3, grain="month", split_by="shipping_region"
        )
        chart = run_tool(
            ctx,
            "make_chart",
            result_id=trend["result_id"],
            type="line",
            x="bucket",
            y=["value"],
            series="series",
            title="South fell while other regions grew",
        )
        spec = ctx.charts[chart["chart_id"]]
        assert spec["x"]["values"] == ["2026-07-01", "2026-08-01", "2026-09-01"]
        assert {s["name"] for s in spec["series"]} == {"East", "North", "South", "West"}
        assert spec["unit"] == "INR" and spec["title"].startswith("South")

    def test_every_tool_has_a_schema(self) -> None:
        for spec in SPECS.values():
            assert spec.parameters["type"] == "object" and spec.description
        assert "what_if" in SPECS  # M9
        assert "growth_signals" in SPECS  # M10
        assert "goal_progress" in SPECS  # M15


# ------------------------------------------------------------------------------------------------- loop
class TestLoop:
    def test_tools_then_an_answer_with_checked_figures(self, ctx: ToolContext) -> None:
        client = FakeLLM(
            [
                tools_turn(
                    call(
                        "driver_analysis",
                        metric="revenue",
                        period=Q3,
                        compare=Q2,
                        dimensions=["shipping_region"],
                    )
                ),
                give(
                    lead="South revenue fell 17.7% [r1] in Q3 while every other region grew.",
                    key_figure="17.7%",
                    evidence=["South: ₹28.0 L [r1], down from ₹34.0 L [r1]", "West grew 18.5%"],
                    next_questions=["Which products fell most in the South?"],
                ),
            ]
        )
        outcome = run(client, ctx)
        assert outcome.error is None and outcome.calls == 2
        answer = outcome.answer
        assert answer is not None
        assert answer.evidence[1] == "West grew 18.5% [r1]"  # the missing citation is added
        assert outcome.repaired is False
        # The second call saw the tool result.
        second = client.calls[1].messages
        assert isinstance(second[-1], ToolResultsMessage)
        assert second[-1].results[0].content["result_id"] == "r1"
        assert {t.name for t in client.calls[0].tools} >= {
            "driver_analysis",
            "give_answer",
            "run_sql",
        }

    def test_unsupported_figures_get_one_repair_turn(self, ctx: ToolContext) -> None:
        client = FakeLLM(
            [
                tools_turn(call("kpi_summary", metrics=["revenue"], period=Q3, compare=Q2)),
                give(lead="Revenue rose 9.9% [r1].", evidence=["It reached ₹1.25 Cr [r1]"]),
                give(lead="Revenue rose 5.5% [r1].", evidence=["It reached ₹1.25 Cr [r1]"]),
            ]
        )
        outcome = run(client, ctx, question="How did revenue do in Q3?")
        assert outcome.repaired and outcome.answer is not None
        assert outcome.answer.lead == "Revenue rose 5.5% [r1]."
        repair = client.calls[2].messages[-1]
        assert isinstance(repair, ToolResultsMessage)
        assert repair.results[0].is_error and repair.results[0].content["detail"] == ["9.9%"]

    def test_figures_still_unsupported_after_the_repair_are_removed(self, ctx: ToolContext) -> None:
        bad = give(
            lead="Revenue rose 9.9% [r1]. It reached ₹1.25 Cr [r1].",
            evidence=["Orders rose 41%", "Revenue ₹1.25 Cr"],
        )
        client = FakeLLM(
            [tools_turn(call("kpi_summary", metrics=["revenue", "orders"], period=Q3)), bad, bad]
        )
        outcome = run(client, ctx, question="How did revenue do?")
        assert outcome.answer is not None
        assert outcome.answer.lead == "It reached ₹1.25 Cr [r1]."
        assert outcome.answer.evidence == ["Revenue ₹1.25 Cr [r1]"]
        assert any(n.startswith("numbers_removed:") for n in outcome.notes)

    def test_the_last_step_asks_for_the_answer(self, ctx: ToolContext) -> None:
        many = [tools_turn(call("list_sources")) for _ in range(2)]
        client = FakeLLM([*many, give(lead="I could not finish; South looks weaker.", evidence=[])])
        outcome = run(client, ctx, max_steps=3)
        assert outcome.answer is not None and outcome.calls == 3
        last = client.calls[2]
        assert [t.name for t in last.tools] == ["give_answer"]
        assert isinstance(last.messages[-1], UserMessage) and "limit" in last.messages[-1].text

    def test_without_an_answer_the_step_limit_is_reported(self, ctx: ToolContext) -> None:
        client = FakeLLM([tools_turn(call("list_sources")) for _ in range(3)])
        outcome = run(client, ctx, max_steps=3)
        assert outcome.answer is None and outcome.error == "limit:steps"

    def test_the_token_budget_stops_the_question(self, ctx: ToolContext) -> None:
        heavy = FakeTurn(tool_calls=[call("list_sources")], usage=Usage(100_000, 10))
        client = FakeLLM([heavy, heavy, give(lead="x", evidence=[])])
        outcome = run(client, ctx, token_budget=150_000)
        assert outcome.error == "limit:tokens" and outcome.calls == 2
        assert client.remaining == 1

    def test_a_plain_text_answer_is_asked_once_for_the_structure_then_used(
        self, ctx: ToolContext
    ) -> None:
        client = FakeLLM(["South fell.", "South fell in Q3.\n- The other regions grew."])
        outcome = run(client, ctx)
        assert outcome.answer is not None
        assert outcome.answer.lead == "South fell in Q3." and outcome.answer.evidence == [
            "The other regions grew."
        ]

    def test_model_errors_end_the_question_with_their_kind(self, ctx: ToolContext) -> None:
        for kind in ("quota", "invalid_key", "rate_limited", "unavailable"):
            outcome = run(FakeLLM([LLMError(kind, "gemini")]), ctx)
            assert outcome.error == f"llm:{kind}" and outcome.answer is None

    def test_a_model_switch_mid_question_starts_again_once(self, ctx: ToolContext) -> None:
        class Switching(FakeLLM):
            """The provider layer moved to another model after the first call, which refuses the old turns."""

            def generate(self, *args: Any, **kwargs: Any) -> Any:
                if len(self.calls) == 1:
                    self._model = "gemini-other"
                return super().generate(*args, **kwargs)

        client = Switching(
            [
                tools_turn(call("list_sources")),
                LLMError("other", "gemini", "thought signature rejected"),
                give(lead="South fell.", evidence=[]),
            ]
        )
        outcome = run(client, ctx)
        assert outcome.answer is not None and "restarted" in outcome.notes
        assert len(client.calls[2].messages) == 1  # the plain question again

    def test_follow_ups_see_earlier_turns_as_text(self) -> None:
        history = loop.history_messages(
            [("user", "How did Q3 go?"), ("assistant", "Revenue rose 5.5%.")]
        )
        assert [type(m).__name__ for m in history] == ["UserMessage", "AssistantMessage"]
        assert loop.answer_text({"lead": "Up.", "evidence": ["a", "b"]}) == "Up.\n- a\n- b"


# ---------------------------------------------------------------------------------------- answer rules
class TestAnswerRules:
    def store(self) -> ResultStore:
        store = ResultStore()
        store.add(
            "x",
            {"impact": 450_000.0, "monthly": 4_000_000.0, "small": 20_000.0},
            tables=[],
            inputs={},
            sql=[],
            row_count=1,
        )
        return store

    def test_advice_was_asked(self) -> None:
        assert answers.asks_for_advice("What should we do about the South?")
        assert answers.asks_for_advice("How can I improve repeat orders?")
        assert not answers.asks_for_advice("Why did South revenue fall in Q3?")

    def test_a_recommendation_stays_when_asked(self) -> None:
        answer = answers.Answer(
            lead="x", recommendation=answers.Recommendation("Cut YouTube spend.")
        )
        assert (
            answers.impact_rule(
                answer, self.store(), asked=True, threshold_inr=1e5, threshold_pct=5
            )
            is None
        )
        assert answer.recommendation is not None

    def test_unasked_it_stays_only_when_material_and_supported(self) -> None:
        store = self.store()
        material = answers.Answer(
            lead="x", recommendation=answers.Recommendation("Act", impact_inr=450_000)
        )
        assert (
            answers.impact_rule(material, store, asked=False, threshold_inr=1e5, threshold_pct=5)
            is None
        )
        small = answers.Answer(
            lead="x",
            recommendation=answers.Recommendation("Act", impact_inr=20_000, basis_inr=4_000_000),
        )
        assert (
            answers.impact_rule(small, store, asked=False, threshold_inr=1e5, threshold_pct=5)
            == "recommendation_not_material"
        )
        assert small.recommendation is None
        invented = answers.Answer(
            lead="x", recommendation=answers.Recommendation("Act", impact_inr=999_999)
        )
        answers.impact_rule(invented, store, asked=False, threshold_inr=1e5, threshold_pct=5)
        assert invented.recommendation is None
        pct = answers.Answer(
            lead="x",
            recommendation=answers.Recommendation("Act", impact_inr=20_000, basis_inr=20_000),
        )
        assert (
            answers.impact_rule(pct, store, asked=False, threshold_inr=1e5, threshold_pct=5) is None
        )

    def test_parse_is_lenient_about_types_and_strict_about_lengths(self) -> None:
        parsed = answers.parse(
            {
                "lead": "x" * 5000,
                "evidence": ["a", "", 3, "b", "c", "d", "e", "f"],
                "recommendation": "Do it",
                "charts": ["c1", "drop table", "c2"],
                "confidence": "certain",
            }
        )
        assert len(parsed.lead) == answers.MAX_LEAD
        assert parsed.evidence == ["a", "3", "b", "c", "d"]
        assert parsed.recommendation is not None and parsed.recommendation.text == "Do it"
        assert parsed.charts == ["c1", "c2"] and parsed.confidence is None


class TestFigures:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("₹12.4 L", [1_240_000.0]),
            ("₹1.24 Cr", [12_400_000.0]),
            ("₹8,450", [8450.0]),
            ("₹34,04,299", [3_404_299.0]),
            ("17.7%", [17.7, 0.177]),
            ("0.8x", [0.8]),
            ("45 thousand", [45_000.0]),
        ],
    )
    def test_amounts_are_understood(self, text: str, expected: list[float]) -> None:
        found = figures(text)
        assert len(found) == 1 and list(found[0].candidates) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "text",
        [
            "In Q3 2026",
            "on 2026-08-15",
            "on 15 Aug",
            "the top 3 regions",
            "order #1004",
            "since FY2025",
            "result r12",
            "at 10:30",
        ],
    )
    def test_dates_years_counts_and_ids_are_not_figures(self, text: str) -> None:
        assert figures(text) == []


def test_the_system_prompt_has_the_lens_dictionary_rules_and_playbooks(ctx: ToolContext) -> None:
    from datetime import date

    text, chosen = prompts.system_prompt(
        access=ctx.access,
        visible=ctx.visible,
        lens="marketing",
        question="Why did ROAS drop on Instagram?",
        today=date(2026, 11, 12),
        confirmed=False,
        max_steps=12,
    )
    assert "senior business analyst for UrbanCart, a D2C e-commerce business" in text
    assert "Your lens: Marketing" in text and "attribution limits" in text
    assert (
        "table `shopify_orders`" in text
        and "`revenue` = SUM(total_price) - SUM(total_refunds)" in text
    )
    assert "not yet confirmed" in text and "₹1 lakh" in text and "Thursday" in text
    assert chosen == ["why_changed", "campaign_efficiency"]
    assert "Playbook: why did a metric change?" in text
    assert "$" not in text.replace("₹", "")  # every placeholder was filled


# ------------------------------------------------------------------------------------------------ what-if
class TestWhatIfTool:
    def test_is_offered_to_the_model_and_the_old_note_is_gone(self) -> None:
        assert "what_if" in SPECS
        schema = SPECS["what_if"].parameters
        assert schema["required"] == ["lever", "change"]
        assert schema["properties"]["lever"]["enum"] == ["price", "discount", "marketing_spend"]
        playbook = prompts.playbook_text("what_if")
        assert "not available yet" not in playbook and "what_if" in playbook

    @pytest.mark.parametrize(
        "question",
        [
            "What if we raise prices by 10%?",
            "What would happen if we cut YouTube spend?",
            "Should we increase the discount on Fashion?",
            "Simulate a 5% price cut",
        ],
    )
    def test_what_if_questions_get_the_what_if_playbook_first(self, question: str) -> None:
        assert prompts.choose_playbooks(question)[0] == "what_if"

    def test_a_result_carries_provenance_and_no_raw_rows(self, ctx: ToolContext) -> None:
        content = run_tool(
            ctx, "what_if", lever="price", change=10, scope={"product_type": "Electronics"}
        )
        assert content["result_id"] == "r1" and content["facts"]["delta_low"] is not None
        assert len(content["rows"]) == 3 and set(content["columns"]) >= {
            "bucket",
            "baseline",
            "scenario",
        }
        prov = ctx.store.provenance["r1"]
        assert prov.tool == "what_if" and [t["table"] for t in prov.tables] == ["shopify_orders"]
        assert prov.inputs["lever"] == "price" and prov.sql

    @pytest.mark.parametrize(
        ("args", "error"),
        [
            ({"lever": "stock", "change": 5}, "bad_lever"),
            ({"lever": "price", "change": "ten"}, "bad_change"),
            ({"lever": "price", "change": 500}, "bad_change"),
            ({"lever": "price", "change": 5, "horizon": 12}, "bad_horizon"),
            (
                {"lever": "price", "change": 5, "assumptions": {"price_elasticity": 2}},
                "bad_assumption",
            ),
            ({"lever": "price", "change": 5, "assumptions": {"x": "a"}}, "bad_assumption"),
            ({"lever": "marketing_spend", "change": 5}, "needs_channel"),
            (
                {"lever": "marketing_spend", "change": 5, "scope": {"channel": "Radio"}},
                "unknown_channel",
            ),
            ({"lever": "price", "change": 5, "metric": "nope"}, "unknown_metric"),
        ],
    )
    def test_unusable_requests_come_back_as_errors_the_model_can_act_on(
        self, ctx: ToolContext, args: dict[str, Any], error: str
    ) -> None:
        content = run_tool(ctx, "what_if", **args)
        assert content["error"] == error and content["hint"], content

    def test_a_table_the_person_cannot_see_cannot_be_simulated_or_used_as_scope(self) -> None:
        hidden = agent_context(hidden=("shopify_orders",))
        assert run_tool(hidden, "what_if", lever="price", change=5)["error"] == "needs_metric"
        named = run_tool(hidden, "what_if", lever="price", change=5, metric="revenue")
        assert named["error"] in ("unknown_metric", "metric_unavailable")
        assert not hidden.workspace.has("shopify_orders")

    def test_marketing_tables_that_are_hidden_leave_no_marketing_data(self) -> None:
        quarters = tuple(
            f"q{q}_{y}" for y in (2025, 2026) for q in (1, 2, 3, 4) if (y, q) != (2026, 4)
        )
        ctx = agent_context(hidden=quarters)
        content = run_tool(
            ctx, "what_if", lever="marketing_spend", change=10, scope={"channel": "Email"}
        )
        assert content["error"] == "no_marketing_data"
