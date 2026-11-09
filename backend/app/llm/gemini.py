"""Gemini through ``google-genai`` (the default provider, R6), following the known pitfalls of BUILD_SPEC 7.2:

1. The client is created with ``api_version="v1beta"`` (``GEMINI_API_VERSION``): function calling is not enabled
   on ``v1``.
2. The SDK client is created once and kept on this object for its whole life, so lazy pagers (``models.list``)
   never run against a closed, garbage-collected client. ``close()`` (or ``with``) ends it.
3. The model's original ``Content`` (with every ``thought_signature``) is kept in ``LLMResponse.raw`` and
   replayed unchanged on the next turn.
4. Tool schemas go in ``parameters_json_schema``; automatic function calling is disabled (our agent runs tools).
5. Function calls without an id get a generated one; results are sent back with the function's name (looked up
   from the id) and the provider's own id only when it sent one.
6. Models are listed from the key at runtime (``app.llm.models``); no single model name is hard-coded.

Retries (429/503 after 5/15/30 s, honouring ``retryDelay``) are done here with ``call_with_retries``, because
google-genai does not retry by default. An exhausted daily quota and an invalid key are not retried, and neither is
a request that used its whole timeout (504 or a client timeout): another model is tried instead. Each request has
``LLM_REQUEST_TIMEOUT_SECONDS``; each ``generate`` call, retries and fallbacks included, stops starting new attempts
after ``LLM_CALL_BUDGET_SECONDS``.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import httpx2
from google.genai import Client, errors, types

from app.llm.base import (
    RETRY_DELAYS,
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
    call_with_retries,
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
PROVIDER = "gemini"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_BUDGET_SECONDS = 150
_REFUSED = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
    types.FinishReason.RECITATION,
}


def _new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:16]}"


def _retry_delay(details: list[Any]) -> float | None:
    for item in details:
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("RetryInfo"):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _quota_ids(details: list[Any]) -> list[str]:
    return [
        str(violation.get("quotaId", "")).lower()
        for item in details
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("QuotaFailure")
        for violation in item.get("violations", []) or []
        if isinstance(violation, dict)
    ]


def _is_daily_quota(details: list[Any], message: str) -> bool:
    if any("perday" in quota for quota in _quota_ids(details)):
        return True
    lowered = message.lower()
    return "per day" in lowered or "daily" in lowered or "limit: 0" in lowered


def map_error(error: Exception) -> LLMError:
    """Translate an SDK or network error into an ``LLMError``."""
    if isinstance(error, httpx2.TimeoutException):
        # The whole request timeout has passed: retrying the same model would only double the wait.
        return LLMError("unavailable", PROVIDER, type(error).__name__, retryable=False)
    if isinstance(error, httpx2.HTTPError):
        return LLMError("unavailable", PROVIDER, type(error).__name__)
    if not isinstance(error, errors.APIError):
        return LLMError("other", PROVIDER, type(error).__name__)
    body = error.details if isinstance(error.details, dict) else {}
    payload = body.get("error", body) if isinstance(body, dict) else {}
    details = payload.get("details", []) if isinstance(payload, dict) else []
    details = details if isinstance(details, list) else []
    message = error.message or ""
    status = error.status or ""
    text = f"{status} {message} {details}"
    if "API_KEY_INVALID" in text or "API key not valid" in message or error.code in (401, 403):
        return LLMError("invalid_key", PROVIDER, f"{error.code} {status}")
    if error.code == 429:
        if _is_daily_quota(details, message):
            ids = _quota_ids(details)
            return LLMError(
                "quota",
                PROVIDER,
                f"429 {status} {' '.join(ids)}".strip(),
                retry_after=_retry_delay(details),
                retryable=False,
                # Free-tier quotas are counted per model: another model may still have requests left.
                model_scoped=bool(ids) and all("permodel" in quota for quota in ids),
            )
        return LLMError(
            "rate_limited", PROVIDER, f"429 {status}", retry_after=_retry_delay(details)
        )
    if error.code == 404:
        return LLMError("model_not_found", PROVIDER, f"404 {status}")
    if error.code == 504:
        # The server deadline (our timeout) already passed in full: waiting and retrying the same model only
        # makes people wait longer. Another model is tried instead.
        return LLMError("unavailable", PROVIDER, f"504 {status}", retryable=False)
    if error.code in (500, 502, 503):
        return LLMError("unavailable", PROVIDER, f"{error.code} {status}")
    return LLMError("other", PROVIDER, f"{error.code} {status}")


class GeminiClient:
    provider = PROVIDER

    def __init__(
        self,
        api_key: str,
        *,
        preferred_model: str | None = None,
        api_version: str = "v1beta",
        sdk_client: Any | None = None,
        catalog: ModelCatalog = CATALOG,
        sleep: Callable[[float], None] = time.sleep,
        retry_delays: Sequence[float] = RETRY_DELAYS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        budget_seconds: float = DEFAULT_BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._budget = budget_seconds
        self._clock = clock
        self._preferred = preferred_model or None
        self._catalog = catalog
        self._sleep = sleep
        self._retry_delays = retry_delays
        self._model: str | None = None
        # Pitfalls 1 and 2: v1beta, and one long-lived SDK client owned by this object.
        self._client: Any = sdk_client or Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                api_version=api_version, timeout=int(timeout_seconds * 1000)
            ),
        )

    # ------------------------------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> GeminiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---------------------------------------------------------------------------------------------- models
    def _list(self) -> list[str]:
        names: list[str] = []
        try:
            for model in self._client.models.list(config={"page_size": 100}):
                actions = model.supported_actions or []
                if model.name and "generateContent" in actions:
                    names.append(model.name.removeprefix("models/"))
        except Exception as error:
            raise map_error(error) from error
        return names

    def list_models(self) -> list[str]:
        return call_with_retries(self._list, sleep=self._sleep, delays=self._retry_delays)

    @property
    def model(self) -> str:
        if self._model is None:
            available = self._catalog.get(PROVIDER, self._api_key, self.list_models)
            self._model = choose_model(PROVIDER, available, self._preferred)
        return self._model

    # ------------------------------------------------------------------------------------------- generate
    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 3000,
    ) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=max_tokens,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters_json_schema=tool.parameters,  # pitfall 4
                        )
                        for tool in tools
                    ]
                )
            ]
            if tools
            else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = to_contents(messages)
        deadline = self._clock() + self._budget
        last_error: LLMError | None = None
        for attempt in range(MAX_MODEL_FALLBACKS + 1):
            try:
                model = self.model
            except LLMError:
                if last_error is not None:  # every usable model failed: report the real reason
                    raise last_error from None
                raise

            def call(model: str = model) -> Any:
                try:
                    return self._client.models.generate_content(
                        model=model, contents=contents, config=config
                    )
                except Exception as error:
                    raise map_error(error) from error

            try:
                response = call_with_retries(
                    call,
                    sleep=self._sleep,
                    delays=self._retry_delays,
                    deadline=deadline,
                    clock=self._clock,
                )
            except LLMError as error:
                pause = fallback_pause(error)  # re-raises errors another model cannot fix
                # Set the model aside even on the last attempt, so the next call starts elsewhere.
                self._catalog.exclude(PROVIDER, self._api_key, model, pause)
                self._model = None
                if attempt == MAX_MODEL_FALLBACKS or self._clock() >= deadline:
                    raise
                last_error = error
                logger.warning("Gemini model %s failed (%s); trying another", model, error.kind)
                continue
            return from_response(response, model)
        raise AssertionError("unreachable")  # pragma: no cover


# ------------------------------------------------------------------------------------------- conversions
def to_contents(messages: Sequence[Message]) -> list[types.Content]:
    contents: list[types.Content] = []
    calls: dict[str, ToolCall] = {}
    for message in portable_history(messages, PROVIDER):
        if isinstance(message, UserMessage):
            contents.append(types.Content(role="user", parts=[types.Part(text=message.text)]))
        elif isinstance(message, AssistantMessage):
            calls.update({call.id: call for call in message.tool_calls})
            contents.append(_assistant_content(message))
        else:
            contents.append(_tool_results(message, calls))
    return contents


def _assistant_content(message: AssistantMessage) -> types.Content:
    if message.provider == PROVIDER and isinstance(message.raw, types.Content):
        return message.raw  # pitfall 3: replay the original parts, thought signatures included
    parts = [types.Part(text=text) for text in message.text_blocks if text]
    parts += [
        types.Part(
            function_call=types.FunctionCall(id=call.native_id, name=call.name, args=call.input)
        )
        for call in message.tool_calls
    ]
    return types.Content(role="model", parts=parts or [types.Part(text="")])


def _tool_results(message: ToolResultsMessage, calls: dict[str, ToolCall]) -> types.Content:
    parts = []
    for result in message.results:
        call = calls.get(result.call_id)
        name = call.name if call else "tool"
        key = "error" if result.is_error else "output"
        response = {key: result.content}
        parts.append(
            types.Part(
                function_response=types.FunctionResponse(
                    id=call.native_id if call else None, name=name, response=response
                )
            )
        )
    return types.Content(role="user", parts=parts)


def from_response(response: Any, model: str) -> LLMResponse:
    meta = response.usage_metadata
    usage = Usage(
        input_tokens=(meta.prompt_token_count or 0) if meta else 0,
        # Thinking tokens are billed as output.
        output_tokens=((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0))
        if meta
        else 0,
    )
    candidate = response.candidates[0] if response.candidates else None
    if candidate is None:  # the prompt itself was blocked
        return LLMResponse([], [], "refused", usage, PROVIDER, model, raw=None)
    content = candidate.content
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in (content.parts if content and content.parts else []) or []:
        if part.function_call is not None:
            native = part.function_call.id or None
            tool_calls.append(
                ToolCall(
                    id=native or _new_call_id(),  # pitfall 5
                    name=part.function_call.name or "",
                    input=dict(part.function_call.args or {}),
                    native_id=native,
                )
            )
        elif part.text and not part.thought:
            texts.append(part.text)
    stop: StopReason
    if tool_calls:
        stop = "tool_use"
    elif candidate.finish_reason == types.FinishReason.STOP:
        stop = "end_turn"
    elif candidate.finish_reason == types.FinishReason.MAX_TOKENS:
        stop = "max_tokens"
    elif candidate.finish_reason in _REFUSED:
        stop = "refused"
    else:
        stop = "other"
    return LLMResponse(texts, tool_calls, stop, usage, PROVIDER, model, raw=content)
