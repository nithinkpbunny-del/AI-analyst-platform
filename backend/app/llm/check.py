"""``make llm-check``: one real tool-calling round trip against the AI provider configured in ``.env``.

Opt-in and never run by tests or CI. It exercises the pitfalls of BUILD_SPEC 7.2 against the live API: the model
is chosen from the key's own model list, the model calls a tool, our result goes back with the model's original
turn replayed (Gemini thought signatures), and the model answers. Uses a few hundred tokens; writes nothing to the
database. The key itself is never printed.

    uv run python -m app.llm.check [--provider gemini|claude]
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.llm.base import (
    LLMError,
    Message,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
    close_client,
)
from app.llm.keys import default_factory

TOOL = ToolSpec(
    name="monthly_revenue",
    description="Revenue in rupees for one calendar month of the demo shop.",
    parameters={
        "type": "object",
        "properties": {"month": {"type": "string", "description": "Month as YYYY-MM"}},
        "required": ["month"],
    },
)
REVENUE = {"2026-09": 1_240_000, "2026-10": 1_675_000}
SYSTEM = (
    "You are a business analyst. Never do arithmetic yourself beyond comparing two numbers; "
    "always call the tool for figures. Reply in one short sentence."
)
QUESTION = "Use the tool to get revenue for 2026-09 and 2026-10, then say which month was higher."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["gemini", "claude"], default=None)
    args = parser.parse_args(argv)
    settings = get_settings()
    provider = args.provider or settings.llm_default_provider
    secret = settings.anthropic_api_key if provider == "claude" else settings.gemini_api_key
    key = secret.get_secret_value()
    if not key:
        name = "ANTHROPIC_API_KEY" if provider == "claude" else "GEMINI_API_KEY"
        print(f"{name} is empty in .env, so there is nothing to check.")
        return 2

    client = default_factory(settings)(provider, key)
    try:
        models = client.list_models()
        print(f"1. The key can use {len(models)} text models. First choice: {client.model}")
        messages: list[Message] = [UserMessage(QUESTION)]
        steps = 0
        while True:
            steps += 1
            response = client.generate(SYSTEM, messages, [TOOL], settings.llm_max_output_tokens)
            print(
                f"{steps + 1}. stop={response.stop_reason} tool_calls="
                f"{[f'{c.name}({c.input})' for c in response.tool_calls]} "
                f"tokens in/out={response.usage.input_tokens}/{response.usage.output_tokens}"
            )
            if not response.tool_calls or steps >= 4:
                break
            messages.append(response.as_message())
            results = [
                ToolResult(c.id, {"revenue_inr": REVENUE.get(str(c.input.get("month")), 0)})
                for c in response.tool_calls
            ]
            messages.append(ToolResultsMessage(results))
    except LLMError as error:
        print(f"The provider call failed: {error.kind} ({error.detail})")
        return 1
    finally:
        close_client(client)
    print(f"Answer ({response.model}): {response.text.strip()}")
    ok = steps >= 2 and response.stop_reason == "end_turn" and bool(response.text.strip())
    print("Round trip OK." if ok else "The model did not finish with a tool round trip; see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
