"""The golden-question evaluation (BUILD_SPEC 12.2, X4).

``golden_questions.yaml`` lists questions about the UrbanCart demo company, each with the facts a correct answer must
state (numbers within a tolerance, named drivers, required notes) and the tool calls an ideal analyst would make
(``plan``). The five what-if questions come from ``whatif_scenarios.yaml`` (M9) by id.

Two ways to run the same pipeline (the agent loop, its tools over the demo tables, the number check, the scorer):

* **fake** (``make test``, CI): a stand-in model replays each question's ``plan`` and then answers with the figures
  the tools returned that the answer key expects. This checks the harness and the tool layer: if a tool stops
  producing an expected figure, the answer lacks it and the question fails. No model is called.
* **real** (``make eval``, opt-in): the configured model (Gemini or Claude, from ``.env``) answers on its own; the
  report in ``eval/reports/<date>.md`` scores it. Phase 1 sign-off needs at least 85% (BUILD_SPEC 12.2). Tokens and
  estimated cost are counted for every call and written into the report.

The demo tables are read from ``eval/fixtures`` through ``tests/analytics_data.py`` exactly as the file connector
reads them, with no database and no live sources.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.agent import answer as answers
from app.agent import loop, prompts, provenance
from app.agent.tools import ToolContext
from app.llm.base import LLMClient, LLMResponse, Message, ToolCall, ToolSpec, Usage

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden_questions.yaml"
SCENARIOS = HERE / "whatif_scenarios.yaml"
TODAY = date(
    2026, 10, 15
)  # two weeks after the demo data ends, so "last month" and "Q3" are complete
PASS_MARK = 85.0


@dataclass(frozen=True)
class Golden:
    id: str
    ask: str
    language: str
    lens: str
    finding: int | None
    plan: list[dict[str, Any]]
    expect: dict[str, Any]

    @property
    def what_if(self) -> bool:
        return any(step["tool"] == "what_if" for step in self.plan)


def load(path: Path = GOLDEN, scenarios: Path = SCENARIOS) -> list[Golden]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in yaml.safe_load(scenarios.read_text(encoding="utf-8"))["scenarios"]}
    out = []
    for item in raw["questions"]:
        if "scenario" in item:
            scenario = by_id[item["scenario"]]
            low, high = scenario["expect"]["delta_pct_expected"]
            item = {
                "id": item["id"],
                "ask": scenario["ask"],
                "finding": None,
                "plan": [{"tool": "what_if", "args": scenario["args"]}],
                "expect": {
                    "pct_between": [low, high],
                    "names": [
                        ["range", "between", " to "],
                        ["assum", "typical", "history", "past"],
                    ],
                    **item.get("expect", {}),
                },
                **{k: v for k, v in item.items() if k not in ("id", "scenario", "expect")},
            }
        out.append(
            Golden(
                id=item["id"],
                ask=item["ask"],
                language=item.get("language", "en"),
                lens=item.get("lens", "business"),
                finding=item.get("finding"),
                plan=list(item.get("plan") or []),
                expect=dict(item.get("expect") or {}),
            )
        )
    return out


# ------------------------------------------------------------------------------------------------ scoring
@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Score:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def answer_text(answer: dict[str, Any]) -> str:
    rec = answer.get("recommendation") or {}
    parts = [
        str(answer.get("lead") or ""),
        *[str(e) for e in answer.get("evidence") or []],
        str(answer.get("why_it_matters") or ""),
        str(rec.get("text") or "") if isinstance(rec, dict) else "",
        *[str(n) for n in answer.get("data_notes") or []],
    ]
    return "\n".join(p for p in parts if p)


def _values(text: str) -> list[float]:
    return [abs(c) for f in provenance.figures(text) for c in f.candidates]


def _within(value: float, expected: float, tolerance: float, relative: float) -> bool:
    return abs(value - abs(expected)) <= max(tolerance, abs(expected) * relative)


def score(golden: Golden, answer: dict[str, Any] | None) -> Score:
    result = Score()
    if not answer:
        result.checks.append(Check("answered", False, "no answer"))
        return result
    text = answer_text(answer)
    values = _values(text)
    lowered = text.lower()
    for number in golden.expect.get("numbers") or []:
        expected = float(number["value"])
        tolerance = float(number.get("tolerance", 0.5))
        relative = float(number.get("relative", 0.0))
        ok = any(_within(v, expected, tolerance, relative) for v in values)
        result.checks.append(
            Check(f"number {number.get('label', expected)}", ok, f"expected {expected}")
        )
    for group in golden.expect.get("names") or []:
        words = [group] if isinstance(group, str) else list(group)
        ok = any(w.lower() in lowered for w in words)
        result.checks.append(Check(f"names {'/'.join(words)}", ok))
    if "pct_between" in golden.expect:
        low, high = (float(x) for x in golden.expect["pct_between"])
        lo, hi = (
            (0.0, max(abs(low), abs(high))) if low < 0 < high else sorted((abs(low), abs(high)))
        )
        percents = [
            abs(f.candidates[0]) for f in provenance.figures(text) if len(f.candidates) == 2
        ]
        ok = any(lo - 0.5 <= p <= hi + 0.5 for p in percents)
        result.checks.append(Check(f"change between {low}% and {high}%", ok))
    codes = {str(n.get("code")) for n in answer.get("notes") or []}
    for code in golden.expect.get("notes") or []:
        result.checks.append(Check(f"note {code}", code in codes))
    return result


# ------------------------------------------------------------------------------------------- the fake model
class ReplayLLM:
    """A stand-in model: first the golden ``plan`` as tool calls, then an answer with the expected figures that
    the tool results support (each cited), written from the results only."""

    provider = "fake"

    def __init__(self, golden: Golden, ctx: ToolContext) -> None:
        self.golden = golden
        self.ctx = ctx
        self.calls = 0

    @property
    def model(self) -> str:
        return "replay"

    def list_models(self) -> list[str]:
        return ["replay"]

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 3000,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1 and self.golden.plan:
            calls = [
                ToolCall(f"g{i}", step["tool"], dict(step.get("args") or {}))
                for i, step in enumerate(self.golden.plan)
            ]
            return LLMResponse([], calls, "tool_use", Usage(0, 0), "fake", "replay", {})
        return LLMResponse(
            [],
            [ToolCall("ga", "give_answer", self._answer())],
            "tool_use",
            Usage(0, 0),
            "fake",
            "replay",
            {},
        )

    def _answer(self) -> dict[str, Any]:
        store = self.ctx.store
        lines: list[str] = []
        for number in self.golden.expect.get("numbers") or []:
            expected = float(number["value"])
            tolerance = float(number.get("tolerance", 0.5))
            relative = float(number.get("relative", 0.0))
            for rid, values in store.values.items():
                found = next(
                    (v for v in values if _within(abs(v), expected, tolerance, relative)),
                    None,
                )
                if found is not None:
                    lines.append(
                        f"{number.get('label', 'value')}: {_written(found, number)} [{rid}]"
                    )
                    break
        if "pct_between" in self.golden.expect:
            for rid, data in store.data.items():
                facts = data.get("facts") or {}
                pct = facts.get("delta_pct_expected")
                if isinstance(pct, int | float):
                    lines.append(
                        f"Expected change {pct:.1f}% [{rid}], in a range between {facts.get('delta_pct_low', 0):.1f}% "
                        f"[{rid}] and {facts.get('delta_pct_high', 0):.1f}% [{rid}], with the assumptions listed."
                    )
        blob = json.dumps(store.data, default=str).lower()
        words = [
            next((w for w in ([g] if isinstance(g, str) else g) if w.lower().strip() in blob), "")
            for g in self.golden.expect.get("names") or []
        ]
        named = ", ".join(w.strip() for w in words if w.strip())
        lead = lines[0] if lines else "The data does not show this."
        return {
            "lead": lead + (f" ({named})" if named else ""),
            "evidence": lines[1:5],
            "data_notes": [],
            "confidence": "high",
        }


def _written(value: float, number: dict[str, Any]) -> str:
    unit = number.get("unit")
    if unit == "pct":
        shown = value * 100 if abs(value) < 1 and abs(float(number["value"])) >= 1 else value
        return f"{shown:.1f}%"
    if unit == "x":
        return f"{value:.2f}x"
    return f"{value:,.2f}"


# --------------------------------------------------------------------------------------------------- running
@dataclass
class Outcome:
    golden: Golden
    answer: dict[str, Any] | None
    score: Score
    usage: Usage
    calls: int
    seconds: float
    error: str | None = None
    tools: list[str] = field(default_factory=list)


ContextFactory = Callable[[], ToolContext]
ClientFactory = Callable[[Golden, ToolContext], LLMClient]


def run_one(
    golden: Golden, make_context: ContextFactory, make_client: ClientFactory, *, max_steps: int = 12
) -> Outcome:
    ctx = make_context()
    ctx.today = TODAY
    client = make_client(golden, ctx)
    started = time.monotonic()
    system, _ = prompts.system_prompt(
        access=ctx.access,
        visible=ctx.visible,
        lens=golden.lens,
        question=golden.ask,
        today=TODAY,
        confirmed=True,
        max_steps=max_steps,
    )
    try:
        outcome = loop.run(
            client,
            ctx,
            system=system,
            history=[],
            question=golden.ask,
            limits=loop.Limits(max_steps=max_steps),
        )
    finally:
        ctx.workspace.close()
    answer = None
    if outcome.answer is not None:
        notes = [{"code": n.partition(":")[0]} for n in ctx.notes]
        answer = answers.as_json(outcome.answer, notes=notes, charts=[])
    return Outcome(
        golden=golden,
        answer=answer,
        score=score(golden, answer),
        usage=outcome.usage,
        calls=outcome.calls,
        seconds=time.monotonic() - started,
        error=outcome.error,
        tools=[t["tool"] for t in ctx.trace],
    )


def run_all(
    goldens: Sequence[Golden],
    make_context: ContextFactory,
    make_client: ClientFactory,
    *,
    max_steps: int = 12,
) -> list[Outcome]:
    return [run_one(g, make_context, make_client, max_steps=max_steps) for g in goldens]


def pass_rate(outcomes: Sequence[Outcome]) -> float:
    return 100.0 * sum(o.score.passed for o in outcomes) / len(outcomes) if outcomes else 0.0


def report(
    outcomes: Sequence[Outcome], *, mode: str, model: str, cost_usd: float | None, when: str
) -> str:
    rate = pass_rate(outcomes)
    tokens_in = sum(o.usage.input_tokens for o in outcomes)
    tokens_out = sum(o.usage.output_tokens for o in outcomes)
    lines = [
        f"# Evaluation report, {when}",
        "",
        f"- Mode: {mode}; model: {model}",
        f"- Passed: {sum(o.score.passed for o in outcomes)} of {len(outcomes)} ({rate:.1f}%); "
        f"Phase 1 needs {PASS_MARK:.0f}% on a real model: **{'met' if rate >= PASS_MARK else 'not met'}**",
        f"- AI calls: {sum(o.calls for o in outcomes)}; tokens in {tokens_in:,}, out {tokens_out:,}"
        + (f"; estimated cost ${cost_usd:.4f}" if cost_usd is not None else ""),
        "- Answers are in English for now (DECISIONS D41): Hindi and Kannada questions are scored on their facts.",
        "",
        "| Question | Lang | Result | Failed checks | Tools | Calls |",
        "|---|---|---|---|---|---:|",
    ]
    for o in outcomes:
        failed = "; ".join(c.name for c in o.score.checks if not c.ok) or (o.error or "")
        lines.append(
            f"| {o.golden.id} | {o.golden.language} | {'pass' if o.score.passed else 'FAIL'} | {failed} | "
            f"{', '.join(dict.fromkeys(o.tools))} | {o.calls} |"
        )
    lines += ["", "## Answers", ""]
    for o in outcomes:
        lines += [f"### {o.golden.id}", "", f"> {o.golden.ask}", ""]
        lines.append(answer_text(o.answer) if o.answer else f"_No answer ({o.error})_")
        lines.append("")
    return "\n".join(lines)
