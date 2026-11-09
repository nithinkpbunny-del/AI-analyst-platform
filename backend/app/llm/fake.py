"""The fake LLM (BUILD_SPEC 7.9): scripted responses for unit, API and end-to-end tests. CI never calls a model.

    llm = FakeLLM(script=[
        FakeTurn(tool_calls=[ToolCall("c1", "kpi_summary", {"metric": "revenue"})]),
        "Revenue grew 12%.",
    ])

Each ``generate`` call takes the next item: a ``FakeTurn``, a plain string (a text answer) or an ``LLMError`` to
raise. Every call is recorded in ``calls`` so tests can check the prompt, the history and the tools sent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)


@dataclass(frozen=True)
class FakeTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: StopReason | None = (
        None  # default: tool_use when there are tool calls, else end_turn
    )
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=120, output_tokens=30))


@dataclass(frozen=True)
class FakeCall:
    system: str
    messages: list[Message]
    tools: list[ToolSpec]
    max_tokens: int


ScriptItem = FakeTurn | str | LLMError


class FakeLLM:
    def __init__(
        self,
        script: Iterable[ScriptItem] = (),
        *,
        provider: str = "gemini",
        model: str = "fake-model",
        models: Sequence[str] | None = None,
        list_error: LLMError | None = None,
    ) -> None:
        self.provider = provider
        self._model = model
        self._script = list(script)
        self._models = list(models) if models is not None else [model]
        self._list_error = list_error
        self.calls: list[FakeCall] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def remaining(self) -> int:
        return len(self._script)

    def list_models(self) -> list[str]:
        if self._list_error is not None:
            raise self._list_error
        return list(self._models)

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 3000,
    ) -> LLMResponse:
        self.calls.append(FakeCall(system, list(messages), list(tools), max_tokens))
        if not self._script:
            raise AssertionError(
                "FakeLLM script is exhausted: the code made more calls than expected"
            )
        item = self._script.pop(0)
        if isinstance(item, LLMError):
            raise item
        turn = FakeTurn(text=item) if isinstance(item, str) else item
        stop = turn.stop_reason or ("tool_use" if turn.tool_calls else "end_turn")
        return LLMResponse(
            text_blocks=[turn.text] if turn.text else [],
            tool_calls=list(turn.tool_calls),
            stop_reason=stop,
            usage=turn.usage,
            provider=self.provider,
            model=self._model,
            raw={"fake": True},
        )


class FakeLLMFactory:
    """An ``LLMFactory`` for app tests: every key works except those in ``rejected_keys``.

    Each client it builds answers ``reply`` once (enough for the "Test AI connection" check). Set ``next_script``
    to script the next client instead. ``created`` lists ``(provider, api_key)`` for each client built.
    """

    def __init__(self, *, reply: str = "OK", rejected_keys: Iterable[str] = ()) -> None:
        self.reply = reply
        self.rejected_keys = set(rejected_keys)
        self.unreachable = False
        self.next_script: list[ScriptItem] | None = None
        self.created: list[tuple[str, str]] = []
        self.clients: list[FakeLLM] = []

    def __call__(self, provider: str, api_key: str) -> FakeLLM:
        self.created.append((provider, api_key))
        list_error = None
        if api_key in self.rejected_keys:
            list_error = LLMError("invalid_key", provider, "rejected by the fake")
        elif self.unreachable:
            list_error = LLMError("unavailable", provider, "unreachable fake")
        script: list[ScriptItem] = (
            self.next_script if self.next_script is not None else [self.reply]
        )
        self.next_script = None
        if list_error is not None:
            script = [list_error]
        model = "gemini-2.5-flash" if provider == "gemini" else "claude-opus-5-5"
        client = FakeLLM(script, provider=provider, model=model, list_error=list_error)
        self.clients.append(client)
        return client
