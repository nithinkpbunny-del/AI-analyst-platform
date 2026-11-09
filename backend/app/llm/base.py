"""The provider-neutral LLM interface (BUILD_SPEC 7.2).

``LLMClient.generate(system, messages, tools, max_tokens) -> LLMResponse`` with normalised ``text_blocks``,
``tool_calls[{id, name, input}]``, ``stop_reason`` and ``usage``. Gemini, Claude and the fake client implement it.

A conversation is a list of ``UserMessage``, ``AssistantMessage`` and ``ToolResultsMessage``. An
``AssistantMessage`` keeps the provider's original content in ``raw`` so the next turn can replay it unchanged
(Gemini thought signatures, Claude thinking blocks: pitfall 3). ``portable_history`` rewrites turns written by a
*different* provider as plain text, because one provider cannot replay another's tool-call turns.

Errors are ``LLMError`` with a ``kind``; the API maps kinds to friendly messages (invalid key / quota / model not
found / other). ``call_with_retries`` retries 429 and 503 after 5 s, 15 s and 30 s, honouring the provider's
``retryDelay``, and never retries an exhausted daily quota or an invalid key.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Provider = Literal["gemini", "claude"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refused", "other"]
ErrorKind = Literal[
    "invalid_key", "quota", "rate_limited", "model_not_found", "unavailable", "other"
]


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. ``parameters`` is a JSON schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    # The provider's own id, when it sent one (Gemini may not: pitfall 5). ``id`` is always set.
    native_id: str | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text_blocks: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str | None = None
    raw: Any = None  # provider-native content, replayed as-is to the same provider

    @property
    def text(self) -> str:
        return "".join(self.text_blocks)


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: Any  # JSON-serialisable
    is_error: bool = False


@dataclass(frozen=True)
class ToolResultsMessage:
    results: list[ToolResult]


Message = UserMessage | AssistantMessage | ToolResultsMessage


@dataclass(frozen=True)
class LLMResponse:
    text_blocks: list[str]
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: Usage
    provider: str
    model: str
    raw: Any = None

    @property
    def text(self) -> str:
        return "".join(self.text_blocks)

    def as_message(self) -> AssistantMessage:
        """The assistant turn to append to the conversation (keeps ``raw`` for replay)."""
        return AssistantMessage(
            text_blocks=list(self.text_blocks),
            tool_calls=list(self.tool_calls),
            provider=self.provider,
            raw=self.raw,
        )


class LLMClient(Protocol):
    provider: str

    @property
    def model(self) -> str:
        """The model this client calls (resolved from the key's model list on first use)."""
        ...

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 3000,
    ) -> LLMResponse: ...

    def list_models(self) -> list[str]:
        """Models this key can call for text generation (pitfall 6: never hard-code one)."""
        ...


def close_client(client: object) -> None:
    """Release a client's HTTP connections (real clients have ``close()``; the fake does not)."""
    close = getattr(client, "close", None)
    if callable(close):
        close()


# A factory builds a client for a provider and an API key. Tests inject one that returns ``FakeLLM``.
LLMFactory = Callable[[str, str], LLMClient]


# -------------------------------------------------------------------------------------------------- errors
class LLMError(Exception):
    """A provider call failed. ``detail`` is for logs only (never shown, may contain provider text)."""

    def __init__(
        self,
        kind: ErrorKind,
        provider: str,
        detail: str = "",
        *,
        retry_after: float | None = None,
        retryable: bool = True,
        model_scoped: bool = False,
    ) -> None:
        super().__init__(f"{provider}: {kind}")
        self.kind: ErrorKind = kind
        self.provider = provider
        self.detail = detail
        self.retry_after = retry_after
        # False when waiting cannot help or already happened (e.g. a server deadline that took its full time).
        self.retryable = retryable
        # True when only this model is affected (e.g. Gemini's per-model daily quota): another model may work.
        self.model_scoped = model_scoped


RETRYABLE: frozenset[ErrorKind] = frozenset({"rate_limited", "unavailable"})
RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 30.0)
MAX_RETRY_WAIT = 60.0


def call_with_retries[T](
    call: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    delays: Sequence[float] = RETRY_DELAYS,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Run ``call``; on a retryable ``LLMError`` wait and try again (``len(delays)`` retries at most).

    No retry starts if waiting would pass ``deadline`` (a ``clock()`` value): the caller's time budget wins.
    """
    for delay in [*delays, None]:
        try:
            return call()
        except LLMError as error:
            if error.kind not in RETRYABLE or not error.retryable or delay is None:
                raise
            wait = error.retry_after if error.retry_after is not None else delay
            wait = min(max(wait, 0.0), MAX_RETRY_WAIT)
            if deadline is not None and clock() + wait >= deadline:
                raise
            sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


# ------------------------------------------------------------------------------------------------- history
def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def portable_history(messages: Sequence[Message], provider: str) -> list[Message]:
    """Turns another provider wrote (e.g. after a workspace switched keys) become plain text for ``provider``.

    Tool calls of a foreign turn are written into its text, and the results of those calls become a user
    message, so the conversation keeps its meaning without replaying another provider's native structures.
    """
    foreign_calls: dict[str, str] = {}
    out: list[Message] = []
    for message in messages:
        if isinstance(message, AssistantMessage) and message.provider not in (None, provider):
            lines = [*message.text_blocks]
            for call in message.tool_calls:
                foreign_calls[call.id] = call.name
                lines.append(f"[Called tool {call.name} with {_as_text(call.input)}]")
            out.append(AssistantMessage(text_blocks=["\n".join(lines)], provider=None))
        elif isinstance(message, ToolResultsMessage) and any(
            r.call_id in foreign_calls for r in message.results
        ):
            lines = [
                f"[Result of {foreign_calls.get(r.call_id, 'tool')}"
                f"{' (error)' if r.is_error else ''}: {_as_text(r.content)}]"
                for r in message.results
            ]
            out.append(UserMessage("\n".join(lines)))
        else:
            out.append(message)
    return out
