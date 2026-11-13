"""``make eval``: the golden questions against a real model (opt-in; BUILD_SPEC 12.2).

    cd backend && uv run python ../eval/run_eval.py [--provider gemini|claude] [--only id1,id2] [--limit N] [--list]

Uses the platform key in ``.env`` (``GEMINI_API_KEY`` or ``ANTHROPIC_API_KEY``) through the app's own LLM layer, so the
known Gemini pitfalls, retries on 429/503 with backoff (never on an exhausted daily quota or an invalid key) and the
model fallback all apply. Every call's tokens and estimated cost are counted and written into the report, which goes
to ``eval/reports/<date>.md``. The full set is about 48 questions and a few hundred model calls; on a free key, run a
few questions at a time with ``--only`` or ``--limit``. ``--list`` prints the questions and calls nothing.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path[:0] = [str(BACKEND), str(HERE)]

import harness  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm.base import LLMClient, LLMResponse  # noqa: E402
from app.llm.keys import default_factory  # noqa: E402
from app.llm.pricing import estimate_cost_usd  # noqa: E402


class Metered:
    """Wraps the real client: counts every call's tokens and estimated cost (no workspace to bill here)."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.provider = client.provider
        self.cost = Decimal(0)
        self.known_cost = True

    @property
    def model(self) -> str:
        return self.client.model

    def list_models(self) -> list[str]:
        return self.client.list_models()

    def generate(self, *args: object, **kwargs: object) -> LLMResponse:
        response = self.client.generate(*args, **kwargs)  # type: ignore[arg-type]
        cost = estimate_cost_usd(response.model, response.usage)
        if cost is None:
            self.known_cost = False
        else:
            self.cost += cost
        return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["gemini", "claude"], default=None)
    parser.add_argument("--only", default="", help="comma-separated question ids")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--list", action="store_true", help="print the questions; call nothing")
    args = parser.parse_args(argv)

    goldens = harness.load()
    if args.only:
        wanted = {i.strip() for i in args.only.split(",") if i.strip()}
        goldens = [g for g in goldens if g.id in wanted]
    if args.limit:
        goldens = goldens[: args.limit]
    if args.list:
        for g in goldens:
            print(f"{g.id:42} {g.language}  {g.ask}")
        return 0
    if not goldens:
        print("No questions selected.")
        return 1

    settings = get_settings()
    provider = args.provider or settings.llm_default_provider
    secret = settings.anthropic_api_key if provider == "claude" else settings.gemini_api_key
    key = secret.get_secret_value()
    if not key:
        name = "ANTHROPIC_API_KEY" if provider == "claude" else "GEMINI_API_KEY"
        print(f"{name} is empty in .env: the evaluation needs a real key. Nothing was called.")
        return 2
    client = Metered(default_factory(settings)(provider, key))

    from tests.analytics_data import agent_context

    print(f"Running {len(goldens)} questions on {provider}...")
    outcomes = []
    for golden in goldens:
        outcome = harness.run_one(
            golden, agent_context, lambda _g, _c: client, max_steps=settings.agent_max_steps
        )
        outcomes.append(outcome)
        print(
            f"  {golden.id:42} {'pass' if outcome.score.passed else 'FAIL'}  ({outcome.calls} calls)"
        )
    now = datetime.now(UTC)
    text = harness.report(
        outcomes,
        mode="real",
        model=f"{provider}: {client.model}",
        cost_usd=float(client.cost) if client.known_cost else None,
        when=now.strftime("%Y-%m-%d %H:%M UTC"),
    )
    out = HERE / "reports" / f"{now:%Y-%m-%d-%H%M}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    rate = harness.pass_rate(outcomes)
    print(f"Passed {rate:.1f}% (Phase 1 needs {harness.PASS_MARK:.0f}%). Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
