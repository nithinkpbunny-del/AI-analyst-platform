"""The golden-question harness with the stand-in model (BUILD_SPEC 12.2: "CI runs the same pipeline with the fake
LLM to check the harness and tool layer"). No model is called: ``make eval`` runs the same questions on a real one."""

from __future__ import annotations

from typing import Any

import harness
import pytest

from tests.analytics_data import agent_context

GOLDENS = harness.load()


def test_the_question_set_meets_the_spec() -> None:
    assert len(GOLDENS) >= 40
    assert len({g.id for g in GOLDENS}) == len(GOLDENS)
    assert sum(g.language in ("hi", "kn") for g in GOLDENS) >= 6
    assert sum(g.what_if for g in GOLDENS) == 5  # the five what-ifs of whatif_scenarios.yaml (D75)
    assert {g.finding for g in GOLDENS} >= {
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
    }  # every planted finding is asked about
    for golden in GOLDENS:
        assert golden.plan, golden.id
        assert golden.expect.get("numbers") or "pct_between" in golden.expect, golden.id


@pytest.fixture(scope="module")
def outcomes() -> dict[str, harness.Outcome]:
    results = harness.run_all(GOLDENS, agent_context, harness.ReplayLLM)
    return {o.golden.id: o for o in results}


@pytest.mark.parametrize("golden", GOLDENS, ids=[g.id for g in GOLDENS])
def test_the_tool_layer_supports_every_expected_fact(
    golden: harness.Golden, outcomes: dict[str, Any]
) -> None:
    outcome = outcomes[golden.id]
    failed = [(c.name, c.detail) for c in outcome.score.checks if not c.ok]
    assert outcome.score.passed, (failed, outcome.error, outcome.answer)
    assert outcome.tools[: len(golden.plan)] == [s["tool"] for s in golden.plan]


def test_the_report(outcomes: dict[str, harness.Outcome]) -> None:
    text = harness.report(
        list(outcomes.values()), mode="fake", model="replay", cost_usd=None, when="2026-11-13"
    )
    assert "Passed: 48 of 48 (100.0%)" in text or "(100.0%)" in text
    assert "| south_q3_why | en | pass |" in text


class TestScorer:
    golden = harness.Golden(
        id="t",
        ask="Why did South fall?",
        language="en",
        lens="business",
        finding=1,
        plan=[],
        expect={
            "numbers": [{"value": -17.7, "tolerance": 0.5}, {"value": 2801265, "relative": 0.01}],
            "names": [["South"], ["repeat", "existing"]],
        },
    )

    def test_a_good_answer_passes(self) -> None:
        answer = {
            "lead": "South revenue fell 17.7% to ₹28.0 L in Q3.",
            "evidence": ["Existing customers ordered less often."],
        }
        assert harness.score(self.golden, answer).passed

    def test_a_wrong_number_fails(self) -> None:
        answer = {
            "lead": "South revenue fell 12% to ₹28.0 L.",
            "evidence": ["Repeat customers left."],
        }
        score = harness.score(self.golden, answer)
        assert not score.passed and [c.name for c in score.checks if not c.ok] == ["number -17.7"]

    def test_a_missing_driver_fails(self) -> None:
        answer = {"lead": "South revenue fell 17.7% to ₹28.0 L.", "evidence": []}
        assert not harness.score(self.golden, answer).passed

    def test_no_answer_fails(self) -> None:
        assert not harness.score(self.golden, None).passed

    def test_pct_between(self) -> None:
        golden = harness.Golden("w", "x", "en", "business", None, [], {"pct_between": [-40, -10]})
        assert harness.score(
            golden, {"lead": "Revenue would fall 22.5% (range 15% to 30%)."}
        ).passed
        assert not harness.score(golden, {"lead": "Revenue would fall 2.5%."}).passed
