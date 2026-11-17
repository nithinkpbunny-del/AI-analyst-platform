"""The agent loop for one question (BUILD_SPEC 7.1, 6.5).

    history (last turns as plain text) + question
      -> model call with the tools -> run the tool calls -> results back -> ... -> give_answer
      -> number check -> (one repair turn) -> answer

Limits (6.5): at most ``max_steps`` model calls; the last one offers only ``give_answer`` and asks for the answer
with what was found, so a long question still ends with an answer. ``token_budget`` input tokens per question:
when spent, the loop stops and says so (``limit:tokens``). Each model call is metered by the caller's
``on_response`` (one ``usage_events`` row per call, D42).

Model fallback (M5 notes): the Gemini layer may switch models between calls. A different model can refuse the
first model's signed turns; when that happens mid-question the question starts again on the new model, once, from
the plain question (results already computed stay available).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.agent import answer as answers
from app.agent.tools import ToolContext, execute, specs
from app.llm.base import (
    AssistantMessage,
    LLMClient,
    LLMError,
    LLMResponse,
    Message,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
)

logger = logging.getLogger(__name__)

FINAL_NUDGE = (
    "You have reached the limit of steps for one question. Call give_answer now with what you found; say in "
    "data_notes what you could not check."
)
CUT_OFF_NUDGE = "Your reply was cut off. Call give_answer now, briefly."
TEXT_NUDGE = "Call give_answer with your answer."


@dataclass
class LoopOutcome:
    answer: answers.Answer | None
    error: str | None = None  # limit:steps | limit:tokens | llm:<kind> | no_answer | cancelled
    notes: list[str] = field(
        default_factory=list
    )  # code notes: numbers_removed:<n>, restarted, ...
    usage: Usage = field(default_factory=Usage)
    calls: int = 0
    models: list[str] = field(default_factory=list)
    provider: str | None = None
    repaired: bool = False


@dataclass
class Limits:
    max_steps: int = 12
    token_budget: int = 150_000
    max_output_tokens: int = 3000


def _tool_specs(final: bool, ctx: ToolContext) -> list[ToolSpec]:
    return [answers.ANSWER_TOOL] if final else [*specs(ctx), answers.ANSWER_TOOL]


def run(
    client: LLMClient,
    ctx: ToolContext,
    *,
    system: str,
    history: Sequence[Message],
    question: str,
    limits: Limits,
    on_response: Callable[[LLMResponse], None] = lambda _r: None,
    should_stop: Callable[[], bool] = lambda: False,
) -> LoopOutcome:
    start: list[Message] = [*history, UserMessage(question)]
    messages: list[Message] = list(start)
    outcome = LoopOutcome(answer=None)
    restarted = False
    nudged_cut = False
    nudged_text = False

    while outcome.calls < limits.max_steps:
        if should_stop():
            # A long task the person cancelled: stop before the next model call (nothing more is spent).
            outcome.error = "cancelled"
            return outcome
        if outcome.usage.input_tokens >= limits.token_budget:
            outcome.error = "limit:tokens"
            return outcome
        final = outcome.calls == limits.max_steps - 1
        if final and outcome.calls > 0:
            messages.append(UserMessage(FINAL_NUDGE))
        ctx.progress({"code": "thinking"} if outcome.calls == 0 else {"code": "working"})
        try:
            response = client.generate(
                system, messages, _tool_specs(final, ctx), limits.max_output_tokens
            )
        except LLMError as error:
            switched = outcome.models and client.model not in outcome.models
            if error.kind == "other" and switched and not restarted and len(messages) > len(start):
                logger.info("agent: model changed to %s mid-question; starting again", client.model)
                restarted = True
                outcome.notes.append("restarted")
                messages = list(start)
                continue
            outcome.error = f"llm:{error.kind}"
            return outcome
        outcome.calls += 1
        outcome.usage = outcome.usage + response.usage
        outcome.provider = response.provider
        if response.model not in outcome.models:
            outcome.models.append(response.model)
        on_response(response)
        messages.append(response.as_message())

        give = next((c for c in response.tool_calls if c.name == answers.ANSWER_TOOL.name), None)
        others = [c for c in response.tool_calls if c.name != answers.ANSWER_TOOL.name]
        if give is not None:
            ctx.progress({"code": "checking_numbers"})
            parsed = answers.parse(give.input or {})
            problem = _problem(parsed, ctx, outcome)
            if problem is None:
                outcome.answer = parsed
                return outcome
            if outcome.repaired or outcome.calls >= limits.max_steps:
                outcome.answer = _strip(parsed, problem, outcome)
                return outcome
            outcome.repaired = True
            results = [execute(ctx, c) for c in others]
            results.append(ToolResult(give.id, problem, is_error=True))
            messages.append(ToolResultsMessage(results))
            continue
        if response.tool_calls:
            ctx.progress({"code": "working"})
            messages.append(ToolResultsMessage([execute(ctx, c) for c in response.tool_calls]))
            continue
        if response.stop_reason == "max_tokens" and not nudged_cut:
            nudged_cut = True
            messages.append(UserMessage(CUT_OFF_NUDGE))
            continue
        if response.text.strip():
            if not nudged_text and outcome.calls < limits.max_steps:
                # A plain-text answer: ask once for the structured one; if it comes as text again, use it.
                nudged_text = True
                messages.append(UserMessage(TEXT_NUDGE))
                continue
            parsed = answers.from_plain_text(response.text)
            problem = _problem(parsed, ctx, outcome)
            outcome.answer = parsed if problem is None else _strip(parsed, problem, outcome)
            return outcome
        outcome.error = "no_answer"
        return outcome
    outcome.error = "limit:steps"
    return outcome


def _problem(
    parsed: answers.Answer, ctx: ToolContext, outcome: LoopOutcome
) -> dict[str, Any] | None:
    """What is wrong with an answer, as the error the model sees in its repair turn; ``None`` when it is fine."""
    if not parsed.lead:
        return {"error": "lead_required", "hint": "Write the answer in 'lead' (1-2 sentences)."}
    report = answers.number_check(parsed, ctx.store)
    unknown_charts = [c for c in parsed.charts if c not in ctx.charts]
    parsed.charts = [c for c in parsed.charts if c in ctx.charts]
    if not report.unsupported:
        if unknown_charts:
            outcome.notes.append("unknown_charts_dropped")
        return None
    return {
        "error": "unsupported_numbers",
        "detail": report.unsupported[:20],
        "unknown_result_ids": sorted(set(report.unknown_citations))[:10],
        "hint": (
            "These figures are not in any tool result. Use only figures from tool results and cite each one "
            "with its result id, e.g. '₹28.0 L [r3]'. Get a missing figure with a tool first if you need it, "
            "or leave it out. Then call give_answer again."
        ),
    }


def _strip(parsed: answers.Answer, problem: dict[str, Any], outcome: LoopOutcome) -> answers.Answer:
    unsupported = [str(u) for u in problem.get("detail", [])]
    removed = answers.remove_unsupported(parsed, unsupported)
    if removed:
        outcome.notes.append(f"numbers_removed:{removed}")
    return parsed


def history_messages(turns: Sequence[tuple[str, str]]) -> list[Message]:
    """Earlier turns of the conversation as plain text (questions and the answers' text), oldest first. Tool calls
    are not replayed: the new question fetches fresh data."""
    out: list[Message] = []
    for role, text in turns:
        if not text.strip():
            continue
        out.append(UserMessage(text) if role == "user" else AssistantMessage(text_blocks=[text]))
    return out


def answer_text(answer_json: dict[str, Any]) -> str:
    """An earlier answer as the text a follow-up question sees."""
    parts = [str(answer_json.get("lead") or "")]
    parts += [f"- {e}" for e in answer_json.get("evidence") or []]
    return "\n".join(p for p in parts if p)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
