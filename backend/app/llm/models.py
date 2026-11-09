"""Choosing a model from what the key can actually use (BUILD_SPEC 7.2, pitfall 6).

Models are listed from the provider with the key at runtime and cached per key for an hour. The configured
preference (``GEMINI_DEFAULT_MODEL`` / ``CLAUDE_DEFAULT_MODEL``) wins when the key offers it; otherwise the newest
general-purpose model of the preferred family is used, so a retired or renamed model never breaks the workspace.

Providers sometimes keep listing a model that they no longer serve to a key (Google: "no longer available to new
users", HTTP 404), a single model can be overloaded while others answer ("high demand", 503), and Gemini's daily
quotas are counted per model. The clients then switch to the next best model and try again
(``MAX_MODEL_FALLBACKS``). ``ModelCatalog.exclude`` remembers the skipped model for that key: an hour for a
refused model, ``OVERLOAD_PAUSE_SECONDS`` for an overloaded one, and until the quota resets for a model whose
daily quota is used up.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable, Iterable

from app.llm.base import LLMError

# Specialised variants that cannot hold an analyst conversation with tools.
_SPECIAL = re.compile(
    r"(embed|aqa|tts|audio|image|imagen|veo|live|vision|learnlm|gemma|robotics|computer-use|"
    r"native|thinking-exp|-exp|nano|banana|deep-research)",
    re.IGNORECASE,
)
_UNSTABLE = re.compile(r"(preview|latest|experimental|-exp)", re.IGNORECASE)

MAX_MODEL_FALLBACKS = 2
OVERLOAD_PAUSE_SECONDS = 300.0
MAX_QUOTA_PAUSE_SECONDS = 86_400.0

# Families in order of preference when the configured model is not offered.
_FAMILIES = {
    "gemini": ("flash", "pro", "flash-lite"),
    "claude": ("opus", "sonnet", "haiku"),
}


def _version(model: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", model.split("@")[0])[:3])


def _family(provider: str, model: str) -> str | None:
    name = model.lower()
    if provider == "gemini":
        if "flash-lite" in name:
            return "flash-lite"
        for family in ("flash", "pro"):
            if f"-{family}" in name:
                return family
        return None
    for family in _FAMILIES["claude"]:
        if f"-{family}" in name:
            return family
    return None


def choose_model(provider: str, available: Iterable[str], preferred: str | None) -> str:
    models = [m.removeprefix("models/") for m in available]
    if preferred and preferred.removeprefix("models/") in models:
        return preferred.removeprefix("models/")
    usable = [m for m in models if not _SPECIAL.search(m)]
    if not usable:
        raise LLMError("model_not_found", provider, "the key offers no usable text model")
    for stable_only in (True, False):
        pool = [m for m in usable if not stable_only or not _UNSTABLE.search(m)]
        for family in _FAMILIES.get(provider, ()):
            candidates = [m for m in pool if _family(provider, m) == family]
            if candidates:
                return max(candidates, key=lambda m: (_version(m), -len(m)))
    return max(usable, key=lambda m: (_version(m), -len(m)))


class ModelCatalog:
    """Remembers each key's model list for ``ttl_seconds`` (keys are stored only as a hash)."""

    def __init__(self, ttl_seconds: float = 3600, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, list[str]]] = {}
        self._excluded: dict[str, dict[str, float]] = {}  # model -> excluded until (clock time)

    @staticmethod
    def _key(provider: str, api_key: str) -> str:
        return provider + ":" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def get(self, provider: str, api_key: str, load: Callable[[], list[str]]) -> list[str]:
        """The key's models, minus any it was refused recently."""
        key = self._key(provider, api_key)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            cached = entry[1] if entry and now - entry[0] < self._ttl else None
        if cached is None:
            cached = load()
            with self._lock:
                self._entries[key] = (now, cached)
        with self._lock:
            excluded = {
                model for model, until in self._excluded.get(key, {}).items() if now < until
            }
        return [model for model in cached if model not in excluded]

    def exclude(
        self, provider: str, api_key: str, model: str, seconds: float | None = None
    ) -> None:
        """Skip ``model`` for this key for ``seconds`` (default: the list's lifetime)."""
        until = self._clock() + (self._ttl if seconds is None else seconds)
        with self._lock:
            self._excluded.setdefault(self._key(provider, api_key), {})[model] = until

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._excluded.clear()


def fallback_pause(error: LLMError) -> float | None:
    """How long to skip a model after ``error`` before trying another one (``None``: the model list's
    lifetime). Re-raises ``error`` when another model would not help (invalid key, quota, ...)."""
    if error.kind == "model_not_found":
        return None
    if error.kind == "unavailable":
        return OVERLOAD_PAUSE_SECONDS
    if error.kind == "quota" and error.model_scoped:
        # This model's daily quota is used up; skip it until the provider says it resets.
        return min(error.retry_after or MAX_QUOTA_PAUSE_SECONDS, MAX_QUOTA_PAUSE_SECONDS)
    raise error


CATALOG = ModelCatalog()
