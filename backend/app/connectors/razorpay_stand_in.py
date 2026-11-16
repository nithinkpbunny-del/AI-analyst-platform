"""A local stand-in for Razorpay's API, used only in development (``APP_ENV=development``).

It answers the four ``GET`` list endpoints (payments, orders, refunds, settlements) in Razorpay's format, from
the demo ``eval/fixtures/razorpay_payments.csv`` (``make fixtures``) or, when that file is missing, from a small
built-in sample. Orders, refunds and settlements are derived from the payments. It is chosen for keys whose id
starts with ``rzp_test_demo``; every other key goes to the real Razorpay. Nothing here is a secret.
"""

from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

STAND_IN_PREFIX = "rzp_test_demo"
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "eval" / "fixtures"
DAY = 86_400


def _sample() -> list[dict[str, Any]]:
    base = 1_760_000_000
    return [
        {
            "id": f"pay_sample{i:04d}",
            "entity": "payment",
            "amount": 49_900 + 1_000 * (i % 7),
            "currency": "INR",
            "status": "captured" if i % 9 else "failed",
            "order_id": f"order_sample{i:04d}",
            "method": ("upi", "card", "netbanking")[i % 3],
            "captured": bool(i % 9),
            "description": f"Order #{1000 + i}",
            "amount_refunded": 49_900 if i % 13 == 0 else 0,
            "refund_status": "full" if i % 13 == 0 else None,
            "fee": 1_000,
            "tax": 180,
            "international": False,
            "error_code": None,
            "created_at": base + i * 3_600,
        }
        for i in range(120)
    ]


def _load_payments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return _sample()
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            refunded = int(row.get("amount_refunded") or 0)
            amount = int(row["amount"])
            rows.append(
                {
                    "id": row["id"],
                    "entity": "payment",
                    "amount": amount,
                    "currency": row["currency"],
                    "status": row["status"],
                    "order_id": row["order_id"] or None,
                    "method": row["method"],
                    "captured": row["captured"].lower() == "true",
                    "description": row["description"],
                    "amount_refunded": refunded,
                    "refund_status": ("full" if refunded >= amount else "partial")
                    if refunded
                    else None,
                    "fee": int(row.get("fee") or 0),
                    "tax": int(row.get("tax") or 0),
                    "international": False,
                    "error_code": None,
                    "created_at": int(float(row["created_at"])),
                }
            )
    return rows


@lru_cache(maxsize=2)
def tables(fixtures_dir: Path = FIXTURES_DIR) -> dict[str, list[dict[str, Any]]]:
    payments = _load_payments(fixtures_dir / "razorpay_payments.csv")
    orders = [
        {
            "id": p["order_id"],
            "entity": "order",
            "amount": p["amount"],
            "amount_paid": p["amount"] if p["captured"] else 0,
            "amount_due": 0 if p["captured"] else p["amount"],
            "currency": p["currency"],
            "receipt": p["description"],
            "status": "paid" if p["captured"] else "attempted",
            "attempts": 1,
            "created_at": p["created_at"] - 60,
        }
        for p in payments
        if p["order_id"]
    ]
    refunds = [
        {
            "id": "rfnd_" + p["id"][4:],
            "entity": "refund",
            "amount": p["amount_refunded"],
            "currency": p["currency"],
            "payment_id": p["id"],
            "status": "processed",
            "speed_processed": "normal",
            "created_at": p["created_at"] + DAY,
        }
        for p in payments
        if p["amount_refunded"]
    ]
    days: dict[int, list[dict[str, Any]]] = {}
    for p in payments:
        if p["captured"]:
            days.setdefault(p["created_at"] // DAY, []).append(p)
    settlements = []
    for day, items in sorted(days.items()):
        fees = sum(i["fee"] for i in items)
        tax = sum(i["tax"] for i in items)
        gross = sum(i["amount"] for i in items)
        settlements.append(
            {
                "id": f"setl_{day}",
                "entity": "settlement",
                "amount": gross - fees - tax,
                "status": "processed",
                "fees": fees,
                "tax": tax,
                "utr": f"UTR{day:08d}",
                "created_at": (day + 2) * DAY,
            }
        )
    return {
        "payments": sorted(payments, key=lambda r: -r["created_at"]),
        "orders": sorted(orders, key=lambda r: -r["created_at"]),
        "refunds": sorted(refunds, key=lambda r: -r["created_at"]),
        "settlements": sorted(settlements, key=lambda r: -r["created_at"]),
    }


def _error(status: int, code: str, description: str) -> httpx.Response:
    body = {"error": {"code": code, "description": description}}
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )


class StandInRazorpayTransport(httpx.BaseTransport):
    def __init__(self, fixtures_dir: Path = FIXTURES_DIR) -> None:
        self._dir = fixtures_dir

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return _error(405, "BAD_REQUEST_ERROR", "The stand-in only answers GET.")
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Basic "):
            return _error(401, "BAD_REQUEST_ERROR", "Authentication failed")
        import base64

        try:
            key_id, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
        except ValueError:
            return _error(401, "BAD_REQUEST_ERROR", "Authentication failed")
        if not key_id.startswith(STAND_IN_PREFIX) or secret == "wrong":  # noqa: S105
            return _error(401, "BAD_REQUEST_ERROR", "Authentication failed")
        name = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        rows = tables(self._dir).get(name)
        if rows is None:
            return _error(
                404, "BAD_REQUEST_ERROR", "The requested URL was not found on the server."
            )
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        try:
            count = min(100, max(1, int(query.get("count", "10"))))
            skip = max(0, int(query.get("skip", "0")))
            start = int(query.get("from", "0"))
            end = int(query.get("to", str(2**40)))
        except ValueError:
            return _error(400, "BAD_REQUEST_ERROR", "Invalid value in the query.")
        chosen = [r for r in rows if start <= r["created_at"] <= end]
        page = chosen[skip : skip + count]
        body = {"entity": "collection", "count": len(page), "items": page}
        return httpx.Response(
            200, content=json.dumps(body), headers={"content-type": "application/json"}
        )
