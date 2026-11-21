"""M19 hardening: security headers on every response, the caller's address behind a load balancer, and no API docs
in production (docs/SECURITY.md)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth.deps import client_ip
from app.config import Settings
from app.main import create_app, security_headers


def _app(settings: Settings, **overrides: Any) -> TestClient:
    app = create_app(
        settings.model_copy(update=overrides),
        readiness_checks={},
        session_factory=None,
    )
    return TestClient(app, base_url="http://localhost:8000")


def _request(client: str, forwarded: list[str]) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (client, 1234)}
    )


class TestHeaders:
    def test_every_response_carries_the_security_headers_and_no_store(
        self, client: TestClient
    ) -> None:
        for response in (
            client.get("/health"),
            client.get("/auth/me"),  # 401
            client.get("/no-such-route"),  # 404
        ):
            headers = response.headers
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["x-frame-options"] == "DENY"
            assert headers["referrer-policy"] == "no-referrer"
            assert "frame-ancestors 'none'" in headers["content-security-policy"]
            assert "default-src 'none'" in headers["content-security-policy"]
            assert headers["cache-control"] == "no-store"
            assert headers["cross-origin-opener-policy"] == "same-origin"

    def test_hsts_only_over_https(self, settings: Settings) -> None:
        local = security_headers(settings)
        assert "Strict-Transport-Security" not in local
        https = security_headers(settings.model_copy(update={"api_base_url": "https://api.x.in"}))
        assert https["Strict-Transport-Security"].startswith("max-age=31536000")

    def test_forms_may_post_only_to_the_api_and_the_app(self, settings: Settings) -> None:
        csp = security_headers(settings)["Content-Security-Policy"]
        assert "form-action 'self' http://localhost:8000 http://localhost:5173;" in csp
        assert "script-src" not in csp  # no scripts at all from API pages


class TestDocs:
    def test_docs_are_served_in_development(self, settings: Settings) -> None:
        with _app(settings, app_env="development") as c:
            assert c.get("/openapi.json").status_code == 200
            docs = c.get("/docs")
            assert docs.status_code == 200
            assert "content-security-policy" not in docs.headers  # Swagger needs its CDN

    def test_docs_are_not_published_in_production(self, settings: Settings) -> None:
        with _app(settings, app_env="production") as c:
            assert c.get("/docs").status_code == 404
            assert c.get("/openapi.json").status_code == 404


class TestClientAddress:
    def test_without_trusted_proxies_forwarding_headers_are_ignored(self) -> None:
        assert client_ip(_request("10.0.0.5", ["1.2.3.4"]), 0) == "10.0.0.5"

    def test_behind_one_load_balancer_the_right_most_entry_is_the_caller(self) -> None:
        # The caller wrote "6.6.6.6" themselves; the load balancer appended the address it really saw.
        assert client_ip(_request("10.0.0.5", ["6.6.6.6, 203.0.113.9"]), 1) == "203.0.113.9"
        assert client_ip(_request("10.0.0.5", ["6.6.6.6", "203.0.113.9"]), 1) == "203.0.113.9"

    def test_two_hops(self) -> None:
        request = _request("10.0.0.5", ["6.6.6.6, 203.0.113.9, 10.0.1.1"])
        assert client_ip(request, 2) == "203.0.113.9"

    @pytest.mark.parametrize("forwarded", [[], ["not-an-ip"], ["  "]])
    def test_missing_or_malformed_falls_back_to_the_connecting_address(
        self, forwarded: list[str]
    ) -> None:
        assert client_ip(_request("10.0.0.5", forwarded), 1) == "10.0.0.5"

    def test_ipv6_is_normalised(self) -> None:
        assert client_ip(_request("10.0.0.5", ["2001:DB8::1"]), 1) == "2001:db8::1"

    def test_the_activity_log_and_rate_limits_use_the_forwarded_address(
        self, client: TestClient
    ) -> None:
        settings: Settings = client.app.state.settings  # type: ignore[attr-defined]
        client.app.state.settings = settings.model_copy(update={"trusted_proxy_hops": 1})  # type: ignore[attr-defined]
        try:
            from app.auth.deps import request_meta

            meta = request_meta(
                Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/",
                        "headers": [(b"x-forwarded-for", b"198.51.100.7")],
                        "client": ("10.0.0.5", 1),
                        "app": client.app,
                    }
                )
            )
            assert meta.ip == "198.51.100.7"
        finally:
            client.app.state.settings = settings  # type: ignore[attr-defined]


class TestBodyLimit:
    def test_a_huge_json_body_is_refused_before_it_is_read(self, client: TestClient) -> None:
        from app.security.body_limit import DEFAULT_MAX_BYTES

        response = client.post(
            "/auth/login",
            content=b"{" + b" " * (DEFAULT_MAX_BYTES + 10) + b"}",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_a_body_without_a_length_is_counted(self, client: TestClient) -> None:
        from app.security.body_limit import DEFAULT_MAX_BYTES

        def chunks() -> Any:
            for _ in range(DEFAULT_MAX_BYTES // 65536 + 2):
                yield b" " * 65536

        response = client.post(
            "/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413

    def test_normal_bodies_pass(self, client: TestClient) -> None:
        response = client.post("/auth/login", json={"email": "a@b.in", "password": "x" * 12})
        assert response.status_code != 413

    def test_limits_per_route(self) -> None:
        from app.security.body_limit import DEFAULT_MAX_BYTES, MULTIPART_OVERHEAD, limit_for

        assert limit_for("/connections/files", upload_max_mb=50, bridge_max_bytes=10) == (
            50 * 1024 * 1024 + MULTIPART_OVERHEAD
        )
        assert limit_for("/bridge/tally/batches", upload_max_mb=50, bridge_max_bytes=2_000_000) == (
            2_000_000 + MULTIPART_OVERHEAD
        )
        assert limit_for("/conversations", upload_max_mb=50, bridge_max_bytes=10) == (
            DEFAULT_MAX_BYTES
        )
