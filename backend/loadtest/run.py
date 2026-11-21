"""Load test: many people ask questions at the same time (M19, BUILD_SPEC 14 "50 concurrent questions").

    make loadtest                       # 50 people, one question each, ramped over 10 s
    make loadtest USERS=20 RAMP=10      # smaller
    cd backend && uv run python -m loadtest.run --help

**FAKE model only.** The AI factory here builds ``PacedFakeLLM`` clients and nothing else; the settings are built in
code (``.env`` is never read) with an obviously fake platform key, and the factory refuses any other key. No
Gemini or Claude client is imported or created.

What runs for real: the FastAPI app under uvicorn (one process, like ``make dev``), PostgreSQL 16 with the
migrations, RLS and the restricted ``app_tenant`` role, Redis (rate limits, dataset-fetch locks, answer reuse), the
upload -> snapshot -> dictionary path, and the whole agent pipeline (tools, DuckDB workspace, number check,
provenance, metering, billing warnings, activity log). Background jobs run in a pool of worker threads with their
own database pool (``--workers``, like Celery's worker processes). Uploaded files and snapshots stay in memory, and
emails are kept in memory (no S3 and no SMTP).

Each simulated person asks a question (``POST /conversations``) and then does what the web app does: polls
``GET /conversations/{id}`` every second until the answer is done or failed.

Safe for a fanless laptop: people start one by one over ``--ramp`` seconds, few worker threads by default, DuckDB
is limited to 2 threads per question, the run stops at ``--max-seconds``, and Ctrl+C stops everything (server,
workers and the throwaway containers) at once.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import secrets
import socket
import statistics
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import redis
import uvicorn
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import NullPool, QueuePool

from alembic import command
from app.agent import service as agent_service
from app.auth import passwords
from app.auth.tokens import issue_access_token
from app.config import Settings
from app.connectors.registry import ConnectorRuntime
from app.data.objects import MemoryObjectStore
from app.db.session import make_engine, make_session_factory
from app.email.sender import MemoryEmailSender
from app.llm.base import (
    LLMResponse,
    Message,
    ToolCall,
    ToolResultsMessage,
    ToolSpec,
    Usage,
)
from app.main import create_app
from app.models import Membership, Organization, Session, TwoFactor, User
from app.tasks.queue import InlineJobQueue

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# The only key the fake factory accepts. Not a real key; it never leaves this process.
FAKE_KEY = "loadtest-fake-key-not-a-real-key"
FAKE_MODEL = "gemini-2.5-flash"
JANUARY = {"start": "2026-01-01", "end": "2026-01-31"}


# ------------------------------------------------------------------------------------------- the data
def orders_csv(rows: int = 40) -> bytes:
    """A small orders table (the same one the API tests use). January's total is ``EXPECTED_TOTAL``."""
    lines = ["id,created_at,customer_id,sales_channel,total_price,currency"]
    for i in range(rows):
        lines.append(
            f"{1000 + i},2026-01-{1 + i % 28:02d}T10:00:00+05:30,{7000 + i % 5},"
            f"{'App' if i % 2 else 'Website'},{100 + i * 3}.5,INR"
        )
    return ("\n".join(lines) + "\n").encode()


EXPECTED_TOTAL = "₹6,360"  # sum(100 + 3i + 0.5 for i < 40)


# ----------------------------------------------------------------------------------------- the fake AI
@dataclass
class FakeStats:
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def count(self) -> None:
        with self.lock:
            self.calls += 1


class PacedFakeLLM:
    """A scripted model that takes a while to answer, like a real one (``delay`` seconds, +/- 50%).

    For a question (``give_answer`` among the tools): first a ``kpi_summary`` call, then the answer citing it.
    For anything else (the dictionary draft): a reply the caller cannot use, so it falls back to the profile."""

    def __init__(self, provider: str, delay: float, stats: FakeStats) -> None:
        self.provider = provider
        self._delay = delay
        self._stats = stats

    @property
    def model(self) -> str:
        return FAKE_MODEL

    def list_models(self) -> list[str]:
        return [FAKE_MODEL]

    def _response(self, text_blocks: list[str], calls: list[ToolCall]) -> LLMResponse:
        return LLMResponse(
            text_blocks=text_blocks,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=Usage(input_tokens=1800, output_tokens=120),
            provider=self.provider,
            model=FAKE_MODEL,
            raw={"fake": True},
        )

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = 3000,
    ) -> LLMResponse:
        self._stats.count()
        if self._delay > 0:
            time.sleep(self._delay * random.uniform(0.5, 1.5))  # noqa: S311 (pacing, not security)
        names = {t.name for t in tools}
        if "give_answer" not in names:
            return self._response(["OK"], [])
        if "kpi_summary" in names and not any(isinstance(m, ToolResultsMessage) for m in messages):
            call = ToolCall(
                "c1",
                "kpi_summary",
                {"metrics": ["sum_total_price"], "period": JANUARY, "compare": "none"},
            )
            return self._response([], [call])
        answer = ToolCall(
            "c2",
            "give_answer",
            {
                "lead": f"Sales in January were {EXPECTED_TOTAL} [r1].",
                "key_figure": EXPECTED_TOTAL,
                "evidence": ["Both channels sold in January"],
                "next_questions": ["How did February compare?"],
            },
        )
        return self._response([], [answer])


class FakeOnlyFactory:
    """The load test's ``LLMFactory``: fake clients only, and only for the fake key."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.stats = FakeStats()

    def __call__(self, provider: str, api_key: str) -> PacedFakeLLM:
        if api_key != FAKE_KEY:
            raise RuntimeError("The load test only uses the fake AI client.")
        return PacedFakeLLM(provider, self.delay, self.stats)


# ------------------------------------------------------------------------------------------ the workers
class WorkerPool(InlineJobQueue):
    """Background jobs like the Celery worker: questions go to ``workers`` threads with their own database pool;
    the set-up jobs (file sync, dictionary draft) run at once, as in the API tests."""

    def __init__(self, runtime: ConnectorRuntime, factory: FakeOnlyFactory, workers: int) -> None:
        super().__init__(runtime, factory)
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="worker")
        self.futures: list[Future[str]] = []
        self.accepting = True

    def answer_question(self, asked: agent_service.Asked) -> None:
        if not self.accepting:
            return
        self.futures.append(
            self.executor.submit(
                agent_service.answer_question, self._runtime, self._factory(), asked
            )
        )

    def _factory(self) -> FakeOnlyFactory:
        factory = self._llm_factory
        assert isinstance(factory, FakeOnlyFactory)
        return factory

    @property
    def waiting(self) -> int:
        return sum(1 for f in self.futures if not f.done())

    def stop(self) -> None:
        self.accepting = False
        self.executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------------------- environment
def settings_for(database_url: str, redis_url: str, args: argparse.Namespace) -> Settings:
    """Built in code; ``.env`` is never read. Production defaults for every limit the run touches."""
    pool: dict[str, Any] = {}
    if args.db_pool_size is not None:
        pool["db_pool_size"] = args.db_pool_size
    if args.db_max_overflow is not None:
        pool["db_max_overflow"] = args.db_max_overflow
    return Settings(
        _env_file=None,
        **pool,
        app_env="test",
        log_level="WARNING",
        database_url=database_url,
        redis_url=redis_url,
        # Fresh random keys for each run: nothing from .env, nothing reusable.
        master_key=secrets.token_urlsafe(32),
        jwt_secret=secrets.token_urlsafe(32),
        access_token_minutes=120,
        gemini_api_key=FAKE_KEY,
        anthropic_api_key="",
        llm_default_provider="gemini",
    )


@contextmanager
def services(args: argparse.Namespace) -> Iterator[tuple[str, str]]:
    """PostgreSQL 16 and Redis 7: given URLs, or throwaway containers that are always removed afterwards."""
    if args.database_url and args.redis_url:
        yield args.database_url, args.redis_url
        return
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.core.container import DockerContainer

    postgres = PostgresContainer("postgres:16-alpine", driver="psycopg")
    cache = DockerContainer("redis:7-alpine").with_exposed_ports(6379)
    started: list[Any] = []
    try:
        print("Starting PostgreSQL and Redis containers (throwaway) ...", flush=True)
        postgres.start()
        started.append(postgres)
        cache.start()
        started.append(cache)
        redis_url = f"redis://{cache.get_container_host_ip()}:{cache.get_exposed_port(6379)}/0"
        _wait_for_redis(redis_url)
        yield postgres.get_connection_url(), redis_url
    finally:
        for container in reversed(started):
            try:
                container.stop()
            except Exception as error:  # pragma: no cover - best effort clean-up
                print(f"Could not stop a container: {error}", file=sys.stderr)


def _wait_for_redis(url: str, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    client = redis.Redis.from_url(url, socket_connect_timeout=1)
    while True:
        try:
            if client.ping():
                return
        except redis.RedisError:
            if time.monotonic() > deadline:
                raise
        time.sleep(0.2)


def migrate(database_url: str) -> None:
    config = Config()  # no ini file: same migrations, without the per-step log lines
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("path_separator", "os")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class ApiServer:
    """uvicorn in a background thread (no signal handlers: Ctrl+C belongs to the load test)."""

    def __init__(self, app: Any, port: int) -> None:
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="api", daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("The API did not start.")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


# ---------------------------------------------------------------------------------------------- people
@dataclass(frozen=True)
class Person:
    index: int
    org_id: uuid.UUID
    token: str


@dataclass(frozen=True)
class Workspace:
    org_id: uuid.UUID
    owner_token: str
    people: list[Person]


def _token(
    db: DbSession, settings: Settings, user: User, org_id: uuid.UUID, role: str, now: datetime
) -> str:
    session = Session(
        user_id=user.id,
        org_id=org_id,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_max_hours),
    )
    db.add(session)
    db.flush()
    token, _ = issue_access_token(
        secret=settings.jwt_secret.get_secret_value(),
        user_id=user.id,
        org_id=org_id,
        role=role,
        session_id=session.id,
        now=now,
        lifetime=timedelta(minutes=settings.access_token_minutes),
    )
    return token


def seed(engine: Engine, settings: Settings, users: int, per_org: int) -> list[Workspace]:
    """Workspaces of ``per_org`` analysts plus one Owner each, signed in directly (no sign-in screens)."""
    now = datetime.now(UTC)
    hashed = passwords.hash_password("loadtest-password-not-used")
    out: list[Workspace] = []
    factory = make_session_factory(engine)
    with factory() as db:
        for w in range(math.ceil(users / per_org)):
            org = Organization(name=f"Load test {w + 1}", monthly_question_limit=100_000)
            db.add(org)
            db.flush()
            owner = User(
                email=f"owner{w}@loadtest.invalid",
                name=f"Owner {w}",
                password_hash=hashed,
                email_verified_at=now,
            )
            db.add(owner)
            db.flush()
            db.add(Membership(org_id=org.id, user_id=owner.id, role="owner", status="active"))
            db.add(
                TwoFactor(user_id=owner.id, method="email", recovery_codes_hash=[], enabled_at=now)
            )
            owner_token = _token(db, settings, owner, org.id, "owner", now)
            people: list[Person] = []
            for p in range(min(per_org, users - w * per_org)):
                index = w * per_org + p
                person = User(
                    email=f"analyst{index}@loadtest.invalid",
                    name=f"Analyst {index}",
                    password_hash=hashed,
                    email_verified_at=now,
                )
                db.add(person)
                db.flush()
                db.add(
                    Membership(org_id=org.id, user_id=person.id, role="analyst", status="active")
                )
                people.append(
                    Person(index, org.id, _token(db, settings, person, org.id, "analyst", now))
                )
            out.append(Workspace(org.id, owner_token, people))
        db.commit()
    return out


# --------------------------------------------------------------------------------------------- results
@dataclass
class Results:
    ask_ms: list[float] = field(default_factory=list)
    poll_ms: list[float] = field(default_factory=list)
    answer_s: list[float] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)
    statuses: Counter[str] = field(default_factory=Counter)
    max_db_api: int = 0
    max_db_workers: int = 0
    max_pg_connections: int = 0
    max_waiting_jobs: int = 0
    started: float = field(default_factory=time.perf_counter)
    finished: float = 0.0


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, math.ceil(p / 100 * len(ordered)) - 1))
    return ordered[k]


def _checked_out(engine: Engine) -> int:
    pool = engine.pool
    return pool.checkedout() if isinstance(pool, QueuePool) else 0


def monitor(
    results: Results,
    api_engine: Engine,
    worker_engine: Engine,
    database_url: str,
    queue: WorkerPool,
    stop: threading.Event,
) -> None:
    """Samples, twice a second: connections in use per pool, all Postgres connections, jobs waiting."""
    admin = create_engine(database_url, poolclass=NullPool)
    try:
        while not stop.is_set():
            results.max_db_api = max(results.max_db_api, _checked_out(api_engine))
            results.max_db_workers = max(results.max_db_workers, _checked_out(worker_engine))
            results.max_waiting_jobs = max(results.max_waiting_jobs, queue.waiting)
            try:
                with admin.connect() as conn:
                    count = conn.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                        )
                    )
                results.max_pg_connections = max(results.max_pg_connections, int(count or 0) - 1)
            except Exception:  # noqa: S110 - sampling only; the run reports errors elsewhere
                pass
            stop.wait(0.5)
    finally:
        admin.dispose()


async def person_asks(
    client: httpx.AsyncClient,
    person: Person,
    *,
    start_after: float,
    rounds: int,
    poll_seconds: float,
    answer_timeout: float,
    results: Results,
) -> None:
    await asyncio.sleep(start_after)
    headers = {"Authorization": f"Bearer {person.token}"}
    for round_no in range(rounds):
        question = (
            f"How much did we sell in January? (person {person.index}, question {round_no + 1})"
        )
        asked = time.perf_counter()
        try:
            response = await client.post(
                "/conversations", json={"question": question}, headers=headers
            )
        except httpx.HTTPError as error:
            results.errors[f"ask: {type(error).__name__}"] += 1
            continue
        results.ask_ms.append((time.perf_counter() - asked) * 1000)
        if response.status_code != 201:
            results.errors[f"ask: HTTP {response.status_code}"] += 1
            continue
        body = response.json()
        conversation_id = body["id"]
        status = body["messages"][-1]["status"]
        error_code = body["messages"][-1].get("error_code")
        while status in ("queued", "running"):
            if time.perf_counter() - asked > answer_timeout:
                status = "timeout"
                break
            await asyncio.sleep(poll_seconds)
            polled = time.perf_counter()
            try:
                response = await client.get(f"/conversations/{conversation_id}", headers=headers)
            except httpx.HTTPError as error:
                results.errors[f"poll: {type(error).__name__}"] += 1
                continue
            results.poll_ms.append((time.perf_counter() - polled) * 1000)
            if response.status_code != 200:
                results.errors[f"poll: HTTP {response.status_code}"] += 1
                continue
            last = response.json()["messages"][-1]
            status, error_code = last["status"], last.get("error_code")
        results.statuses[status] += 1
        if status == "done":
            results.answer_s.append(time.perf_counter() - asked)
        else:
            results.errors[f"answer: {status}{f' ({error_code})' if error_code else ''}"] += 1


async def upload_data(client: httpx.AsyncClient, workspace: Workspace) -> None:
    response = await client.post(
        "/connections/files",
        headers={"Authorization": f"Bearer {workspace.owner_token}"},
        files={"file": ("orders.csv", orders_csv(), "text/csv")},
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed: HTTP {response.status_code} {response.text[:200]}")


async def drive(
    base_url: str, workspaces: list[Workspace], args: argparse.Namespace, results: Results
) -> None:
    limits = httpx.Limits(max_connections=args.users + 10, max_keepalive_connections=args.users)
    timeout = httpx.Timeout(args.request_timeout)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=timeout) as client:
        print(f"Uploading demo orders to {len(workspaces)} workspace(s) ...", flush=True)
        for workspace in workspaces:
            await upload_data(client, workspace)
        people = [p for w in workspaces for p in w.people]
        gap = args.ramp / max(1, len(people) - 1) if len(people) > 1 else 0.0
        print(
            f"{len(people)} people start asking over {args.ramp:.0f} s "
            f"({args.rounds} question(s) each). Ctrl+C stops at any time.",
            flush=True,
        )
        results.started = time.perf_counter()
        await asyncio.gather(
            *(
                person_asks(
                    client,
                    person,
                    start_after=i * gap,
                    rounds=args.rounds,
                    poll_seconds=args.poll,
                    answer_timeout=args.answer_timeout,
                    results=results,
                )
                for i, person in enumerate(people)
            )
        )
        results.finished = time.perf_counter()


# ---------------------------------------------------------------------------------------------- report
def report(results: Results, args: argparse.Namespace, fake: FakeStats, stopped: str) -> str:
    total = sum(results.statuses.values())
    elapsed = (results.finished or time.perf_counter()) - results.started

    def line(name: str, values: list[float], unit: str) -> str:
        if not values:
            return f"| {name} | 0 | - | - | - | - |"
        return (
            f"| {name} | {len(values)} | {pct(values, 50):.0f} {unit} | {pct(values, 95):.0f} {unit} "
            f"| {max(values):.0f} {unit} | {statistics.mean(values):.0f} {unit} |"
        )

    def line_s(name: str, values: list[float]) -> str:
        if not values:
            return f"| {name} | 0 | - | - | - | - |"
        return (
            f"| {name} | {len(values)} | {pct(values, 50):.1f} s | {pct(values, 95):.1f} s "
            f"| {max(values):.1f} s | {statistics.mean(values):.1f} s |"
        )

    errors = "\n".join(f"- {k}: {v}" for k, v in results.errors.most_common()) or "- none"
    verdict = "PASS" if not results.errors and results.statuses.get("done", 0) == total else "FAIL"
    return f"""# Load test, {datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}: {verdict}

FAKE model only ({fake.calls} fake calls, none to a real provider).
{args.users} people, {args.rounds} question(s) each, ramped over {args.ramp:.0f} s; {args.workers} worker thread(s);
fake model delay {args.llm_delay:.2f} s per call (+/- 50%); polling every {args.poll:.1f} s. {stopped}

| What | Count | p50 | p95 | Max | Mean |
|---|---|---|---|---|---|
{line("Ask (POST /conversations)", results.ask_ms, "ms")}
{line("Poll (GET /conversations/id)", results.poll_ms, "ms")}
{line_s("Question to finished answer", results.answer_s)}

- Answers: {dict(results.statuses)} of {args.users * args.rounds} asked, in {elapsed:.1f} s
  ({(results.statuses.get("done", 0) / elapsed * 60 if elapsed > 0 else 0):.1f} answers a minute).
- Most database connections in use: API pool {results.max_db_api}, worker pool {results.max_db_workers};
  all connections to the database {results.max_pg_connections}. Most questions queued or being answered at once:
  {results.max_waiting_jobs}.

Errors:
{errors}
"""


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="loadtest", description="Concurrent questions, FAKE model only (M19)."
    )
    parser.add_argument("--users", type=int, default=50, help="people asking at once (max 200)")
    parser.add_argument("--rounds", type=int, default=1, help="questions per person (max 5)")
    parser.add_argument("--per-org", type=int, default=10, help="people per workspace")
    parser.add_argument("--ramp", type=float, default=10.0, help="seconds over which people start")
    parser.add_argument("--workers", type=int, default=4, help="background worker threads (max 16)")
    parser.add_argument("--llm-delay", type=float, default=0.8, help="fake model seconds per call")
    parser.add_argument(
        "--poll", type=float, default=1.0, help="seconds between polls (web app: 1)"
    )
    parser.add_argument("--answer-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--max-seconds", type=float, default=300.0, help="hard stop for the run")
    parser.add_argument("--db-pool-size", type=int, default=None, help="override DB_POOL_SIZE")
    parser.add_argument(
        "--db-max-overflow", type=int, default=None, help="override DB_MAX_OVERFLOW"
    )
    parser.add_argument("--database-url", default="", help="use this database (default: container)")
    parser.add_argument("--redis-url", default="", help="use this Redis (default: container)")
    parser.add_argument("--no-report-file", action="store_true", help="print only")
    args = parser.parse_args(argv)
    if not 1 <= args.users <= 200 or not 1 <= args.rounds <= 5 or not 1 <= args.workers <= 16:
        parser.error("keep it laptop-sized: --users 1-200, --rounds 1-5, --workers 1-16")
    if bool(args.database_url) != bool(args.redis_url):
        parser.error("give both --database-url and --redis-url, or neither")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    # Production password hashing costs ~50 ms per hash; the seeded accounts never sign in with a password.
    passwords.use_hasher(PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))
    results = Results()
    fake = FakeOnlyFactory(args.llm_delay)
    stopped = ""
    with services(args) as (database_url, redis_url):
        settings = settings_for(database_url, redis_url, args)
        print("Applying migrations ...", flush=True)
        migrate(database_url)
        api_engine = make_engine(
            database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout_seconds=settings.db_pool_timeout_seconds,
        )
        worker_engine = make_engine(
            database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout_seconds=settings.db_pool_timeout_seconds,
        )
        redis_client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=10)
        objects = MemoryObjectStore()
        outbox = MemoryEmailSender()
        app = create_app(
            settings,
            readiness_checks={},
            session_factory=make_session_factory(api_engine),
            redis_client=redis_client,
            email_sender=outbox,
            llm_factory=fake,
            object_store=objects,
            inline_jobs=True,
        )
        api_runtime: ConnectorRuntime = app.state.connector_runtime
        worker_runtime = ConnectorRuntime(
            settings=settings,
            session_factory=make_session_factory(worker_engine),
            objects=objects,
            redis=redis_client,
            secrets=api_runtime.secrets,
            google=api_runtime.google,
            razorpay=api_runtime.razorpay,
            shopify=api_runtime.shopify,
            zoho=api_runtime.zoho,
            database=api_runtime.database,
            clock=api_runtime.clock,
            email=outbox,
        )
        queue = WorkerPool(worker_runtime, fake, args.workers)
        app.state.job_queue = queue
        workspaces = seed(api_engine, settings, args.users, args.per_org)
        port = free_port()
        server = ApiServer(app, port)
        stop_monitor = threading.Event()
        watcher = threading.Thread(
            target=monitor,
            args=(results, api_engine, worker_engine, database_url, queue, stop_monitor),
            daemon=True,
        )
        server.start()
        watcher.start()
        try:
            asyncio.run(
                asyncio.wait_for(
                    drive(f"http://127.0.0.1:{port}", workspaces, args, results),
                    timeout=args.max_seconds,
                )
            )
        except TimeoutError:
            stopped = f"**Stopped at the {args.max_seconds:.0f} s limit.**"
        except KeyboardInterrupt:
            stopped = "**Stopped with Ctrl+C.**"
        finally:
            queue.stop()
            stop_monitor.set()
            server.stop()
            watcher.join(timeout=5)
            api_engine.dispose()
            worker_engine.dispose()
            redis_client.close()
    text_report = report(results, args, fake.stats, stopped)
    print("\n" + text_report)
    if not args.no_report_file:
        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text(text_report, encoding="utf-8")
        print(f"Report saved to {path}")
    clean = not results.errors and not stopped
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
