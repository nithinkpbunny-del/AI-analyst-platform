"""The golden what-if scenarios (``eval/whatif_scenarios.yaml``, BUILD_SPEC 12.2, M9) on the UrbanCart demo data.

Each scenario runs twice: through the engine directly, and through the agent's ``what_if`` tool with the fake model
writing the answer (the number check must accept every figure it cites). Ranges, assumptions and a confidence must
always be shown. No model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agent import loop
from app.llm.base import ToolCall
from app.llm.fake import FakeLLM, FakeTurn
from tests.analytics_data import agent_context

FILE = Path(__file__).resolve().parents[3] / "eval" / "whatif_scenarios.yaml"
SCENARIOS: list[dict[str, Any]] = yaml.safe_load(FILE.read_text(encoding="utf-8"))["scenarios"]


def test_there_are_five_scenarios() -> None:
    assert len(SCENARIOS) == 5 and len({s["id"] for s in SCENARIOS}) == 5


@pytest.fixture(scope="module")
def results() -> dict[str, dict[str, Any]]:
    from app.agent.tools import execute

    out: dict[str, dict[str, Any]] = {}
    for scenario in SCENARIOS:
        ctx = agent_context()
        result = execute(ctx, ToolCall("c", "what_if", scenario["args"]))
        assert not result.is_error, (scenario["id"], result.content)
        out[scenario["id"]] = result.content
    return out


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
class TestScenario:
    def test_meets_the_expectations(
        self, scenario: dict[str, Any], results: dict[str, Any]
    ) -> None:
        facts = results[scenario["id"]]["facts"]
        expect = scenario["expect"]
        low, high = expect["delta_pct_expected"]
        assert low <= facts["delta_pct_expected"] <= high, facts["delta_pct_expected"]
        assert facts["confidence"]["level"] in expect["confidence_in"], facts["confidence"]
        if "assumption_source" in expect:
            main = facts["assumptions"][0]
            assert main["source"] == expect["assumption_source"], main
        if expect.get("range_contains_expected"):
            assert facts["delta_low"] <= facts["delta_expected"] <= facts["delta_high"]
        if expect.get("range_straddles_zero"):
            assert facts["delta_low"] < 0 < facts["delta_high"]
        if expect.get("net_after_spend_positive"):
            assert facts["net_after_spend"] > 0
        if "side_effect" in expect:
            assert expect["side_effect"] in {e["code"] for e in facts["side_effects"]}

    def test_always_shows_range_assumptions_and_confidence(
        self, scenario: dict[str, Any], results: dict[str, Any]
    ) -> None:
        content = results[scenario["id"]]
        facts = content["facts"]
        assert facts["range"] == "80%"
        assert {"delta_low", "delta_expected", "delta_high"} <= facts.keys()
        assert facts["assumptions"] and facts["assumptions"][0]["editable"] is True
        assert {"level", "reason"} <= facts["confidence"].keys()
        assert "what_if_assumptions" in content["notes"]
        assert len(content["rows"]) == scenario["args"]["horizon"]

    def test_the_agent_can_answer_from_it_and_the_number_check_accepts_it(
        self, scenario: dict[str, Any], results: dict[str, Any]
    ) -> None:
        # The engine is deterministic, so the figure the answer cites is the one the tool will return again.
        pct = results[scenario["id"]]["facts"]["delta_pct_expected"]
        shown = f"{abs(pct):.1f}%"
        lead = f"The expected change is {'down' if pct < 0 else 'up'} {shown} [r1]."
        client = FakeLLM(
            [
                FakeTurn(tool_calls=[ToolCall("c1", "what_if", scenario["args"])]),
                FakeTurn(
                    tool_calls=[
                        ToolCall(
                            "c2",
                            "give_answer",
                            {
                                "lead": lead,
                                "key_figure": shown,
                                "evidence": [
                                    "The range and the assumptions are in the result [r1]"
                                ],
                                "next_questions": ["What if we change it by half as much?"],
                            },
                        )
                    ]
                ),
            ]
        )
        ctx = agent_context()
        outcome = loop.run(
            client,
            ctx,
            system="system",
            history=[],
            question=scenario["ask"],
            limits=loop.Limits(),
        )
        assert outcome.error is None, outcome.error
        assert outcome.answer is not None and outcome.answer.lead == lead
        assert not outcome.repaired and not any(
            n.startswith("numbers_removed") for n in outcome.notes
        )
        assert ctx.store.provenance["r1"].tool == "what_if"
