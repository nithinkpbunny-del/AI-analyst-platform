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


# --------------------------------------------------------------------------------------------- database & app
# Database tests run against a real Postgres 16 (citext, triggers and, later, RLS behave as in production):
# TEST_DATABASE_URL if set, otherwise a throwaway testcontainers instance (Docker must be running).

from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import fakeredis  # noqa: E402
from alembic.config import Config  # noqa: E402
from argon2 import PasswordHasher  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from alembic import command  # noqa: E402
from app.auth import passwords  # noqa: E402
from app.connectors.google_sheets import GoogleSheetsSetup  # noqa: E402
from app.connectors.registry import ConnectorRuntime  # noqa: E402
from app.data.objects import MemoryObjectStore  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import make_engine, make_session_factory  # noqa: E402
from app.email.sender import MemoryEmailSender  # noqa: E402
from app.llm.fake import FakeLLMFactory  # noqa: E402
from app.llm.models import CATALOG  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security.secrets import SecretStore, key_wrapper  # noqa: E402
from tests.recorded_google import RecordedGoogle, recorded_setup  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session", autouse=True)
def fast_password_hashing() -> Iterator[None]:
    """Production argon2 parameters cost ~50 ms per hash; tests use cheap ones (same algorithm)."""
    passwords.use_hasher(PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))
    yield
    passwords.use_hasher(PasswordHasher())


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        yield configured
        return
    from testcontainers.community.postgres import PostgresContainer

    try:
        container = PostgresContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception as error:  # pragma: no cover - only when Docker is missing
        pytest.fail(
            f"Database tests need Docker (or TEST_DATABASE_URL). Start Docker Desktop. ({error})"
        )
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_engine(database_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config(database_url), "head")
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def _truncate_all(engine: Engine) -> None:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        # Replica mode skips the append-only trigger on audit_log; only the test superuser can do this.
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def session_factory(migrated_engine: Engine) -> Iterator[sessionmaker[Session]]:
    _truncate_all(migrated_engine)
    yield make_session_factory(migrated_engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    yield session
    session.rollback()
    session.close()


@dataclass
class FakeClock:
    """A clock tests can move. Starts at a fixed UTC moment."""

    now: datetime = field(default_factory=lambda: datetime(2026, 11, 9, 9, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def outbox() -> MemoryEmailSender:
    return MemoryEmailSender()


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def llm_factory() -> Iterator[FakeLLMFactory]:
    """Every AI provider the app talks to in tests (no real LLM calls, BUILD_SPEC 7.9)."""
    CATALOG.clear()
    yield FakeLLMFactory()
    CATALOG.clear()


@pytest.fixture
def object_store() -> MemoryObjectStore:
    """Uploaded files and snapshots, in memory (no S3 in tests)."""
    return MemoryObjectStore()


@pytest.fixture
def google_transport() -> RecordedGoogle:
    """Google's OAuth, Sheets and Drive APIs, answered from recorded fixtures (no live calls)."""
    return RecordedGoogle()


@pytest.fixture
def google_setup(google_transport: RecordedGoogle) -> GoogleSheetsSetup:
    return recorded_setup(google_transport)


@pytest.fixture
def runtime(
    settings: Settings,
    session_factory: sessionmaker[Session],
    fake_redis: fakeredis.FakeRedis,
    object_store: MemoryObjectStore,
    google_setup: GoogleSheetsSetup,
    clock: FakeClock,
    outbox: MemoryEmailSender,
) -> ConnectorRuntime:
    """What connectors, the cache and background jobs use, outside the API."""
    return ConnectorRuntime(
        settings=settings,
        session_factory=session_factory,
        objects=object_store,
        redis=fake_redis,
        secrets=SecretStore(key_wrapper(settings)),
        google=google_setup,
        clock=clock,
        email=outbox,
    )


@pytest.fixture
def client(
    settings: Settings,
    session_factory: sessionmaker[Session],
    fake_redis: fakeredis.FakeRedis,
    outbox: MemoryEmailSender,
    clock: FakeClock,
    llm_factory: FakeLLMFactory,
    object_store: MemoryObjectStore,
    google_setup: GoogleSheetsSetup,
) -> Iterator[TestClient]:
    app = create_app(
        settings,
        readiness_checks={},
        session_factory=session_factory,
        redis_client=fake_redis,
        email_sender=outbox,
        clock=clock,
        llm_factory=llm_factory,
        object_store=object_store,
        google_sheets=google_setup,
        inline_jobs=True,  # background jobs run at once, in the test
    )
    with TestClient(app, base_url="http://localhost:8000") as test_client:
        yield test_client
