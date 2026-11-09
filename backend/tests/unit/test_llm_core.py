"""Provider-neutral parts of the LLM layer: retries, history portability, model choice, prices and the fake."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.llm.base import (
    AssistantMessage,
    LLMError,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    Usage,
    UserMessage,
    call_with_retries,
    portable_history,
)
from app.llm.fake import FakeLLM, FakeLLMFactory, FakeTurn
from app.llm.models import ModelCatalog, choose_model
from app.llm.pricing import estimate_cost_usd, price_for


class TestRetries:
    def _flaky(self, errors: list[LLMError]) -> tuple[list[int], object]:
        calls: list[int] = []

        def call() -> str:
            calls.append(1)
            if errors:
                raise errors.pop(0)
            return "ok"

        return calls, call

    def test_retries_rate_limits_and_outages_with_the_spec_delays(self) -> None:
        sleeps: list[float] = []
        calls, call = self._flaky(
            [
                LLMError("rate_limited", "x"),
                LLMError("unavailable", "x"),
                LLMError("unavailable", "x"),
            ]
        )
        assert call_with_retries(call, sleep=sleeps.append) == "ok"  # type: ignore[arg-type]
        assert sleeps == [5.0, 15.0, 30.0]
        assert len(calls) == 4

    def test_honours_the_providers_retry_delay_up_to_a_minute(self) -> None:
        sleeps: list[float] = []
        _, call = self._flaky(
            [
                LLMError("rate_limited", "x", retry_after=7),
                LLMError("rate_limited", "x", retry_after=600),
            ]
        )
        call_with_retries(call, sleep=sleeps.append)  # type: ignore[arg-type]
        assert sleeps == [7.0, 60.0]

    @pytest.mark.parametrize("kind", ["quota", "invalid_key", "model_not_found", "other"])
    def test_never_retries_what_waiting_cannot_fix(self, kind: str) -> None:
        sleeps: list[float] = []
        calls, call = self._flaky([LLMError(kind, "x")])  # type: ignore[arg-type]
        with pytest.raises(LLMError):
            call_with_retries(call, sleep=sleeps.append)  # type: ignore[arg-type]
        assert sleeps == []
        assert len(calls) == 1


class TestHistory:
    def test_own_turns_are_untouched(self) -> None:
        turn = AssistantMessage(["hi"], provider="gemini", raw="native")
        assert portable_history([UserMessage("q"), turn], "gemini") == [UserMessage("q"), turn]

    def test_foreign_tool_turns_become_text(self) -> None:
        history = portable_history(
            [
                UserMessage("q"),
                AssistantMessage(
                    ["Looking."], [ToolCall("c1", "trend", {"metric": "orders"})], provider="claude"
                ),
                ToolResultsMessage([ToolResult("c1", {"rows": 3}, is_error=True)]),
            ],
            "gemini",
        )
        assert history[1] == AssistantMessage(
            ['Looking.\n[Called tool trend with {"metric": "orders"}]'], provider=None
        )
        assert history[2] == UserMessage('[Result of trend (error): {"rows": 3}]')

    def test_usage_adds_up(self) -> None:
        assert Usage(10, 2) + Usage(5, 1) == Usage(15, 3)


class TestModelChoice:
    def test_the_preferred_model_wins_when_offered(self) -> None:
        assert (
            choose_model("gemini", ["models/gemini-2.5-flash", "gemini-3-pro"], "gemini-2.5-flash")
            == "gemini-2.5-flash"
        )

    def test_otherwise_the_newest_stable_model_of_the_preferred_family(self) -> None:
        offered = [
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash-image",
            "text-embedding-004",
        ]
        assert choose_model("gemini", offered, "gemini-1.5-flash") == "gemini-2.5-flash"

    def test_preview_models_only_when_nothing_stable_exists(self) -> None:
        assert (
            choose_model("gemini", ["gemini-3-flash-preview", "gemini-3-pro-preview"], None)
            == "gemini-3-flash-preview"
        )

    def test_claude_prefers_opus_then_sonnet_then_haiku(self) -> None:
        assert (
            choose_model("claude", ["claude-haiku-5-5", "claude-sonnet-5-5"], "claude-opus-5-5")
            == "claude-sonnet-5-5"
        )
        assert (
            choose_model("claude", ["claude-opus-4-8", "claude-opus-5-5", "claude-haiku-5-5"], None)
            == "claude-opus-5-5"
        )

    def test_no_usable_model(self) -> None:
        with pytest.raises(LLMError) as caught:
            choose_model("gemini", ["text-embedding-004", "imagen-4"], None)
        assert caught.value.kind == "model_not_found"

    def test_the_catalog_caches_per_key_and_expires(self) -> None:
        now = [0.0]
        catalog = ModelCatalog(ttl_seconds=60, clock=lambda: now[0])
        loads: list[str] = []

        def load() -> list[str]:
            loads.append("x")
            return ["m"]

        catalog.get("gemini", "key-1", load)
        catalog.get("gemini", "key-1", load)
        catalog.get("gemini", "key-2", load)
        assert len(loads) == 2
        now[0] = 61
        catalog.get("gemini", "key-1", load)
        assert len(loads) == 3
        assert "key-1" not in str(catalog._entries)  # keys are stored hashed

    def test_refused_models_are_left_out_for_a_while(self) -> None:
        now = [0.0]
        catalog = ModelCatalog(ttl_seconds=60, clock=lambda: now[0])
        catalog.exclude("gemini", "k", "gemini-2.5-flash")
        assert catalog.get("gemini", "k", lambda: ["gemini-2.5-flash", "gemini-3.8-flash"]) == [
            "gemini-3.8-flash"
        ]
        assert catalog.get("gemini", "other-key", lambda: ["gemini-2.5-flash"]) == [
            "gemini-2.5-flash"
        ]
        now[0] = 61
        assert "gemini-2.5-flash" in catalog.get("gemini", "k", lambda: ["gemini-2.5-flash"])


class TestPricing:
    def test_the_longest_prefix_wins(self) -> None:
        assert price_for("gemini-2.5-flash-lite-preview-09-2025") == (
            Decimal("0.10"),
            Decimal("0.40"),
        )
        assert price_for("models/gemini-2.5-flash") == (Decimal("0.30"), Decimal("2.50"))
        assert price_for("claude-opus-5-5") == (Decimal("4.00"), Decimal("20.00"))
        assert price_for("claude-opus-5") == (Decimal("5.00"), Decimal("25.00"))

    def test_cost_estimate(self) -> None:
        # 10,000 in x $0.30/M + 2,000 out x $2.50/M = $0.003 + $0.005
        assert estimate_cost_usd("gemini-2.5-flash", Usage(10_000, 2_000)) == Decimal("0.008000")

    def test_unknown_models_have_no_estimate(self) -> None:
        assert estimate_cost_usd("some-new-model", Usage(1000, 1000)) is None


class TestFakeLLM:
    def test_plays_its_script_and_records_calls(self) -> None:
        llm = FakeLLM(
            [
                FakeTurn(tool_calls=[ToolCall("c1", "list_sources", {})]),
                "All done.",
                LLMError("quota", "gemini"),
            ]
        )
        first = llm.generate("system", [UserMessage("q")])
        assert first.stop_reason == "tool_use"
        second = llm.generate("system", [UserMessage("q"), first.as_message()])
        assert second.text == "All done."
        assert second.stop_reason == "end_turn"
        with pytest.raises(LLMError):
            llm.generate("system", [])
        assert [c.system for c in llm.calls] == ["system"] * 3
        assert llm.calls[1].messages[1].tool_calls[0].name == "list_sources"  # type: ignore[union-attr]
        assert llm.remaining == 0

    def test_fails_loudly_when_the_code_makes_more_calls_than_scripted(self) -> None:
        with pytest.raises(AssertionError, match="exhausted"):
            FakeLLM([]).generate("", [])

    def test_the_factory_rejects_chosen_keys(self) -> None:
        factory = FakeLLMFactory(rejected_keys=["bad"])
        assert factory("gemini", "good").list_models() == ["gemini-2.5-flash"]
        with pytest.raises(LLMError) as caught:
            factory("claude", "bad").list_models()
        assert caught.value.kind == "invalid_key"
        assert factory.created == [("gemini", "good"), ("claude", "bad")]


def test_retries_stop_at_the_deadline() -> None:
    now = [0.0]
    errors = [LLMError("unavailable", "x") for _ in range(3)]

    def call() -> str:
        if errors:
            raise errors.pop(0)
        return "ok"

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(LLMError):
        call_with_retries(call, sleep=sleep, deadline=10.0, clock=lambda: now[0])
    assert now[0] == 5.0  # the 15 s wait would pass the deadline


class TestDatedPrices:
    def test_announced_price_changes_apply_from_their_date(self) -> None:
        from datetime import date

        assert price_for("gemini-3.8-flash", date(2026, 12, 31)) == (
            Decimal("0.75"),
            Decimal("3.75"),
        )
        assert price_for("gemini-3.8-flash", date(2027, 1, 1)) == (Decimal("1.50"), Decimal("7.50"))
        assert price_for("gemini-4-argon", date(2026, 11, 30)) == (
            Decimal("2.00"),
            Decimal("10.00"),
        )
        assert price_for("gemini-4-argon", date(2026, 12, 1)) == (Decimal("4.00"), Decimal("20.00"))

    def test_the_live_check_of_2026_11_09(self) -> None:
        from datetime import date

        # 26 in x $0.75/M + 107 out x $3.75/M
        cost = estimate_cost_usd("gemini-3.6-flash", Usage(26, 107), date(2026, 11, 9))
        assert cost == Decimal("0.000421")
        assert (
            estimate_cost_usd("gemini-3.7-flash", Usage(26, 107)) is None
        )  # not on the price list
