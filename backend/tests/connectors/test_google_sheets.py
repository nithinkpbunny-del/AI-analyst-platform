"""Google Sheets connector against recorded Google answers, and the development stand-in (no live calls)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pandas as pd
import pytest

from app.config import Settings
from app.connectors.base import ConnectorError, DatasetInfo, ReadOnlyViolation
from app.connectors.google_sheets import (
    GoogleSheetsConnector,
    GoogleSheetsSetup,
    Spreadsheet,
    _email_from_id_token,
    authorization_url,
    check_scopes,
    default_setup,
    exchange_code,
)
from app.connectors.google_stand_in import (
    ALL_FILES,
    StandInGoogleTransport,
    issue_code,
    stand_in_files,
)
from tests.recorded_google import (
    ACCOUNT,
    CLIENT_ID,
    DELETED_ID,
    NO_ACCESS_ID,
    REFRESH_TOKEN,
    SHEET_ID,
    RecordedGoogle,
    recorded_setup,
)

FIXTURES = Path(__file__).resolve().parents[3] / "eval" / "fixtures"


def connector(
    transport: RecordedGoogle, *ids: str, refresh_token: str = REFRESH_TOKEN
) -> GoogleSheetsConnector:
    return GoogleSheetsConnector(
        setup=recorded_setup(transport),
        refresh_token=refresh_token,
        spreadsheets=[Spreadsheet(id=i, name="Targets file") for i in (ids or (SHEET_ID,))],
        timezone="Asia/Kolkata",
        max_rows=1000,
    )


class TestOAuth:
    def test_exchange_returns_tokens_and_the_account(
        self, google_transport: RecordedGoogle
    ) -> None:
        setup = recorded_setup(google_transport)
        tokens = exchange_code(setup, setup.http(), code="4/test-code", code_verifier="v" * 64)
        assert tokens.refresh_token == REFRESH_TOKEN
        assert tokens.email == ACCOUNT
        [call] = google_transport.calls
        assert call.form["code_verifier"] == "v" * 64
        assert call.form["redirect_uri"] == "http://localhost:8000/connections/google/callback"

    @pytest.mark.parametrize(
        "extra",
        ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
    )
    def test_any_scope_that_could_write_is_refused(self, extra: str) -> None:
        from app.connectors.google_sheets import REQUIRED_SCOPES

        with pytest.raises(ConnectorError, match="more access"):
            check_scopes(frozenset(REQUIRED_SCOPES | {extra}))

    def test_both_read_scopes_are_required(self) -> None:
        with pytest.raises(ConnectorError, match="tick both boxes"):
            check_scopes(frozenset({"https://www.googleapis.com/auth/drive.file"}))

    def test_the_email_must_be_for_this_app_and_verified(self) -> None:
        def token(**claims: object) -> str:
            body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
            return f"e30.{body}.sig"

        assert (
            _email_from_id_token(
                token(aud=CLIENT_ID, email="a@b.in", email_verified=True), CLIENT_ID
            )
            == "a@b.in"
        )
        assert (
            _email_from_id_token(token(aud="other", email="a@b.in", email_verified=True), CLIENT_ID)
            is None
        )
        assert _email_from_id_token(token(aud=CLIENT_ID, email="a@b.in"), CLIENT_ID) is None
        assert _email_from_id_token("garbage", CLIENT_ID) is None

    def test_authorization_url_for_the_stand_in(self) -> None:
        setup = GoogleSheetsSetup(
            mode="stand_in", client_id="stand-in-client", api_base_url="http://localhost:8000"
        )
        url = urlsplit(authorization_url(setup, state="s" * 32, code_challenge="c" * 43))
        assert (
            f"{url.scheme}://{url.netloc}{url.path}"
            == "http://localhost:8000/connections/google/stand-in"
        )
        assert parse_qs(url.query)["prompt"] == ["consent"]

    def test_modes(self, settings: Settings) -> None:
        assert default_setup(settings).mode == "off"  # APP_ENV=test without credentials
        dev = settings.model_copy(update={"app_env": "development"})
        assert default_setup(dev).mode == "stand_in"
        real = settings.model_copy(
            update={
                "google_oauth_client_id": "id.apps.googleusercontent.com",
                "google_oauth_client_secret": settings.jwt_secret,
                "google_picker_api_key": settings.jwt_secret,
                "google_cloud_project_number": "123456789012",
            }
        )
        assert default_setup(real).mode == "google"
        half = real.model_copy(update={"google_cloud_project_number": ""})
        assert default_setup(half).mode == "off"  # sign-in credentials alone are not enough


class TestReading:
    def test_tabs_are_datasets_without_hidden_or_chart_sheets(
        self, google_transport: RecordedGoogle
    ) -> None:
        found = connector(google_transport).list_datasets()
        assert found == [
            DatasetInfo(ref=f"{SHEET_ID}/0", name="UrbanCart targets 2026 · Targets"),
            DatasetInfo(ref=f"{SHEET_ID}/1702384", name="UrbanCart targets 2026 · Notes"),
        ]

    def test_fetch_reads_values_typed(self, google_transport: RecordedGoogle) -> None:
        result = connector(google_transport).fetch(DatasetInfo(f"{SHEET_ID}/0", "Targets"))
        assert result.source_version == "41"
        assert result.total_row_dropped
        frame = result.frame
        assert len(frame) == 12
        assert frame.loc[0, "month"] == pd.Timestamp("2026-07-01")  # "01/07/2026", day first
        assert frame["revenue_target_inr"].dtype == "Int64"
        values_call = next(c for c in google_transport.calls if "/values/" in c.url)
        assert values_call.url.endswith("/values/'Targets'")
        assert values_call.query == {
            "majorDimension": "ROWS",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        }
        assert values_call.authorization == "Bearer ya29.test-access-token"

    def test_one_token_refresh_for_many_reads(self, google_transport: RecordedGoogle) -> None:
        sheets = connector(google_transport)
        for dataset in sheets.list_datasets():
            sheets.fetch(dataset)
        token_calls = [c for c in google_transport.calls if c.url.endswith("/token")]
        assert len(token_calls) == 1
        metadata_calls = [
            c for c in google_transport.calls if c.url.endswith(f"spreadsheets/{SHEET_ID}")
        ]
        assert len(metadata_calls) == 1  # tab list cached per connector

    def test_only_reads_are_sent(self, google_transport: RecordedGoogle) -> None:
        sheets = connector(google_transport)
        for dataset in sheets.list_datasets():
            sheets.fetch(dataset)
        sheets.test()
        posts = {url for method, url in google_transport.methods() if method != "GET"}
        assert posts == {"https://oauth2.googleapis.com/token"}

    def test_a_write_through_the_connector_client_is_impossible(
        self, google_transport: RecordedGoogle
    ) -> None:
        sheets = connector(google_transport)
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/A1:append"
        with pytest.raises(ReadOnlyViolation):
            sheets._http.request("POST", url, json={"values": [[1]]})
        with pytest.raises(ReadOnlyViolation):
            sheets._http.request("PUT", url, json={"values": [[1]]})
        assert not any("append" in c.url for c in google_transport.calls)

    def test_a_revoked_token_needs_a_new_sign_in(self, google_transport: RecordedGoogle) -> None:
        sheets = connector(google_transport, refresh_token="1//revoked-refresh-token")
        with pytest.raises(ConnectorError) as caught:
            sheets.list_datasets()
        assert caught.value.kind == "needs_reauth"
        result = sheets.test()
        assert not result.ok

    @pytest.mark.parametrize(
        ("spreadsheet", "message"),
        [(NO_ACCESS_ID, "no longer lets this app read"), (DELETED_ID, "was deleted")],
    )
    def test_lost_spreadsheets_say_what_happened(
        self, google_transport: RecordedGoogle, spreadsheet: str, message: str
    ) -> None:
        with pytest.raises(ConnectorError, match=message) as caught:
            connector(google_transport, spreadsheet).list_datasets()
        assert caught.value.kind == "not_found"

    def test_a_deleted_tab(self, google_transport: RecordedGoogle) -> None:
        with pytest.raises(ConnectorError, match="tab of"):
            connector(google_transport).fetch(DatasetInfo(f"{SHEET_ID}/999", "Old tab"))

    def test_an_outage_is_retried_then_reported(self, google_transport: RecordedGoogle) -> None:
        google_transport.override(
            "GET", f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}", 503, {}
        )
        with pytest.raises(ConnectorError) as caught:
            connector(google_transport).list_datasets()
        assert caught.value.kind == "unavailable"
        attempts = [c for c in google_transport.calls if c.url.endswith(f"spreadsheets/{SHEET_ID}")]
        assert len(attempts) == 4

    def test_version_unknown_when_drive_cannot_tell(self, google_transport: RecordedGoogle) -> None:
        google_transport.override(
            "GET", f"https://www.googleapis.com/drive/v3/files/{SHEET_ID}", 403, {}
        )
        assert connector(google_transport).source_version(DatasetInfo(f"{SHEET_ID}/0", "x")) is None

    def test_test_reports_read_only_access(self, google_transport: RecordedGoogle) -> None:
        result = connector(google_transport).test()
        assert result.ok
        assert result.message == "Signed in to Google with read-only access. 1 spreadsheet, 2 tabs."


class TestStandIn:
    def stand_in(self, tmp: Path | None = None) -> GoogleSheetsConnector:
        transport = StandInGoogleTransport(tmp or FIXTURES)
        setup = GoogleSheetsSetup(
            mode="stand_in", client_id="stand-in-client", client_secret="x", transport=transport
        )
        tokens = exchange_code(
            setup, setup.http(), code=issue_code("dev@urbancart.in"), code_verifier="v"
        )
        assert tokens.email == "dev@urbancart.in"
        assert tokens.refresh_token is not None
        return GoogleSheetsConnector(
            setup=setup,
            refresh_token=tokens.refresh_token,
            spreadsheets=[Spreadsheet(f.id, f.name) for f in stand_in_files(tmp or FIXTURES)],
            timezone="Asia/Kolkata",
            max_rows=10_000,
        )

    def test_the_built_in_sample_works_without_fixtures(self, tmp_path: Path) -> None:
        sheets = self.stand_in(tmp_path)
        [dataset] = sheets.list_datasets()
        assert dataset.name == "Outlet sales (sample) · Sales"
        result = sheets.fetch(dataset)
        columns = {c.name: (c.type, c.unit) for c in result.columns}
        assert columns["date"] == ("date", None)
        assert columns["sales_inr"] == ("integer", "INR")
        assert columns["discount_pct"][1] == "percent"
        assert len(result.frame) == 84

    @pytest.mark.skipif(
        not (FIXTURES / "marketing_sheet.xlsx").exists(), reason="run `make fixtures`"
    )
    def test_the_demo_workbooks_are_offered_when_generated(self) -> None:
        assert [f.id for f in stand_in_files()] == [f.id for f in ALL_FILES]
        names = [d.name for d in self.stand_in().list_datasets()]
        assert "UrbanCart marketing plan · Targets" in names
        assert "UrbanCart budget vs actual · Budget vs Actual" in names

    def test_the_stand_in_refuses_writes_and_unknown_tokens(self) -> None:
        client = httpx.Client(transport=StandInGoogleTransport())
        assert client.put("https://sheets.googleapis.com/v4/spreadsheets/x").status_code == 405
        assert (
            client.get(
                "https://sheets.googleapis.com/v4/spreadsheets/standin-sample-outlets"
            ).status_code
            == 401
        )
        bad = client.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "refresh_token", "refresh_token": "nope"},
        )
        assert bad.status_code == 400
