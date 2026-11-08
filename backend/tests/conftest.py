"""Shared test setup. Tests never call real services or real LLMs (BUILD_SPEC section 0, rules 6-7)."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Make a local .env (if present) irrelevant to the environment name; real env vars beat the .env file.
os.environ["APP_ENV"] = "test"

# eval/generate_fixtures.py lives outside the backend package; tests import it by name.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings that ignore any local .env file and contain only obviously fake secrets."""
    return Settings(
        _env_file=None,
        app_env="test",
        app_base_url="http://localhost:5173",
        site_base_url="http://localhost:4321",
        master_key="test-master-key-0000000000000000000000",
        jwt_secret="test-jwt-secret-0000000000000000000000",
        gemini_api_key="test-gemini-key-not-real",
    )


@pytest.fixture(autouse=True)
def restore_root_logging() -> Iterator[None]:
    """``configure_logging`` replaces root handlers; undo that so tests do not affect each other."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
