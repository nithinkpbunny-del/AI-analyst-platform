"""Zoho Books for tests: the stand-in (``app.connectors.zoho_stand_in``) behind a "real" setup, so no live calls.

The setup is in ``zoho`` mode (as with real credentials), so the development-only stand-in pages answer 404 in API
tests; the stand-in transport answers Zoho's accounts server and Books API in Zoho's formats, from the small
built-in sample organisation (no fixture files needed).
"""

from __future__ import annotations

import copy
from typing import Any

from app.connectors.zoho_books import ZohoSetup
from app.connectors.zoho_stand_in import (
    LABS_ORG,
    MAIN_ORG,
    REFRESH_PREFIX,
    STAND_IN_CLIENT_ID,
    STAND_IN_CLIENT_SECRET,
    StandInZohoTransport,
    issue_code,
    sample_orgs,
)

REFRESH_TOKEN = f"{REFRESH_PREFIX}9000"
ORG_ID = MAIN_ORG
OTHER_ORG_ID = LABS_ORG
ACCOUNTS_SERVER = "https://accounts.zoho.in"


def fake_zoho() -> StandInZohoTransport:
    # A copy per test: tests may change the sample's rows (e.g. to see an incremental read).
    return StandInZohoTransport(orgs=copy.deepcopy(sample_orgs()))


def zoho_test_setup(fake: StandInZohoTransport, **kwargs: Any) -> ZohoSetup:
    values: dict[str, Any] = {
        "mode": "zoho",
        "client_id": STAND_IN_CLIENT_ID,
        "client_secret": STAND_IN_CLIENT_SECRET,
        "transport": fake,
        "sleep": lambda _s: None,
        "api_base_url": "http://localhost:8000",
    } | kwargs
    return ZohoSetup(**values)


def code(scope: str | None = None) -> str:
    return issue_code(scope) if scope is not None else issue_code()


LABS_ORG_NAME = "UrbanCart Labs"
