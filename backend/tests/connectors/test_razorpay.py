"""The Razorpay connector against a fake Razorpay (no live calls): read-only, paging, windows, incremental
cursor, errors, money in paise, no personal data."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.connectors.base import ConnectorError, DatasetInfo, ReadOnlyViolation
from app.connectors.razorpay import (
    SPECS,
    RazorpayConnector,
    RazorpaySetup,
    default_setup,
    key_mode,
    mask_key_id,
    valid_key_id,
)
from app.connectors.razorpay_stand_in import STAND_IN_PREFIX
from app.data.cache import merge_incremental
from tests.recorded_razorpay import DAY, KEY_ID, KEY_SECRET, T0, FakeRazorpay, payment, setup

NOW = datetime.fromtimestamp(T0 + 3_600, UTC)


def connector(
    fake: FakeRazorpay,
    *,
    key_id: str = KEY_ID,
    secret: str = KEY_SECRET,
    max_rows: int = 10_000,
    **kw: Any,
) -> RazorpayConnector:
    return RazorpayConnector(
        setup=setup(
            fake,
            history_days=kw.pop("history_days", 365),
            lookback_days=kw.pop("lookback_days", 30),
        ),
        key_id=key_id,
        key_secret=secret,
        timezone="Asia/Kolkata",
        max_rows=max_rows,
        clock=lambda: NOW,
    )


PAYMENTS = DatasetInfo("payments", "payments")


def test_four_datasets() -> None:
    names = [d.ref for d in connector(FakeRazorpay()).list_datasets()]
    assert names == ["payments", "orders", "refunds", "settlements"]


def test_reads_only_with_get_and_basic_auth() -> None:
    fake = FakeRazorpay({"payments": [payment(1)], "orders": [], "refunds": [], "settlements": []})
    conn = connector(fake)
    for info in conn.list_datasets():
        conn.fetch(info)
    assert {r.method for r in fake.requests} == {"GET"}
    assert all(r.headers["authorization"].startswith("Basic ") for r in fake.requests)
    assert all(
        r.url.host == "api.razorpay.com" and r.url.path.startswith("/v1/") for r in fake.requests
    )


def test_a_write_through_the_connectors_client_is_refused() -> None:
    conn = connector(FakeRazorpay())
    with pytest.raises(ReadOnlyViolation):
        conn._http.request("POST", "https://api.razorpay.com/v1/refunds", json={})
    with pytest.raises(ReadOnlyViolation):
        conn._http.request("DELETE", "https://api.razorpay.com/v1/payments/pay_1")


def test_amounts_stay_in_paise_and_times_are_local() -> None:
    fake = FakeRazorpay(
        {"payments": [payment(1, created_at=T0)], "orders": [], "refunds": [], "settlements": []}
    )
    result = connector(fake).fetch(PAYMENTS)
    row = result.frame.iloc[0]
    assert row["amount"] == 100_001 and str(result.frame["amount"].dtype) == "Int64"
    assert row["fee"] == 2_000 and row["tax"] == 360
    assert bool(row["captured"]) is True
    expected = datetime.fromtimestamp(T0, UTC).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    )
    assert row["created_at"].to_pydatetime() == expected.replace(tzinfo=None)
    types = {c.name: c.type for c in result.columns}
    assert (
        types["amount"] == "integer"
        and types["created_at"] == "datetime"
        and types["captured"] == "boolean"
    )
    assert all(c.unit is None for c in result.columns)  # paise vs rupees is the dictionary's call


def test_no_personal_data_is_read() -> None:
    fake = FakeRazorpay({"payments": [payment(1)], "orders": [], "refunds": [], "settlements": []})
    frame = connector(fake).fetch(PAYMENTS).frame
    assert not {"email", "contact", "card_id", "vpa"} & set(frame.columns)
    assert "buyer@example.com" not in frame.to_csv()


def test_pages_of_100_in_monthly_windows_newest_first() -> None:
    rows = [
        payment(i) for i in range(250)
    ]  # 250 hourly payments span ~10 days: one window, 3 pages
    fake = FakeRazorpay({"payments": rows, "orders": [], "refunds": [], "settlements": []})
    result = connector(fake).fetch(PAYMENTS)
    assert len(result.frame) == 250 and result.frame["id"].is_unique
    calls = fake.calls("payments")
    assert [c["skip"] for c in calls[:3]] == ["0", "100", "200"]  # newest window first
    assert len(calls) == 3 + 11  # 12 windows of 31 days cover the 365-day history
    assert {c["count"] for c in calls} == {"100"}
    assert all(int(c["to"]) >= int(c["from"]) for c in calls)
    assert result.cursor == str(T0 - 0)  # newest created_at
    assert result.key_column == "id" and result.incremental is False


def test_history_spans_several_windows() -> None:
    rows = [payment(i, created_at=T0 - i * 20 * DAY) for i in range(10)]  # 180 days
    fake = FakeRazorpay({"payments": rows, "orders": [], "refunds": [], "settlements": []})
    result = connector(fake, history_days=365).fetch(PAYMENTS)
    assert len(result.frame) == 10
    windows = {(c["from"], c["to"]) for c in fake.calls("payments")}
    assert len(windows) >= 11  # 365 days / 31-day windows
    # windows run newest first and never go back past the history limit
    assert min(int(f) for f, _ in windows) >= int(NOW.timestamp()) - 365 * DAY


def test_history_limit_leaves_older_rows_out() -> None:
    rows = [payment(1, created_at=T0), payment(2, created_at=T0 - 400 * DAY)]
    fake = FakeRazorpay({"payments": rows, "orders": [], "refunds": [], "settlements": []})
    assert list(connector(fake, history_days=365).fetch(PAYMENTS).frame["id"]) == ["pay_000001"]


def test_row_cap_keeps_the_newest_and_says_truncated() -> None:
    rows = [payment(i) for i in range(30)]
    fake = FakeRazorpay({"payments": rows, "orders": [], "refunds": [], "settlements": []})
    result = connector(fake, max_rows=12).fetch(PAYMENTS)
    assert result.truncated and len(result.frame) == 12
    assert "pay_000000" in set(result.frame["id"])


def test_incremental_starts_before_the_cursor_and_changed_rows_replace_old_ones() -> None:
    old = [payment(i, created_at=T0 - i * DAY) for i in range(5)]
    fake = FakeRazorpay({"payments": old, "orders": [], "refunds": [], "settlements": []})
    conn = connector(fake, lookback_days=10)
    first = conn.fetch(PAYMENTS)
    fake.requests.clear()
    # one payment is refunded later; a new one arrives
    fake.tables["payments"][1] = payment(
        1, created_at=T0 - DAY, amount_refunded=100_001, refund_status="full"
    )
    fake.tables["payments"].append(payment(99, created_at=T0 + 600))
    second = conn.fetch(PAYMENTS, since_cursor=first.cursor)
    assert second.incremental is True
    assert min(int(c["from"]) for c in fake.calls("payments")) == int(first.cursor or 0) - 10 * DAY
    assert {"pay_000099", "pay_000001"} <= set(second.frame["id"])
    merged = merge_incremental(first.frame, second.frame, second.key_column)
    assert len(merged) == 6 and merged["id"].is_unique
    refunded = merged.loc[merged["id"] == "pay_000001", "amount_refunded"].iloc[0]
    assert refunded == 100_001
    assert second.cursor == str(T0 + 600)


def test_incremental_with_nothing_new_keeps_the_cursor() -> None:
    fake = FakeRazorpay({"payments": [], "orders": [], "refunds": [], "settlements": []})
    result = connector(fake).fetch(PAYMENTS, since_cursor=str(T0))
    assert result.frame.empty and result.cursor == str(T0)


def test_a_bad_cursor_means_a_full_read() -> None:
    fake = FakeRazorpay({"payments": [payment(1)], "orders": [], "refunds": [], "settlements": []})
    result = connector(fake).fetch(PAYMENTS, since_cursor="not-a-number")
    assert result.incremental is False and len(result.frame) == 1


def test_empty_tables_have_typed_columns() -> None:
    for spec in SPECS.values():
        result = connector(FakeRazorpay()).fetch(DatasetInfo(spec.ref, spec.name))
        assert list(result.frame.columns) == [f.name for f in spec.fields]
        assert result.frame.empty and result.cursor is None


def test_orders_refunds_and_settlements_have_their_columns() -> None:
    fake = FakeRazorpay(
        {
            "payments": [],
            "orders": [
                {
                    "id": "order_1",
                    "entity": "order",
                    "amount": 5_000,
                    "amount_paid": 5_000,
                    "amount_due": 0,
                    "currency": "INR",
                    "receipt": "r1",
                    "status": "paid",
                    "attempts": 1,
                    "created_at": T0,
                }
            ],
            "refunds": [
                {
                    "id": "rfnd_1",
                    "entity": "refund",
                    "amount": 1_000,
                    "currency": "INR",
                    "payment_id": "pay_1",
                    "status": "processed",
                    "speed_processed": "normal",
                    "created_at": T0,
                }
            ],
            "settlements": [
                {
                    "id": "setl_1",
                    "entity": "settlement",
                    "amount": 4_000,
                    "status": "processed",
                    "fees": 100,
                    "tax": 18,
                    "utr": "UTR1",
                    "created_at": T0,
                }
            ],
        }
    )
    conn = connector(fake)
    assert conn.fetch(DatasetInfo("orders", "orders")).frame.iloc[0]["amount_paid"] == 5_000
    assert conn.fetch(DatasetInfo("refunds", "refunds")).frame.iloc[0]["payment_id"] == "pay_1"
    settlement = conn.fetch(DatasetInfo("settlements", "settlements")).frame.iloc[0]
    assert settlement["fees"] == 100 and settlement["utr"] == "UTR1"


# ------------------------------------------------------------------------------------------- errors
def test_a_wrong_key_needs_reauth_in_plain_words() -> None:
    conn = connector(FakeRazorpay(), secret="wrong-secret-value")
    with pytest.raises(ConnectorError) as raised:
        conn.fetch(PAYMENTS)
    assert raised.value.kind == "needs_reauth"
    assert "key" in raised.value.message and "wrong-secret-value" not in raised.value.message


def test_429_and_503_are_retried_then_succeed() -> None:
    fake = FakeRazorpay({"payments": [payment(1)], "orders": [], "refunds": [], "settlements": []})
    fake.failures = [429, 503]
    assert len(connector(fake).fetch(PAYMENTS).frame) == 1
    assert len(fake.requests) == 2 + 12  # two refused answers, then one read per 31-day window


def test_a_source_that_keeps_failing_is_unavailable_or_rate_limited() -> None:
    fake = FakeRazorpay()
    fake.always = 503
    with pytest.raises(ConnectorError) as raised:
        connector(fake).fetch(PAYMENTS)
    assert raised.value.kind == "unavailable"
    fake.always = 429
    with pytest.raises(ConnectorError) as limited:
        connector(fake).fetch(PAYMENTS)
    assert limited.value.kind == "rate_limited"


def test_network_failure_is_unavailable() -> None:
    class Down(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

    conn = RazorpayConnector(
        setup=RazorpaySetup(transport=Down(), sleep=lambda _s: None),
        key_id=KEY_ID,
        key_secret=KEY_SECRET,
        timezone="Asia/Kolkata",
        max_rows=10,
    )
    with pytest.raises(ConnectorError) as raised:
        conn.fetch(PAYMENTS)
    assert raised.value.kind == "unavailable"


def test_an_unreadable_answer_is_unavailable() -> None:
    class Junk(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    conn = RazorpayConnector(
        setup=RazorpaySetup(transport=Junk(), sleep=lambda _s: None),
        key_id=KEY_ID,
        key_secret=KEY_SECRET,
        timezone="Asia/Kolkata",
        max_rows=10,
    )
    with pytest.raises(ConnectorError) as raised:
        conn.fetch(PAYMENTS)
    assert raised.value.kind == "unavailable"


def test_unknown_table() -> None:
    with pytest.raises(ConnectorError):
        connector(FakeRazorpay()).fetch(DatasetInfo("subscriptions", "subscriptions"))


# --------------------------------------------------------------------------------------------- test()
def test_test_reports_checks_and_mode() -> None:
    result = connector(FakeRazorpay()).test()
    assert result.ok and result.details == {"mode": "live"}
    assert [c.label for c in result.checks] == ["signed_in", "read_only", "payments_readable"]
    assert "only reads" in result.message


def test_test_with_a_bad_key_fails_with_signed_in_false() -> None:
    result = connector(FakeRazorpay(), secret="nope-nope-nope").test()
    assert not result.ok
    assert [(c.label, c.ok) for c in result.checks] == [
        ("signed_in", False),
        ("payments_readable", False),
    ]


def test_incremental_is_supported_and_pushdown_is_not() -> None:
    conn = connector(FakeRazorpay())
    assert conn.supports_incremental() and not conn.supports_pushdown()
    assert conn.source_version(PAYMENTS) is None


# ---------------------------------------------------------------------------------------- key helpers
def test_key_helpers() -> None:
    assert key_mode("rzp_live_abc") == "live" and key_mode("rzp_test_abc") == "test"
    assert key_mode("sk_live_abc") is None
    assert valid_key_id(KEY_ID) and not valid_key_id("hello")
    assert mask_key_id(KEY_ID) == "rzp_live_AbCd••••"
    assert KEY_ID[14:] not in mask_key_id(KEY_ID)


def test_the_stand_in_is_only_for_development() -> None:
    dev = default_setup(Settings(_env_file=None, app_env="development"))
    assert dev.stand_in_prefix == STAND_IN_PREFIX and dev.transport is not None
    assert dev.http("rzp_live_realkey1234") is not None
    prod_settings = Settings(
        _env_file=None,
        app_env="production",
        master_key="m" * 40,
        jwt_secret="j" * 40,
    )
    prod = default_setup(prod_settings)
    assert prod.transport is None and prod.stand_in_prefix is None
    assert prod.base_url == "https://api.razorpay.com/v1"


def test_the_stand_in_serves_the_demo_data_in_razorpay_format() -> None:
    from app.connectors.razorpay_stand_in import StandInRazorpayTransport

    setup_ = RazorpaySetup(
        transport=StandInRazorpayTransport(),
        stand_in_prefix=STAND_IN_PREFIX,
        history_days=3650,
    )
    conn = RazorpayConnector(
        setup=setup_,
        key_id="rzp_test_demoAbCd1234",
        key_secret="anything-goes",
        timezone="Asia/Kolkata",
        max_rows=100_000,
        clock=lambda: datetime(2026, 12, 31, tzinfo=UTC),
    )
    assert conn.test().ok
    payments = conn.fetch(PAYMENTS)
    assert len(payments.frame) > 100 and payments.frame["id"].is_unique
    for ref in ("orders", "refunds", "settlements"):
        assert len(conn.fetch(DatasetInfo(ref, ref)).frame) > 0
    wrong = RazorpayConnector(
        setup=setup_,
        key_id="rzp_test_demoAbCd1234",
        key_secret="wrong",
        timezone="UTC",
        max_rows=10,
    )
    assert not wrong.test().ok
