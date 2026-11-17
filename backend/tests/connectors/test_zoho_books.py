"""The Zoho Books connector (M13) against the stand-in Zoho (no live calls): sign-in, data centres, read scopes,
paging, history, incremental reads, the daily and per-minute limits, the P&L report, no personal data, no writes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import fakeredis
import httpx
import pandas as pd
import pytest

from app.connectors.base import ConnectorError, DatasetInfo, ReadOnlyViolation
from app.connectors.zoho_books import (
    DEFAULT_SCOPES,
    PAGE_SIZE,
    TABLES,
    ZohoBooksConnector,
    ZohoQuota,
    ZohoSetup,
    api_domain_for,
    authorization_url,
    check_scopes,
    exchange_code,
    pnl_lines,
    read_scopes,
    region_for_accounts_server,
    revoke,
)
from app.connectors.zoho_stand_in import (
    FIXTURES_DIR,
    LABS_ORG,
    MAIN_ORG,
    REVOKED_REFRESH_TOKEN,
    StandInZohoTransport,
    demo_orgs,
)
from tests.recorded_zoho import REFRESH_TOKEN, code, fake_zoho, zoho_test_setup

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
PERSONAL = {
    "email",
    "phone",
    "mobile",
    "description",
    "billing_address",
    "contact_persons",
    "gst_no",
}


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def fake() -> StandInZohoTransport:
    return fake_zoho()


@pytest.fixture
def setup(fake: StandInZohoTransport) -> ZohoSetup:
    return zoho_test_setup(fake)


def connector(
    setup: ZohoSetup,
    *,
    token: str = REFRESH_TOKEN,
    organization_id: str = MAIN_ORG,
    max_rows: int = 1_000_000,
    quota: ZohoQuota | None = None,
    clock: Clock | None = None,
) -> ZohoBooksConnector:
    return ZohoBooksConnector(
        setup=setup,
        refresh_token=token,
        region="in",
        organization_id=organization_id,
        timezone="Asia/Kolkata",
        max_rows=max_rows,
        clock=clock or Clock(),
        quota=quota,
    )


def fetch(c: ZohoBooksConnector, ref: str, cursor: str | None = None) -> Any:
    return c.fetch(DatasetInfo(ref=ref, name=ref), since_cursor=cursor)


class TestScopesAndRegions:
    def test_only_read_scopes_are_ever_requested(self) -> None:
        assert read_scopes("") == DEFAULT_SCOPES
        assert all(s.endswith(".READ") for s in DEFAULT_SCOPES)
        chosen = read_scopes(
            "ZohoBooks.invoices.READ ZohoBooks.invoices.CREATE,ZohoBooks.fullaccess.all, "
            "ZohoBooks.bills.READ ZohoBooks.invoices.READ"
        )
        assert chosen == ("ZohoBooks.invoices.READ", "ZohoBooks.bills.READ")
        assert read_scopes("ZohoBooks.invoices.ALL") == DEFAULT_SCOPES

    @pytest.mark.parametrize(
        "granted",
        [
            "ZohoBooks.invoices.READ,ZohoBooks.settings.READ,ZohoBooks.contacts.READ,ZohoBooks.invoices.CREATE",
            "ZohoBooks.fullaccess.all",
            "ZohoBooks.invoices.READ,ZohoBooks.settings.READ,ZohoBooks.contacts.READ,ZohoBooks.bills.ALL",
        ],
    )
    def test_a_token_that_can_write_is_refused(self, granted: str) -> None:
        with pytest.raises(ConnectorError) as raised:
            check_scopes(granted)
        assert raised.value.kind == "invalid"
        assert "only reading" in raised.value.message

    def test_a_token_missing_the_core_reads_is_refused(self) -> None:
        with pytest.raises(ConnectorError, match="did not allow"):
            check_scopes("ZohoBooks.bills.READ")
        check_scopes(",".join(DEFAULT_SCOPES))  # fine
        check_scopes(None)  # Zoho did not say: the requested scopes were read-only

    @pytest.mark.parametrize(
        ("server", "region"),
        [
            ("https://accounts.zoho.in", "in"),
            ("https://accounts.zoho.com", "com"),
            ("https://accounts.zoho.eu/", "eu"),
            ("https://accounts.zohocloud.ca", "ca"),
            ("https://accounts.zoho.in.evil.com", None),
            ("http://accounts.zoho.in", None),
            ("https://evil.com", None),
            ("", None),
        ],
    )
    def test_only_zohos_own_data_centres_are_accepted(
        self, server: str, region: str | None
    ) -> None:
        assert region_for_accounts_server(server) == region

    def test_the_api_host_must_belong_to_the_data_centre(self) -> None:
        assert api_domain_for("in", "https://www.zohoapis.in") == "https://www.zohoapis.in"
        assert api_domain_for("in", "https://www.zohoapis.com") == "https://www.zohoapis.in"
        assert api_domain_for("in", "https://attacker.example") == "https://www.zohoapis.in"
        assert api_domain_for("eu", None) == "https://www.zohoapis.eu"

    def test_the_consent_page_is_indias_by_default_with_offline_read_access(
        self, setup: ZohoSetup
    ) -> None:
        url = urlsplit(authorization_url(setup, state="s" * 32))
        assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.zoho.in/oauth/v2/auth"
        query = parse_qs(url.query)
        assert query["scope"] == [",".join(DEFAULT_SCOPES)]
        assert query["access_type"] == ["offline"]
        assert query["redirect_uri"] == ["http://localhost:8000/connections/zoho/callback"]

    def test_another_region_can_be_configured(self, fake: StandInZohoTransport) -> None:
        url = authorization_url(zoho_test_setup(fake, region="eu"), state="s" * 32)
        assert url.startswith("https://accounts.zoho.eu/oauth/v2/auth?")


class TestSignIn:
    def test_a_code_becomes_a_refresh_token_once_and_the_answer_is_checked(
        self, setup: ZohoSetup
    ) -> None:
        http = setup.http("in")
        tokens = exchange_code(setup, http, region="in", code=code())
        assert tokens.refresh_token.startswith("1000.standin-refresh.")
        assert tokens.api_domain == "https://www.zohoapis.in"

    def test_a_write_scope_in_the_answer_is_refused(self, setup: ZohoSetup) -> None:
        with pytest.raises(ConnectorError, match="only reading"):
            exchange_code(
                setup,
                setup.http("in"),
                region="in",
                code=code(
                    "ZohoBooks.invoices.READ,ZohoBooks.settings.READ,ZohoBooks.contacts.READ,"
                    "ZohoBooks.invoices.CREATE"
                ),
            )

    def test_a_bad_code_is_a_plain_refusal(self, setup: ZohoSetup) -> None:
        with pytest.raises(ConnectorError) as raised:
            exchange_code(setup, setup.http("in"), region="in", code="made-up")
        assert raised.value.kind == "invalid"
        assert "Connect Zoho Books" in raised.value.message

    def test_the_organisations_list_drops_personal_details(self, setup: ZohoSetup) -> None:
        orgs = connector(setup, organization_id="").organizations()
        assert orgs == [
            {"id": MAIN_ORG, "name": "UrbanCart", "currency": "INR", "default": True},
            {"id": LABS_ORG, "name": "UrbanCart Labs", "currency": "INR", "default": False},
        ]

    def test_the_check_reads_once_and_says_it_only_reads(self, setup: ZohoSetup) -> None:
        result = connector(setup).test()
        assert result.ok, result.message
        assert "only reads" in result.message
        assert {c.label for c in result.checks if c.ok} == {
            "signed_in",
            "read_only",
            "organization",
            "invoices_readable",
        }
        assert result.details["organization_name"] == "UrbanCart"

    def test_an_organisation_the_account_cannot_see_fails_the_check(self, setup: ZohoSetup) -> None:
        result = connector(setup, organization_id="999").test()
        assert not result.ok
        assert any(c.label == "organization" and not c.ok for c in result.checks)

    def test_a_revoked_refresh_token_needs_a_new_sign_in(self, setup: ZohoSetup) -> None:
        c = connector(setup, token=REVOKED_REFRESH_TOKEN)
        result = c.test()
        assert not result.ok
        assert any(ch.label == "signed_in" and not ch.ok for ch in result.checks)
        with pytest.raises(ConnectorError) as raised:
            fetch(c, "invoices")
        assert raised.value.kind == "needs_reauth"
        assert "Sign in to Zoho again" in raised.value.message

    def test_an_expired_access_token_is_renewed_once(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        c = connector(setup)
        c.test()
        before = fake.token_calls
        fake.expire_next = 1
        assert len(fetch(c, "items").frame) == 6
        assert fake.token_calls == before + 1

    def test_access_tokens_are_reused_not_fetched_per_call(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        c = connector(setup)
        for ref in ("items", "contacts", "chart_of_accounts"):
            fetch(c, ref)
        assert fake.token_calls == 1

    def test_revoking_tells_zoho_to_forget_the_token(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        revoke(setup, setup.http("in"), region="in", refresh_token=REFRESH_TOKEN)
        assert REFRESH_TOKEN in fake.revoked
        with pytest.raises(ConnectorError) as raised:
            fetch(connector(setup), "items")
        assert raised.value.kind == "needs_reauth"


class TestReading:
    def test_seven_tables(self, setup: ZohoSetup) -> None:
        refs = [d.ref for d in connector(setup).list_datasets()]
        assert refs == [
            "invoices",
            "bills",
            "expenses",
            "contacts",
            "items",
            "chart_of_accounts",
            "profit_and_loss",
        ]

    def test_pages_of_200_are_followed_to_the_end(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        result = fetch(connector(setup), "expenses")
        assert len(result.frame) == 315 > PAGE_SIZE
        assert result.frame["expense_id"].is_unique

    def test_money_is_in_rupees_and_declared_so(self, setup: ZohoSetup) -> None:
        result = fetch(connector(setup), "invoices")
        money = {c.name: c for c in result.columns if c.stored_in}
        assert set(money) == {"total", "balance"}
        assert all(c.unit == "INR" and c.stored_in == "rupees" for c in money.values())
        assert result.frame["total"].dtype == "float64"
        assert result.frame["date"].dtype.kind == "M"
        assert result.key_column == "invoice_id"

    def test_no_personal_details_are_kept(self, setup: ZohoSetup) -> None:
        c = connector(setup)
        for table in TABLES:
            frame = fetch(c, table).frame
            assert not (set(frame.columns) & PERSONAL), table
            assert list(frame.columns) == [f.name for f in TABLES[table].fields]

    def test_a_foreign_currency_organisation_leaves_the_unit_to_the_owner(
        self, setup: ZohoSetup
    ) -> None:
        c = connector(setup)
        c.use_organization(MAIN_ORG, "USD")
        result = fetch(c, "invoices")
        total = next(col for col in result.columns if col.name == "total")
        assert total.unit is None and total.stored_in is None and total.currencies == ("USD",)

    def test_the_first_read_goes_back_the_history_window(self, fake: StandInZohoTransport) -> None:
        setup = zoho_test_setup(fake, history_days=200)
        frame = fetch(connector(setup), "expenses").frame
        assert frame["date"].min() >= pd.Timestamp("2026-04-23")

    def test_the_row_cap_stops_a_read(self, setup: ZohoSetup) -> None:
        result = fetch(connector(setup, max_rows=250), "expenses")
        assert len(result.frame) == 250 and result.truncated

    def test_later_reads_ask_only_for_changed_rows(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        c = connector(setup)
        first = fetch(c, "invoices")
        assert not first.incremental and first.cursor
        assert fetch(c, "invoices", first.cursor).frame.shape[0] <= 3  # the overlap only
        row = fake.orgs[MAIN_ORG].tables["invoices"][0]  # type: ignore[index]
        row["balance"] = 0.0
        row["status"] = "paid"
        row["last_modified_time"] = "2026-11-01T10:00:00+0530"
        later = fetch(c, "invoices", first.cursor)
        assert later.incremental
        assert row["invoice_id"] in set(later.frame["invoice_id"])
        assert later.cursor == "2026-11-01T10:00:00+0530"

    def test_the_chart_of_accounts_is_read_whole(self, setup: ZohoSetup) -> None:
        c = connector(setup)
        first = fetch(c, "chart_of_accounts")
        again = fetch(c, "chart_of_accounts", first.cursor)
        assert not again.incremental and len(again.frame) == len(first.frame) == 10

    def test_an_unknown_table_is_not_found(self, setup: ZohoSetup) -> None:
        with pytest.raises(ConnectorError) as raised:
            fetch(connector(setup), "journals")
        assert raised.value.kind == "not_found"


class TestProfitAndLoss:
    def test_the_report_is_read_month_by_month_and_flattened(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        before = fake.calls
        result = fetch(connector(setup), "profit_and_loss")
        assert fake.calls - before == 24  # ZOHO_REPORT_MONTHS
        frame = result.frame
        assert set(frame["section"]) == {
            "Operating Income",
            "Cost of Goods Sold",
            "Operating Expense",
        }
        july = frame[frame["month"] == pd.Timestamp("2026-07-01")]
        assert set(july["account_name"]) == {
            "Sales",
            "Purchases",
            "Marketing",
            "Operations",
            "Technology",
            "HR",
        }
        assert frame["line_id"].is_unique
        assert result.cursor == "2026-11"
        amount = next(c for c in result.columns if c.name == "amount")
        assert amount.stored_in == "rupees"

    def test_a_refused_report_fails_only_that_table(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        fake.refuse_reports = True
        c = connector(setup)
        with pytest.raises(ConnectorError) as raised:
            fetch(c, "profit_and_loss")
        assert raised.value.kind == "invalid"  # not "needs_reauth": the connection stays live
        assert "profit and loss report" in raised.value.message
        assert len(fetch(c, "invoices").frame) > 0

    def test_a_later_read_repeats_only_the_last_two_months(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        c = connector(setup)
        first = fetch(c, "profit_and_loss")
        before = fake.calls
        later = fetch(c, "profit_and_loss", first.cursor)
        assert later.incremental and fake.calls - before == 2

    def test_nested_groups_are_filed_under_their_section(self) -> None:
        report = [
            {
                "name": "Gross Profit",
                "total": 70,
                "account_transactions": [
                    {
                        "name": "Operating Income",
                        "total": 100,
                        "account_transactions": [
                            {
                                "name": "Sales group",
                                "account_transactions": [{"name": "Online", "total": 100}],
                            }
                        ],
                    },
                    {
                        "name": "Cost of Goods Sold",
                        "account_transactions": [{"name": "Stock", "total": "30"}],
                    },
                ],
            },
            {"name": "Net Profit/Loss", "total": 70, "account_transactions": []},
            {"name": "Operating Expense", "account_transactions": [{"name": "Rent", "total": 0}]},
        ]
        rows = pnl_lines(report, date(2026, 7, 1))
        assert [(r["section"], r["account_name"], r["amount"]) for r in rows] == [
            ("Operating Income", "Online", 100.0),
            ("Cost of Goods Sold", "Stock", 30.0),
        ]
        assert pnl_lines({"unexpected": True}, date(2026, 7, 1)) == []


class TestLimits:
    def quota(
        self, *, daily: int, redis_client: Any = None, sleeps: list[float] | None = None
    ) -> ZohoQuota:
        return ZohoQuota(
            redis_client if redis_client is not None else fakeredis.FakeRedis(),
            daily_limit=daily,
            reserve_pct=10.0,
            per_minute=90,
            clock=Clock(),
            sleep=(sleeps.append if sleeps is not None else lambda _s: None),
        )

    def test_reading_stops_at_ninety_percent_of_the_daily_limit(self, setup: ZohoSetup) -> None:
        quota = self.quota(daily=20)
        c = connector(setup, quota=quota)
        fetch(c, "items")
        with pytest.raises(ConnectorError) as raised:
            fetch(c, "profit_and_loss")  # 24 calls do not fit into the 18 allowed
        assert raised.value.kind == "rate_limited" and raised.value.stale_ok
        assert "daily API limit" in raised.value.message
        assert quota.used(MAIN_ORG) == 18
        with pytest.raises(ConnectorError):
            fetch(c, "items")  # refused before any call

    def test_the_count_is_per_organisation_and_per_day(self) -> None:
        client = fakeredis.FakeRedis()
        quota = self.quota(daily=1000, redis_client=client)
        quota.take(MAIN_ORG)
        quota.take(MAIN_ORG)
        quota.take(LABS_ORG)
        assert quota.used(MAIN_ORG) == 2 and quota.used(LABS_ORG) == 1
        tomorrow = ZohoQuota(
            client,
            daily_limit=1000,
            reserve_pct=10.0,
            per_minute=90,
            clock=Clock(datetime(2026, 11, 10, 9, 0, tzinfo=UTC)),
            sleep=lambda _s: None,
        )
        assert tomorrow.used(MAIN_ORG) == 0

    def test_reads_wait_when_the_minutes_share_is_used(self) -> None:
        sleeps: list[float] = []
        quota = self.quota(daily=10_000, sleeps=sleeps)
        for _ in range(95):
            quota.take(MAIN_ORG)
        assert len(sleeps) == 5 and all(0 < s <= 61 for s in sleeps)

    def test_zohos_own_daily_limit_answer_stops_reading_for_the_day(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        fake.daily_limit = 3
        quota = self.quota(daily=1000)
        c = connector(setup, quota=quota)
        with pytest.raises(ConnectorError) as raised:
            fetch(c, "profit_and_loss")
        assert raised.value.stale_ok and quota.near_limit(MAIN_ORG)

    def test_the_http_client_retries_a_busy_answer(self, fake: StandInZohoTransport) -> None:
        answers = iter([httpx.Response(503), httpx.Response(429, headers={"retry-after": "1"})])

        class Flaky(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path.startswith("/books/v3/items"):
                    nxt = next(answers, None)
                    if nxt is not None:
                        return nxt
                return fake.handle_request(request)

        sleeps: list[float] = []
        setup = zoho_test_setup(fake, transport=Flaky(), sleep=sleeps.append)
        assert len(fetch(connector(setup), "items").frame) == 6
        assert sleeps == [1.0, 1.0]


class TestReadOnly:
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_the_client_refuses_writes_before_sending(
        self, setup: ZohoSetup, fake: StandInZohoTransport, method: str
    ) -> None:
        c = connector(setup)
        with pytest.raises(ReadOnlyViolation):
            c._http.request(method, "https://www.zohoapis.in/books/v3/invoices", json={})
        assert fake.writes_received == 0

    def test_only_the_token_endpoints_take_a_post(self, setup: ZohoSetup) -> None:
        http = setup.http("in")
        http.check("POST", "https://accounts.zoho.in/oauth/v2/token")
        http.check("POST", "https://accounts.zoho.in/oauth/v2/token/revoke")
        for url in (
            "https://accounts.zoho.com/oauth/v2/token",  # another data centre's
            "https://www.zohoapis.in/books/v3/invoices",
            "https://www.zohoapis.in/books/v3/invoices/1/void",
        ):
            with pytest.raises(ReadOnlyViolation):
                http.check("POST", url)

    def test_a_whole_read_sends_nothing_but_gets(
        self, setup: ZohoSetup, fake: StandInZohoTransport
    ) -> None:
        c = connector(setup)
        c.test()
        for table in TABLES:
            fetch(c, table)
        assert fake.writes_received == 0


@pytest.mark.skipif(
    not (FIXTURES_DIR / "zoho_budget_expenses.xlsx").exists(), reason="run `make fixtures` first"
)
class TestDemoOrganisation:
    def test_expenses_add_up_to_the_answer_keys_actual_spend(self) -> None:
        budget = pd.read_excel(
            FIXTURES_DIR / "zoho_budget_expenses.xlsx", sheet_name="Budget vs Actual"
        )
        expenses = pd.DataFrame(demo_orgs()[MAIN_ORG].tables["expenses"])
        expenses["month"] = pd.to_datetime(expenses["date"]).dt.to_period("M").dt.to_timestamp()
        ours = expenses.groupby(["month", "account_name"])["total"].sum()
        theirs = budget.set_index(["Month", "Department"])["Actual (INR)"]
        theirs.index = theirs.index.set_names(["month", "account_name"])
        pd.testing.assert_series_equal(
            ours.sort_index(), theirs.astype(float).sort_index(), check_names=False
        )

    def test_marketing_is_22_percent_over_budget_in_q3_2026(self) -> None:
        budget = pd.read_excel(
            FIXTURES_DIR / "zoho_budget_expenses.xlsx", sheet_name="Budget vs Actual"
        )
        q3 = budget[(budget["Month"] >= "2026-07-01") & (budget["Month"] < "2026-10-01")]
        planned = q3[q3["Department"] == "Marketing"]["Budget (INR)"].sum()
        expenses = pd.DataFrame(demo_orgs()[MAIN_ORG].tables["expenses"])
        spent = expenses[
            (expenses["date"] >= "2026-07-01")
            & (expenses["date"] < "2026-10-01")
            & (expenses["account_name"] == "Marketing")
        ]["total"].sum()
        assert round((spent / planned - 1) * 100, 1) == 22.0

    def test_the_demo_organisation_reads_through_the_connector(self) -> None:
        fake = StandInZohoTransport()
        setup = zoho_test_setup(fake)
        c = connector(setup)
        sizes = {table: len(fetch(c, table).frame) for table in TABLES}
        assert sizes["expenses"] == 21 * 5 * 3
        assert sizes["items"] == 40
        assert all(sizes.values())
        assert fake.writes_received == 0


def test_the_stand_in_is_never_used_with_real_credentials() -> None:
    from app.config import Settings
    from app.connectors.zoho_books import default_setup

    real = default_setup(
        Settings(
            _env_file=None,
            app_env="development",
            zoho_client_id="1000.REAL",
            zoho_client_secret="real-secret",
        )
    )
    assert real.mode == "zoho" and real.transport is None
    off = default_setup(
        Settings(_env_file=None, app_env="production", master_key="m" * 32, jwt_secret="j" * 32)
    )
    assert off.mode == "off" and not off.available
    dev = default_setup(Settings(_env_file=None, app_env="development"))
    assert dev.mode == "stand_in" and dev.region == "in"
    assert Path(FIXTURES_DIR).name == "fixtures"
