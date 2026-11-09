"""The Gemini adapter follows every pitfall of BUILD_SPEC 7.2, checked against a stub of the SDK client (no network).

The stub returns real ``google.genai.types`` objects and raises real ``google.genai.errors`` exceptions, so the
tests also catch changes in the SDK's shapes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from google.genai import errors, types

from app.llm import gemini
from app.llm.base import (
    LLMError,
    Message,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from app.llm.gemini import GeminiClient
from app.llm.models import ModelCatalog

TOOL = ToolSpec(
    name="kpi_summary",
    description="Metric values for a period",
    parameters={
        "type": "object",
        "properties": {"metric": {"type": "string"}},
        "required": ["metric"],
    },
)


class StubModels:
    def __init__(self, replies: list[Any], models: list[types.Model]) -> None:
        self.replies = replies
        self.models = models
        self.requests: list[dict[str, Any]] = []
        self.list_calls = 0

    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.requests.append({"model": model, "contents": contents, "config": config})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def list(self, *, config: Any = None) -> Iterator[types.Model]:
        self.list_calls += 1
        return iter(self.models)


class StubSdk:
    def __init__(self, replies: list[Any], models: list[types.Model] | None = None) -> None:
        self.models = StubModels(
            replies,
            models
            if models is not None
            else [
                types.Model(name="models/gemini-2.5-flash", supported_actions=["generateContent"]),
                types.Model(name="models/gemini-2.5-pro", supported_actions=["generateContent"]),
                types.Model(name="models/text-embedding-004", supported_actions=["embedContent"]),
            ],
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True


def reply(
    *parts: types.Part,
    finish: types.FinishReason = types.FinishReason.STOP,
    prompt_tokens: int = 50,
    output_tokens: int = 10,
    thoughts: int | None = None,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)), finish_reason=finish
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thoughts,
        ),
    )


def make(replies: list[Any], **kwargs: Any) -> tuple[GeminiClient, StubSdk, list[float]]:
    sdk = StubSdk(replies, kwargs.pop("models", None))
    sleeps: list[float] = []
    client = GeminiClient(
        "test-key",
        preferred_model=kwargs.pop("preferred_model", "gemini-2.5-flash"),
        sdk_client=sdk,
        catalog=ModelCatalog(),
        sleep=sleeps.append,
        **kwargs,
    )
    return client, sdk, sleeps


def api_error(code: int, status: str, message: str, details: list[Any] | None = None) -> Exception:
    body = {"error": {"code": code, "message": message, "status": status, "details": details or []}}
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, body)


class TestClientSetup:
    def test_uses_v1beta_so_function_calling_works(self) -> None:
        """Pitfall 1: function calling is not enabled on v1."""
        client = GeminiClient("test-key")
        http_options = client._client._api_client._http_options
        assert http_options.api_version == "v1beta"
        client.close()

    def test_one_long_lived_sdk_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pitfall 2: the SDK client is created once and kept, so lazy pagers never hit a closed client."""
        created: list[StubSdk] = []

        def fake_client(**_: Any) -> StubSdk:
            sdk = StubSdk([reply(types.Part(text="one")), reply(types.Part(text="two"))])
            created.append(sdk)
            return sdk

        monkeypatch.setattr(gemini, "Client", fake_client)
        client = GeminiClient("test-key", catalog=ModelCatalog())
        client.generate("", [UserMessage("hi")])
        client.generate("", [UserMessage("again")])
        client.list_models()
        assert len(created) == 1
        with client:
            pass
        assert created[0].closed


class TestRequests:
    def test_tools_use_json_schema_and_automatic_calling_is_off(self) -> None:
        """Pitfall 4."""
        client, sdk, _ = make([reply(types.Part(text="Revenue grew."))])
        client.generate("You are an analyst.", [UserMessage("How is revenue?")], [TOOL], 3000)
        config = sdk.models.requests[0]["config"]
        declaration = config.tools[0].function_declarations[0]
        assert declaration.name == "kpi_summary"
        assert declaration.parameters_json_schema == TOOL.parameters
        assert declaration.parameters is None
        assert config.automatic_function_calling.disable is True
        assert config.system_instruction == "You are an analyst."
        assert config.max_output_tokens == 3000

    def test_no_tools_sends_no_tool_config(self) -> None:
        client, sdk, _ = make([reply(types.Part(text="Hi"))])
        client.generate("", [UserMessage("Hi")])
        config = sdk.models.requests[0]["config"]
        assert config.tools is None
        assert config.system_instruction is None


class TestToolCalls:
    def test_missing_ids_are_generated_and_results_are_sent_by_name(self) -> None:
        """Pitfall 5: function calls may come without an id."""
        call = types.Part(
            function_call=types.FunctionCall(name="kpi_summary", args={"metric": "revenue"})
        )
        client, sdk, _ = make([reply(call), reply(types.Part(text="Revenue is ₹12.4 L."))])
        first = client.generate("", [UserMessage("Revenue?")], [TOOL])
        assert first.stop_reason == "tool_use"
        [tool_call] = first.tool_calls
        assert tool_call.id.startswith("call_")
        assert tool_call.native_id is None
        assert tool_call.input == {"metric": "revenue"}

        history: list[Message] = [
            UserMessage("Revenue?"),
            first.as_message(),
            ToolResultsMessage([ToolResult(tool_call.id, {"value": 1240000})]),
        ]
        second = client.generate("", history, [TOOL])
        assert second.text == "Revenue is ₹12.4 L."
        sent = sdk.models.requests[1]["contents"][2].parts[0].function_response
        assert sent.name == "kpi_summary"
        assert sent.id is None
        assert sent.response == {"output": {"value": 1240000}}

    def test_provider_ids_are_kept(self) -> None:
        call = types.Part(
            function_call=types.FunctionCall(id="fc-7", name="kpi_summary", args={"metric": "aov"})
        )
        client, sdk, _ = make([reply(call), reply(types.Part(text="Done"))])
        first = client.generate("", [UserMessage("AOV?")], [TOOL])
        assert first.tool_calls[0].id == "fc-7"
        history: list[Message] = [
            UserMessage("AOV?"),
            first.as_message(),
            ToolResultsMessage([ToolResult("fc-7", "failed", is_error=True)]),
        ]
        client.generate("", history, [TOOL])
        sent = sdk.models.requests[1]["contents"][2].parts[0].function_response
        assert sent.id == "fc-7"
        assert sent.response == {"error": "failed"}

    def test_original_parts_and_thought_signatures_are_replayed(self) -> None:
        """Pitfall 3: newer models reject function-call turns without their thought signatures."""
        call = types.Part(
            function_call=types.FunctionCall(name="kpi_summary", args={"metric": "orders"}),
            thought_signature=b"signature-bytes",
        )
        client, sdk, _ = make([reply(call), reply(types.Part(text="Done"))])
        first = client.generate("", [UserMessage("Orders?")], [TOOL])
        history: list[Message] = [
            UserMessage("Orders?"),
            first.as_message(),
            ToolResultsMessage([ToolResult(first.tool_calls[0].id, 42)]),
        ]
        client.generate("", history, [TOOL])
        replayed = sdk.models.requests[1]["contents"][1]
        assert replayed is first.raw
        assert replayed.parts[0].thought_signature == b"signature-bytes"

    def test_turns_from_another_provider_become_text(self) -> None:
        from app.llm.base import AssistantMessage, ToolCall

        claude_turn = AssistantMessage(
            text_blocks=["Let me check."],
            tool_calls=[ToolCall("toolu_1", "kpi_summary", {"metric": "revenue"})],
            provider="claude",
            raw=[{"type": "tool_use"}],
        )
        client, sdk, _ = make([reply(types.Part(text="ok"))])
        client.generate(
            "",
            [
                UserMessage("Revenue?"),
                claude_turn,
                ToolResultsMessage([ToolResult("toolu_1", {"value": 5})]),
            ],
        )
        contents = sdk.models.requests[0]["contents"]
        assert "kpi_summary" in contents[1].parts[0].text
        assert contents[1].parts[0].function_call is None
        assert contents[2].role == "user"
        assert '{"value": 5}' in contents[2].parts[0].text


class TestResponses:
    def test_thoughts_are_not_answer_text_but_their_tokens_count(self) -> None:
        client, _, _ = make(
            [
                reply(
                    types.Part(text="thinking...", thought=True),
                    types.Part(text="Answer."),
                    output_tokens=10,
                    thoughts=25,
                )
            ]
        )
        response = client.generate("", [UserMessage("q")])
        assert response.text_blocks == ["Answer."]
        assert response.usage.input_tokens == 50
        assert response.usage.output_tokens == 35
        assert response.model == "gemini-2.5-flash"
        assert response.provider == "gemini"

    @pytest.mark.parametrize(
        ("finish", "stop"),
        [
            (types.FinishReason.STOP, "end_turn"),
            (types.FinishReason.MAX_TOKENS, "max_tokens"),
            (types.FinishReason.SAFETY, "refused"),
            (types.FinishReason.MALFORMED_FUNCTION_CALL, "other"),
        ],
    )
    def test_finish_reasons(self, finish: types.FinishReason, stop: str) -> None:
        client, _, _ = make([reply(types.Part(text="x"), finish=finish)])
        assert client.generate("", [UserMessage("q")]).stop_reason == stop

    def test_a_blocked_prompt_has_no_candidates(self) -> None:
        client, _, _ = make([types.GenerateContentResponse(candidates=[])])
        response = client.generate("", [UserMessage("q")])
        assert response.stop_reason == "refused"
        assert response.text == ""


class TestModels:
    def test_lists_text_models_from_the_key(self) -> None:
        """Pitfall 6: models come from the key at runtime."""
        client, _, _ = make([])
        assert client.list_models() == ["gemini-2.5-flash", "gemini-2.5-pro"]

    def test_uses_the_configured_model_when_offered(self) -> None:
        client, sdk, _ = make([reply(types.Part(text="x"))], preferred_model="gemini-2.5-pro")
        client.generate("", [UserMessage("q")])
        assert sdk.models.requests[0]["model"] == "gemini-2.5-pro"

    def test_falls_back_when_the_configured_model_is_gone(self) -> None:
        models = [
            types.Model(name="models/gemini-3-flash", supported_actions=["generateContent"]),
            types.Model(name="models/gemini-3-pro", supported_actions=["generateContent"]),
        ]
        client, sdk, _ = make(
            [reply(types.Part(text="x"))], models=models, preferred_model="gemini-2.5-flash"
        )
        client.generate("", [UserMessage("q")])
        assert sdk.models.requests[0]["model"] == "gemini-3-flash"

    def test_the_model_list_is_cached_per_key(self) -> None:
        catalog = ModelCatalog()
        sdk = StubSdk([reply(types.Part(text="a")), reply(types.Part(text="b"))])
        GeminiClient("k", sdk_client=sdk, catalog=catalog).generate("", [UserMessage("q")])
        GeminiClient("k", sdk_client=sdk, catalog=catalog).generate("", [UserMessage("q")])
        assert sdk.models.list_calls == 1


QUOTA_PER_DAY = [  # project-wide: another model would not help (per-model quotas: TestPerModelQuota)
    {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerDayPerProject-FreeTier"}],
    },
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "40s"},
]
PER_MINUTE = [
    {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}],
    },
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "23s"},
]


class TestErrorsAndRetries:
    def test_rate_limits_wait_for_the_retry_delay(self) -> None:
        client, sdk, sleeps = make(
            [
                api_error(429, "RESOURCE_EXHAUSTED", "Slow down", PER_MINUTE),
                reply(types.Part(text="ok")),
            ]
        )
        assert client.generate("", [UserMessage("q")]).text == "ok"
        assert sleeps == [23.0]
        assert len(sdk.models.requests) == 2

    def test_unavailable_is_retried_after_5_15_30_seconds_then_another_model(self) -> None:
        overloaded = [
            api_error(503, "UNAVAILABLE", "This model is currently experiencing high demand.")
            for _ in range(4)
        ]
        client, sdk, sleeps = make([*overloaded, reply(types.Part(text="ok"))])
        response = client.generate("", [UserMessage("q")])
        assert sleeps == [5.0, 15.0, 30.0]
        assert [r["model"] for r in sdk.models.requests] == ["gemini-2.5-flash"] * 4 + [
            "gemini-2.5-pro"
        ]
        assert response.model == "gemini-2.5-pro"

    def test_a_deadline_that_took_its_full_time_is_not_waited_out_again(self) -> None:
        client, sdk, sleeps = make(
            [api_error(504, "DEADLINE_EXCEEDED", "Deadline expired"), reply(types.Part(text="ok"))]
        )
        assert client.generate("", [UserMessage("q")]).model == "gemini-2.5-pro"
        assert sleeps == []
        assert len(sdk.models.requests) == 2

    def test_gives_up_when_every_model_is_down(self) -> None:
        client, sdk, _ = make([api_error(504, "DEADLINE_EXCEEDED", "Deadline expired")] * 3)
        with pytest.raises(LLMError) as caught:
            client.generate("", [UserMessage("q")])
        assert caught.value.kind == "unavailable"
        assert len(sdk.models.requests) == 2  # both usable models were tried

    def test_an_exhausted_daily_quota_is_not_retried(self) -> None:
        client, sdk, sleeps = make([api_error(429, "RESOURCE_EXHAUSTED", "Quota", QUOTA_PER_DAY)])
        with pytest.raises(LLMError) as caught:
            client.generate("", [UserMessage("q")])
        assert caught.value.kind == "quota"
        assert sleeps == []
        assert len(sdk.models.requests) == 1

    @pytest.mark.parametrize(
        ("error", "kind"),
        [
            (
                api_error(
                    400,
                    "INVALID_ARGUMENT",
                    "API key not valid. Please pass a valid API key.",
                    [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "API_KEY_INVALID",
                        }
                    ],
                ),
                "invalid_key",
            ),
            (api_error(403, "PERMISSION_DENIED", "Permission denied"), "invalid_key"),
            (api_error(404, "NOT_FOUND", "models/gemini-9 is not found"), "model_not_found"),
            (api_error(400, "INVALID_ARGUMENT", "Bad schema"), "other"),
        ],
    )
    def test_errors_get_a_kind_and_are_not_retried(self, error: Exception, kind: str) -> None:
        client, _, sleeps = make([error, error, error])  # a 404 first tries the other models
        with pytest.raises(LLMError) as caught:
            client.generate("", [UserMessage("q")])
        assert caught.value.kind == kind
        assert sleeps == []

    def test_network_errors_count_as_unavailable(self) -> None:
        import httpx2

        assert gemini.map_error(httpx2.ConnectError("refused")).kind == "unavailable"
        assert gemini.map_error(ValueError("odd")).kind == "other"

    def test_an_invalid_key_while_listing_models(self) -> None:
        sdk = StubSdk([])

        def broken_list(*, config: Any = None) -> Iterator[types.Model]:
            raise api_error(
                400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."
            )

        sdk.models.list = broken_list  # type: ignore[method-assign]
        client = GeminiClient("bad", sdk_client=sdk, catalog=ModelCatalog(), sleep=lambda _: None)
        with pytest.raises(LLMError) as caught:
            client.list_models()
        assert caught.value.kind == "invalid_key"


class TestRefusedModels:
    """Google keeps listing models it no longer serves to new keys ("no longer available to new users")."""

    GONE = api_error(
        404,
        "NOT_FOUND",
        "This model models/gemini-2.5-flash is no longer available to new users. Please update your code to "
        "use models/gemini-3.8-flash.",
    )

    def test_a_listed_but_refused_model_is_skipped(self) -> None:
        models = [
            types.Model(name=f"models/{name}", supported_actions=["generateContent"])
            for name in ("gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.8-flash-tts")
        ]
        catalog = ModelCatalog()
        sdk = StubSdk(
            [self.GONE, reply(types.Part(text="OK")), reply(types.Part(text="again"))], models
        )
        client = GeminiClient(
            "k",
            preferred_model="gemini-2.5-flash",
            sdk_client=sdk,
            catalog=catalog,
            sleep=lambda _: None,
        )
        response = client.generate("", [UserMessage("q")])
        assert response.text == "OK"
        assert response.model == "gemini-3.8-flash"
        assert [r["model"] for r in sdk.models.requests] == ["gemini-2.5-flash", "gemini-3.8-flash"]
        # The next client for the same key goes straight to the working model.
        second = GeminiClient(
            "k", preferred_model="gemini-2.5-flash", sdk_client=sdk, catalog=catalog
        )
        assert second.generate("", [UserMessage("q")]).model == "gemini-3.8-flash"
        assert sdk.models.list_calls == 1

    def test_gives_up_when_no_model_works(self) -> None:
        client, sdk, _ = make([self.GONE, self.GONE, self.GONE])
        with pytest.raises(LLMError) as caught:
            client.generate("", [UserMessage("q")])
        assert caught.value.kind == "model_not_found"
        assert len(sdk.models.requests) == 2  # both usable models were tried


class TestTimeBudget:
    """Overloaded providers must not keep a person waiting for many minutes (seen live on 2026-11-09)."""

    def test_a_client_timeout_is_not_retried_on_the_same_model(self) -> None:
        import httpx2

        client, sdk, sleeps = make([httpx2.ReadTimeout("timed out"), reply(types.Part(text="ok"))])
        assert client.generate("", [UserMessage("q")]).model == "gemini-2.5-pro"
        assert sleeps == []
        assert [r["model"] for r in sdk.models.requests] == ["gemini-2.5-flash", "gemini-2.5-pro"]

    def test_no_retry_or_fallback_starts_after_the_budget(self) -> None:
        now = [0.0]
        overloaded = api_error(503, "UNAVAILABLE", "high demand")
        sdk = StubSdk([overloaded] * 4 + [reply(types.Part(text="ok"))])
        respond = sdk.models.generate_content

        def slow(*, model: str, contents: Any, config: Any) -> Any:
            now[0] += 10  # an overloaded model takes 10 s to say no
            return respond(model=model, contents=contents, config=config)

        sdk.models.generate_content = slow  # type: ignore[method-assign]

        def sleep(seconds: float) -> None:
            now[0] += seconds

        client = GeminiClient(
            "k",
            sdk_client=sdk,
            catalog=ModelCatalog(),
            sleep=sleep,
            budget_seconds=30,
            clock=lambda: now[0],
        )
        with pytest.raises(LLMError) as caught:
            client.generate("", [UserMessage("q")])
        assert caught.value.kind == "unavailable"
        # flash fails at 10 s, waits 5, fails again at 25 s (a 15 s wait would pass 30 s), then pro fails at 35 s.
        assert [r["model"] for r in sdk.models.requests] == [
            "gemini-2.5-flash",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
        ]
        assert now[0] == 35.0

    def test_the_request_timeout_is_configurable(self) -> None:
        client = GeminiClient("test-key", timeout_seconds=45)
        assert client._client._api_client._http_options.timeout == 45_000
        client.close()


class TestPerModelQuota:
    """Free-tier keys get a daily quota per model (seen live: 20 requests a day for gemini-3.8-flash)."""

    PER_MODEL: ClassVar[list[dict[str, Any]]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
        },
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "51635s"},
    ]
    PER_PROJECT: ClassVar[list[dict[str, Any]]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}],
        }
    ]

    def test_another_model_is_used_until_the_quota_resets(self) -> None:
        now = [0.0]
        catalog = ModelCatalog(clock=lambda: now[0])
        sdk = StubSdk(
            [
                api_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded", self.PER_MODEL),
                reply(types.Part(text="ok")),
                reply(types.Part(text="still ok")),
            ]
        )
        client = GeminiClient(
            "k", sdk_client=sdk, catalog=catalog, sleep=lambda _: None, clock=lambda: now[0]
        )
        assert client.generate("", [UserMessage("q")]).model == "gemini-2.5-pro"
        now[0] = 3 * 3600.0  # hours later the quota has not reset yet
        later = GeminiClient("k", sdk_client=sdk, catalog=catalog, clock=lambda: now[0])
        assert later.model == "gemini-2.5-pro"
        now[0] = 51_636.0
        assert GeminiClient("k", sdk_client=sdk, catalog=catalog).model == "gemini-2.5-flash"

    def test_a_project_wide_quota_stops(self) -> None:
        client, sdk, _ = make(
            [api_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded", self.PER_PROJECT)]
        )
        with pytest.raises(LLMError) as caught:
            client.generate("", [UserMessage("q")])
        assert caught.value.kind == "quota"
        assert caught.value.model_scoped is False
        assert len(sdk.models.requests) == 1


def test_a_model_that_fails_on_the_last_attempt_is_set_aside_too() -> None:
    """Seen live: the next request should not start with a model that just ran out of quota."""
    gone = api_error(404, "NOT_FOUND", "no longer available to new users")
    overloaded = api_error(504, "DEADLINE_EXCEEDED", "deadline")
    models = [
        types.Model(name=f"models/{name}", supported_actions=["generateContent"])
        for name in ("gemini-2.5-flash", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash")
    ]
    catalog = ModelCatalog()
    sdk = StubSdk([gone, overloaded, overloaded, reply(types.Part(text="ok"))], models)
    first = GeminiClient(
        "k",
        preferred_model="gemini-2.5-flash",
        sdk_client=sdk,
        catalog=catalog,
        sleep=lambda _: None,
    )
    with pytest.raises(LLMError):
        first.generate("", [UserMessage("q")])
    second = GeminiClient("k", preferred_model="gemini-2.5-flash", sdk_client=sdk, catalog=catalog)
    assert second.generate("", [UserMessage("q")]).model == "gemini-3.6-flash"
    assert [r["model"] for r in sdk.models.requests] == [
        "gemini-2.5-flash",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    ]
