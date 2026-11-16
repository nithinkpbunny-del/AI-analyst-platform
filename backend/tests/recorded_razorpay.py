"""A fake Razorpay for tests: the four list endpoints in Razorpay's format, paging by ``count``/``skip`` and
``from``/``to``, Basic auth, and every request recorded. No test talks to the real Razorpay."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.connectors.razorpay import RazorpaySetup

KEY_ID = "rzp_live_AbCdEf123456"
KEY_SECRET = "s3cr3tK3yValue99"
TEST_KEY_ID = "rzp_test_AbCdEf123456"
DAY = 86_400
T0 = 1_780_000_000  # a fixed "now" for the data


def payment(i: int, *, created_at: int | None = None, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"pay_{i:06d}",
        "entity": "payment",
        "amount": 100_000 + i,
        "currency": "INR",
        "status": "captured",
        "order_id": f"order_{i:06d}",
        "method": "upi",
        "captured": True,
        "description": f"Order #{i}",
        "amount_refunded": 0,
        "refund_status": None,
        "fee": 2_000,
        "tax": 360,
        "international": False,
        "error_code": None,
        "created_at": created_at if created_at is not None else T0 - i * 3_600,
        # Personal data Razorpay sends; the connector must not keep any of it.
        "email": "buyer@example.com",
        "contact": "+919800000000",
        "card_id": "card_secret",
        "vpa": "buyer@upi",
    }
    row.update(overrides)
    return row


class FakeRazorpay(httpx.BaseTransport):
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = tables or {
            "payments": [],
            "orders": [],
            "refunds": [],
            "settlements": [],
        }
        self.requests: list[httpx.Request] = []
        self.valid: tuple[tuple[str, str], ...] = ((KEY_ID, KEY_SECRET), (TEST_KEY_ID, KEY_SECRET))
        # status codes to answer with before behaving, per request (e.g. [429, 503])
        self.failures: list[int] = []
        self.always: int | None = None

    def params(self, request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}

    def calls(self, name: str) -> list[dict[str, str]]:
        return [self.params(r) for r in self.requests if r.url.path.endswith(f"/{name}")]

    @staticmethod
    def _answer(status: int, body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            status, content=json.dumps(body), headers={"content-type": "application/json"}
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.always is not None:
            return self._answer(self.always, {"error": {"description": "nope"}})
        if self.failures:
            return self._answer(self.failures.pop(0), {"error": {"description": "slow down"}})
        auth = request.headers.get("authorization", "")
        try:
            who = tuple(base64.b64decode(auth[6:]).decode().split(":", 1))
        except ValueError:
            who = ("", "")
        if who not in self.valid:
            return self._answer(
                401,
                {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}},
            )
        name = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        rows = self.tables.get(name)
        if rows is None:
            return self._answer(404, {"error": {"description": "not found"}})
        q = self.params(request)
        count = min(100, int(q.get("count", "10")))
        skip = int(q.get("skip", "0"))
        start, end = int(q.get("from", "0")), int(q.get("to", str(2**40)))
        chosen = sorted(
            (r for r in rows if start <= r["created_at"] <= end), key=lambda r: -r["created_at"]
        )
        page = chosen[skip : skip + count]
        return self._answer(200, {"entity": "collection", "count": len(page), "items": page})


def setup(fake: FakeRazorpay, **kwargs: Any) -> RazorpaySetup:
    return RazorpaySetup(transport=fake, sleep=lambda _s: None, **kwargs)
