"""The Claude adapter (BUILD_SPEC 7.2): native tool use, ``max_retries=3``, replay of original blocks, error kinds.

The stub SDK returns real ``anthropic.types.Message`` objects and the error tests raise the SDK's own exception
classes, so a change in the SDK's shapes shows up here.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message as ClaudeMessage

from app.llm.base import (
    AssistantMessage,
    LLMError,
    Message,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from app.llm.claude import FALLBACK_BETA, MAX_RETRIES, ClaudeClient, map_error
from app.llm.models import ModelCatalog

TOOL = ToolSpec("kpi_summary", "Metric values", {"type": "object", "properties": {}})


def message(
    content: list[dict[str, Any]], stop: str = "end_turn", model: str = "claude-opus-5-5"
) -> ClaudeMessage:
    return ClaudeMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 5,
            },
        }
    )


class StubMessages:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []

    def create(self, **params: Any) -> Any:
        self.requests.append(params)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class StubModelList:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    def list(self, **_: Any) -> list[Any]:
        return [type("ModelInfo", (), {"id": i})() for i in self.ids]


class StubSdk:
    def __init__(self, replies: list[Any], models: list[str] | None = None) -> None:
        self.messages = StubMessages(replies)
        self.beta = type("Beta", (), {})()
        self.beta.messages = self.messages
        self.models = StubModelList(
            models or ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
        )


def make(replies: list[Any], **kwargs: Any) -> tuple[ClaudeClient, StubSdk]:
    sdk = StubSdk(replies, kwargs.pop("models", None))
    client = ClaudeClient(
        "test-key",
        preferred_model=kwargs.pop("preferred_model", "claude-opus-5-5"),
        sdk_client=sdk,
        catalog=ModelCatalog(),
    )
    return client, sdk


def status_error(
    cls: type[anthropic.APIStatusError],
    code: int,
    message: str = "error",
    headers: dict[str, str] | None = None,
) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(code, request=request, headers=headers or {})
    return cls(message, response=response, body=None)


def test_the_sdk_client_retries_three_times() -> None:
    client = ClaudeClient("test-key")
    assert MAX_RETRIES == 3
    assert client._client.max_retries == 3


def test_tools_system_and_limits_are_sent_natively() -> None:
    client, sdk = make([message([{"type": "text", "text": "Hello"}])])
    response = client.generate("You are an analyst.", [UserMessage("Hi")], [TOOL], 3000)
    request = sdk.messages.requests[0]
    assert request["system"] == "You are an analyst."
    assert request["max_tokens"] == 3000
    assert request["tools"] == [
        {"name": "kpi_summary", "description": "Metric values", "input_schema": TOOL.parameters}
    ]
    assert request["messages"] == [{"role": "user", "content": "Hi"}]
    assert response.text == "Hello"
    assert response.stop_reason == "end_turn"


def test_usage_counts_cached_input_too() -> None:
    client, _ = make([message([{"type": "text", "text": "x"}])])
    usage = client.generate("", [UserMessage("q")]).usage
    assert usage.input_tokens == 135
    assert usage.output_tokens == 20


def test_tool_calls_and_replay_of_the_original_blocks() -> None:
    first = message(
        [
            {"type": "thinking", "thinking": "", "signature": "sig-1"},
            {"type": "text", "text": "Checking."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "kpi_summary",
                "input": {"metric": "revenue"},
            },
        ],
        stop="tool_use",
    )
    client, sdk = make([first, message([{"type": "text", "text": "Revenue is up."}])])
    response = client.generate("", [UserMessage("Revenue?")], [TOOL])
    assert response.stop_reason == "tool_use"
    assert response.tool_calls == [
        ToolCall("toolu_1", "kpi_summary", {"metric": "revenue"}, native_id="toolu_1")
    ]
    history: list[Message] = [
        UserMessage("Revenue?"),
        response.as_message(),
        ToolResultsMessage([ToolResult("toolu_1", {"value": 12})]),
    ]
    client.generate("", history, [TOOL])
    sent = sdk.messages.requests[1]["messages"]
    assert sent[1]["role"] == "assistant"
    assert sent[1]["content"][0].type == "thinking"  # the thinking block goes back unchanged
    assert sent[1]["content"][0].signature == "sig-1"
    assert sent[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": '{"value": 12}',
                "is_error": False,
            }
        ],
    }


def test_turns_from_another_provider_become_text() -> None:
    gemini_turn = AssistantMessage(
        tool_calls=[ToolCall("call_1", "kpi_summary", {"metric": "aov"})],
        provider="gemini",
        raw=object(),
    )
    client, sdk = make([message([{"type": "text", "text": "ok"}])])
    client.generate(
        "",
        [UserMessage("AOV?"), gemini_turn, ToolResultsMessage([ToolResult("call_1", 1500)])],
    )
    sent = sdk.messages.requests[0]["messages"]
    assert sent[1]["content"][0]["type"] == "text"
    assert "kpi_summary" in sent[1]["content"][0]["text"]
    assert sent[2] == {"role": "user", "content": "[Result of kpi_summary: 1500]"}


def test_refusal_fallbacks_on_models_that_support_them() -> None:
    client, sdk = make([message([{"type": "text", "text": "x"}])])
    client.generate("", [UserMessage("q")])
    assert sdk.messages.requests[0]["betas"] == [FALLBACK_BETA]
    assert sdk.messages.requests[0]["fallbacks"] == "default"


def test_no_fallbacks_on_other_models() -> None:
    client, sdk = make(
        [message([{"type": "text", "text": "x"}], model="claude-haiku-4-5")],
        models=["claude-haiku-4-5"],
        preferred_model="claude-haiku-4-5",
    )
    response = client.generate("", [UserMessage("q")])
    assert "betas" not in sdk.messages.requests[0]
    assert response.model == "claude-haiku-4-5"


def test_a_refusal_is_reported() -> None:
    client, _ = make([message([], stop="refusal")])
    assert client.generate("", [UserMessage("q")]).stop_reason == "refused"


def test_the_configured_model_or_the_newest_of_its_family() -> None:
    client, _ = make([], models=["claude-sonnet-5", "claude-sonnet-5-5", "claude-haiku-5-5"])
    assert client.model == "claude-sonnet-5-5"  # claude-opus-5-5 is not offered to this key
    client, _ = make([])
    assert client.model == "claude-opus-5-5"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (status_error(anthropic.AuthenticationError, 401), "invalid_key"),
        (status_error(anthropic.PermissionDeniedError, 403), "invalid_key"),
        (status_error(anthropic.NotFoundError, 404, "model: claude-x"), "model_not_found"),
        (status_error(anthropic.EnforcedSpendLimitReachedError, 429), "quota"),
        (status_error(anthropic.RateLimitError, 429), "rate_limited"),
        (
            status_error(
                anthropic.BadRequestError, 400, "Your credit balance is too low to access the API."
            ),
            "quota",
        ),
        (status_error(anthropic.BadRequestError, 400, "messages: roles must alternate"), "other"),
        (status_error(anthropic.OverloadedError, 529), "unavailable"),
        (status_error(anthropic.InternalServerError, 500), "unavailable"),
        (status_error(anthropic.APIStatusError, 402), "quota"),
        (status_error(anthropic.APIStatusError, 418), "other"),
    ],
)
def test_errors_get_a_kind(error: Exception, kind: str) -> None:
    client, _ = make([error, error, error])  # a 404 first tries the other models
    with pytest.raises(LLMError) as caught:
        client.generate("", [UserMessage("q")])
    assert caught.value.kind == kind
    assert caught.value.provider == "claude"


def test_rate_limit_keeps_retry_after() -> None:
    error = status_error(anthropic.RateLimitError, 429, headers={"retry-after": "12"})
    assert map_error(error).retry_after == 12.0


def test_network_errors_are_unavailable() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    assert map_error(anthropic.APIConnectionError(request=request)).kind == "unavailable"
    assert map_error(anthropic.APITimeoutError(request=request)).kind == "unavailable"
    assert map_error(ValueError("odd")).kind == "other"


def test_listing_models_maps_errors() -> None:
    sdk = StubSdk([])

    def broken(**_: Any) -> list[Any]:
        raise status_error(anthropic.AuthenticationError, 401)

    sdk.models.list = broken  # type: ignore[method-assign]
    client = ClaudeClient("bad", sdk_client=sdk, catalog=ModelCatalog())
    with pytest.raises(LLMError) as caught:
        client.list_models()
    assert caught.value.kind == "invalid_key"


def test_a_refused_model_falls_back_to_the_next_best() -> None:
    gone = status_error(anthropic.NotFoundError, 404, "model: claude-opus-5-5")
    client, sdk = make([gone, message([{"type": "text", "text": "ok"}], model="claude-sonnet-5-5")])
    response = client.generate("", [UserMessage("q")])
    assert [r["model"] for r in sdk.messages.requests] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert response.model == "claude-sonnet-5-5"


def test_timeouts_come_from_settings() -> None:
    from app.config import Settings
    from app.llm.keys import default_factory

    settings = Settings(_env_file=None, llm_request_timeout_seconds=30, llm_call_budget_seconds=90)
    client = default_factory(settings)("claude", "test-key")
    assert isinstance(client, ClaudeClient)
    assert client._client.timeout == 30
    assert client._budget == 90


def test_no_fallback_after_the_budget() -> None:
    now = [0.0]
    gone = status_error(anthropic.NotFoundError, 404, "model: claude-opus-5-5")
    sdk = StubSdk([gone, message([{"type": "text", "text": "ok"}])])

    def create(**params: Any) -> Any:
        now[0] += 200  # this call took longer than the whole budget
        return StubMessages.create(sdk.messages, **params)

    sdk.messages.create = create  # type: ignore[method-assign]
    client = ClaudeClient(
        "k", sdk_client=sdk, catalog=ModelCatalog(), budget_seconds=150, clock=lambda: now[0]
    )
    with pytest.raises(LLMError) as caught:
        client.generate("", [UserMessage("q")])
    assert caught.value.kind == "model_not_found"
    assert len(sdk.messages.requests) == 1
