"""Claude through the official ``anthropic`` SDK (the supported alternative, R6; BUILD_SPEC 7.2).

* Native tool use. The assistant's original content blocks (thinking blocks included) are kept in
  ``LLMResponse.raw`` and sent back unchanged on the next turn, as the API requires.
* ``max_retries=3``: the SDK itself retries 408/409/429/5xx and connection errors with backoff, honouring
  ``retry-after``. We do not add a second retry loop on top.
* Models are listed from the key at runtime; ``CLAUDE_DEFAULT_MODEL`` (default ``claude-opus-5-5``) is used when
  the key offers it (``app.llm.models``).
* For the models that support it, requests opt into server-side refusal fallbacks (``fallbacks="default"``):
  if the model declines for a safety reason, the API re-runs the request on a suitable model in the same call.
  Usage is then recorded against the model that answered (``response.model``).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import anthropic

from app.llm.base import (
    AssistantMessage,
    LLMError,
    LLMResponse,
    Message,
    StopReason,
    ToolCall,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
    portable_history,
)
from app.llm.models import (
    CATALOG,
    MAX_MODEL_FALLBACKS,
    ModelCatalog,
    choose_model,
    fallback_pause,
)

logger = logging.getLogger(__name__)
PROVIDER = "claude"
MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_BUDGET_SECONDS = 150.0
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept ``fallbacks="default"`` on the Claude API.
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
_STOP: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refused",
}


def map_error(error: Exception) -> LLMError:
    """Most specific first: the SDK has one class per status (and some per cause)."""
    if isinstance(error, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return LLMError("invalid_key", PROVIDER, f"{error.status_code}")
    if isinstance(error, anthropic.NotFoundError):
        return LLMError("model_not_found", PROVIDER, "404")
    if isinstance(error, anthropic.EnforcedSpendLimitReachedError):
        return LLMError("quota", PROVIDER, "429 spend limit")
    if isinstance(error, anthropic.RateLimitError):
        retry_after = error.response.headers.get("retry-after")
        return LLMError(
            "rate_limited",
            PROVIDER,
            "429",
            retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
        )
    if isinstance(error, anthropic.BadRequestError):
        if "credit balance" in str(error.message).lower():
            return LLMError("quota", PROVIDER, "400 credit balance")
        return LLMError("other", PROVIDER, "400")
    if isinstance(error, anthropic.InternalServerError):  # 5xx, overloaded (529), unavailable (503)
        return LLMError("unavailable", PROVIDER, f"{error.status_code}")
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code == 402:
            return LLMError("quota", PROVIDER, "402")
        return LLMError("other", PROVIDER, f"{error.status_code}")
    if isinstance(error, anthropic.APIConnectionError):  # includes timeouts
        return LLMError("unavailable", PROVIDER, type(error).__name__)
    return LLMError("other", PROVIDER, type(error).__name__)


class ClaudeClient:
    provider = PROVIDER

    def __init__(
        self,
        api_key: str,
        *,
        preferred_model: str | None = None,
        sdk_client: Any | None = None,
        catalog: ModelCatalog = CATALOG,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._budget = budget_seconds
        self._clock = clock
        self._preferred = preferred_model or None
        self._catalog = catalog
        self._model: str | None = None
        self._client: Any = sdk_client or anthropic.Anthropic(
            api_key=api_key, max_retries=MAX_RETRIES, timeout=timeout_seconds
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> ClaudeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_models(self) -> list[str]:
        try:
            return [model.id for model in self._client.models.list(limit=100)]
        except Exception as error:
            raise map_error(error) from error

    @property
    def model(self) -> str:
        if self._model is None:
            available = self._catalog.get(PROVIDER, self._api_key, self.list_models)
            self._model = choose_model(PROVIDER, available, self._preferred)
        return self._model

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 3000,
    ) -> LLMResponse:
        params: dict[str, Any] = {"max_tokens": max_tokens, "messages": to_messages(messages)}
        deadline = self._clock() + self._budget
        if system:
            params["system"] = system
        if tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        last_error: LLMError | None = None
        for attempt in range(MAX_MODEL_FALLBACKS + 1):
            try:
                model = self.model
            except LLMError:
                if last_error is not None:  # every usable model failed: report the real reason
                    raise last_error from None
                raise
            try:
                response = self._create(model, params)
            except LLMError as error:
                pause = fallback_pause(error)  # re-raises errors another model cannot fix
                # Set the model aside even on the last attempt, so the next call starts elsewhere.
                self._catalog.exclude(PROVIDER, self._api_key, model, pause)
                self._model = None
                if attempt == MAX_MODEL_FALLBACKS or self._clock() >= deadline:
                    raise
                last_error = error
                logger.warning("Claude model %s failed (%s); trying another", model, error.kind)
                continue
            return from_response(response, model)
        raise AssertionError("unreachable")  # pragma: no cover

    def _create(self, model: str, params: dict[str, Any]) -> Any:
        try:
            if model in FALLBACK_MODELS:
                return self._client.beta.messages.create(
                    model=model, **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            return self._client.messages.create(model=model, **params)
        except Exception as error:
            raise map_error(error) from error


def _result_text(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def to_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in portable_history(messages, PROVIDER):
        if isinstance(message, UserMessage):
            out.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            out.append({"role": "assistant", "content": _assistant_content(message)})
        elif isinstance(message, ToolResultsMessage):
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": result.call_id,
                            "content": _result_text(result.content),
                            "is_error": result.is_error,
                        }
                        for result in message.results
                    ],
                }
            )
    return out


def _assistant_content(message: AssistantMessage) -> Any:
    if message.provider == PROVIDER and message.raw is not None:
        return message.raw  # the original blocks, thinking and fallback blocks included
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": text} for text in message.text_blocks if text
    ]
    blocks += [
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
        for call in message.tool_calls
    ]
    return blocks


def from_response(response: Any, requested_model: str) -> LLMResponse:
    usage_in = response.usage
    usage = Usage(
        input_tokens=(usage_in.input_tokens or 0)
        + (getattr(usage_in, "cache_creation_input_tokens", 0) or 0)
        + (getattr(usage_in, "cache_read_input_tokens", 0) or 0),
        output_tokens=usage_in.output_tokens or 0,
    )
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                ToolCall(id=block.id, name=block.name, input=dict(block.input), native_id=block.id)
            )
    stop = _STOP.get(str(response.stop_reason), "other")
    if tool_calls and stop != "refused":
        stop = "tool_use"
    model = str(getattr(response, "model", "") or requested_model)
    return LLMResponse(texts, tool_calls, stop, usage, PROVIDER, model, raw=list(response.content))
