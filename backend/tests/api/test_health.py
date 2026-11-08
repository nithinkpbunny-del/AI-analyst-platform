"""Health routes. They are public by design, so 401/403/404-cross-tenant cases do not apply to them."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.api.health import (
    ReadinessCheck,
    check_database,
    check_redis,
    check_storage,
    default_checks,
)
from app.config import Settings
from app.main import create_app


def _ok() -> None:
    return None


def _client(settings: Settings, checks: dict[str, ReadinessCheck] | None = None) -> TestClient:
    app = create_app(
        settings, readiness_checks=checks or {"database": _ok, "redis": _ok, "storage": _ok}
    )
    return TestClient(app)


def test_liveness(settings: Settings) -> None:
    response = _client(settings).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "analyst-api",
        "version": __version__,
        "env": "test",
    }


def test_readiness_all_ok(settings: Settings) -> None:
    response = _client(settings).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "storage": "ok"},
    }


def test_readiness_reports_failures_with_503_and_no_details(settings: Settings) -> None:
    def broken() -> None:
        raise RuntimeError("could not connect to postgresql://app:leakedpassword@db/x")

    response = _client(settings, {"database": broken, "redis": _ok, "storage": _ok}).get(
        "/health/ready"
    )
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {"database": "failed", "redis": "ok", "storage": "ok"}
    assert "leakedpassword" not in response.text


def test_readiness_failure_is_logged_without_the_secret(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> None:
        raise RuntimeError("could not connect to postgresql://app:leakedpassword@db/x")

    _client(settings, {"database": broken}).get("/health/ready")
    out = capsys.readouterr().out
    assert "readiness check failed: database" in out
    assert "leakedpassword" not in out


def test_request_id_is_generated_and_echoed(settings: Settings) -> None:
    client = _client(settings)
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 32
    assert (
        client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )


def test_overlong_request_id_is_truncated(settings: Settings) -> None:
    response = _client(settings).get("/health", headers={"X-Request-ID": "a" * 500})
    assert len(response.headers["x-request-id"]) == 64


def test_cors_allows_the_app_origin_only(settings: Settings) -> None:
    client = _client(settings)
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    other = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_unknown_route_is_404(settings: Settings) -> None:
    assert _client(settings).get("/does-not-exist").status_code == 404


def test_openapi_is_published(settings: Settings) -> None:
    paths = _client(settings).get("/openapi.json").json()["paths"]
    assert "/health" in paths and "/health/ready" in paths


def test_default_checks_cover_database_redis_and_storage(settings: Settings) -> None:
    assert set(default_checks(settings)) == {"database", "redis", "storage"}


@pytest.fixture
def dead_services(settings: Settings) -> Settings:
    """Settings pointing at a closed local port: the real check functions must fail fast, not hang."""
    return settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://app:pw@127.0.0.1:1/analyst",
            "redis_url": "redis://127.0.0.1:1/0",
            "s3_endpoint": "http://127.0.0.1:1",
            "health_check_timeout_seconds": 1.0,
        }
    )


@pytest.mark.parametrize("check", [check_database, check_redis, check_storage])
def test_real_checks_fail_quickly_when_the_service_is_down(
    dead_services: Settings, check: object
) -> None:
    started = time.monotonic()
    with pytest.raises(Exception):  # noqa: B017 - any connection error type is acceptable
        check(dead_services)  # type: ignore[operator]
    assert time.monotonic() - started < 10
