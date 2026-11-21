"""The load test's own pieces (M19): it can only ever talk to the fake model, and its maths is right."""

from __future__ import annotations

import pytest

from app.llm.base import ToolResult, ToolResultsMessage, ToolSpec, UserMessage
from loadtest import run


def _tools(*names: str) -> list[ToolSpec]:
    return [ToolSpec(name, "", {"type": "object"}) for name in names]


def test_the_factory_only_builds_fake_clients_for_the_fake_key() -> None:
    factory = run.FakeOnlyFactory(delay=0)
    assert isinstance(factory("gemini", run.FAKE_KEY), run.PacedFakeLLM)
    with pytest.raises(RuntimeError, match="fake"):
        factory("gemini", "AIza-a-real-looking-key")


def test_settings_are_built_in_code_with_the_fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "a-real-key-from-the-shell")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "another-real-key")
    settings = run.settings_for("postgresql+psycopg://x/y", "redis://z/0", run.parse([]))
    assert settings.gemini_api_key.get_secret_value() == run.FAKE_KEY
    assert settings.anthropic_api_key.get_secret_value() == ""
    assert settings.database_url == "postgresql+psycopg://x/y"


def test_the_fake_model_asks_for_the_figure_then_answers_with_it() -> None:
    llm = run.PacedFakeLLM("gemini", 0, run.FakeStats())
    tools = _tools("kpi_summary", "give_answer")
    first = llm.generate("system", [UserMessage("How much?")], tools)
    assert [c.name for c in first.tool_calls] == ["kpi_summary"]
    results = ToolResultsMessage([ToolResult("c1", {"result_id": "r1"})])
    second = llm.generate("system", [UserMessage("How much?"), results], tools)
    assert second.tool_calls[0].name == "give_answer"
    assert run.EXPECTED_TOTAL in second.tool_calls[0].input["lead"]
    # Anything that is not a question (the dictionary draft) gets a reply it cannot use.
    assert llm.generate("system", [UserMessage("describe")], []).text_blocks == ["OK"]


def test_the_demo_orders_add_up_to_the_expected_total() -> None:
    lines = run.orders_csv().decode().strip().splitlines()[1:]
    assert round(sum(float(line.split(",")[4]) for line in lines)) == 6360
    assert run.EXPECTED_TOTAL == "₹6,360"


def test_percentiles() -> None:
    values = [float(v) for v in range(1, 101)]
    assert run.pct(values, 50) == 50 and run.pct(values, 95) == 95 and run.pct(values, 100) == 100
    assert run.pct([], 50) != run.pct([], 50)  # NaN when nothing was measured


def test_the_run_stays_laptop_sized() -> None:
    args = run.parse([])
    assert (args.users, args.rounds, args.workers, args.ramp) == (50, 1, 4, 10.0)
    for bad in (["--users", "500"], ["--workers", "64"], ["--rounds", "9"]):
        with pytest.raises(SystemExit):
            run.parse(bad)
