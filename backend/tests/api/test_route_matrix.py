"""The route-test rule (BUILD_SPEC 0.6, 15) for every workspace route: success, validation error, 401 (signed
out), 403 (wrong role, and Owners without 2FA) and 404 for another workspace's ids.

``test_every_route_is_classified`` lists the whole API: a new route fails it until it is added below as public,
person-scoped ("self", tested in its own file) or a workspace route with a ``Case``. That keeps the rule enforced
as later milestones add routes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.analytics.common import metric_info
from app.api.connections import _GOOGLE_STATE as GOOGLE_STATE_PREFIX
from app.auth.permissions import ROLE_PERMISSIONS, Permission
from app.config import Settings
from app.connectors.registry import ConnectorRuntime
from app.dictionary import service as dictionary_service
from app.llm import keys, usage
from app.llm.base import Usage
from app.llm.fake import FakeLLMFactory
from app.models import (
    AuditLog,
    Conversation,
    DashboardTile,
    Dataset,
    FileVersion,
    Goal,
    Invitation,
    Membership,
    SavedQuestion,
)
from app.security.secrets import SecretStore, key_wrapper
from tests.conftest import FakeClock
from tests.customer_db import READER
from tests.datasources import (
    csv_bytes,
    make_database_connection,
    make_file_connection,
    make_razorpay_connection,
    make_sheets_connection,
    make_tally_connection,
    make_zoho_connection,
)
from tests.recorded_google import NO_ACCESS_ID, SHEET_ID
from tests.recorded_razorpay import KEY_ID, KEY_SECRET
from tests.recorded_shopify import SHOP
from tests.recorded_shopify import code as shopify_code
from tests.recorded_zoho import OTHER_ORG_ID as ZOHO_OTHER_ORG
from tests.recorded_zoho import code as zoho_code
from tests.tenancy import World, add_member, build_world, token_for

P = Permission

PUBLIC = {
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("GET", "/auth/csrf"),
    ("POST", "/auth/signup"),
    ("POST", "/auth/verify-email"),
    ("POST", "/auth/resend-verification"),
    ("POST", "/auth/login"),
    ("POST", "/auth/login/verify"),
    ("POST", "/auth/login/resend-code"),
    ("POST", "/auth/login/setup"),
    ("POST", "/auth/login/setup/resend-code"),
    ("POST", "/auth/login/setup/confirm"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("POST", "/auth/secure-account"),
    ("GET", "/auth/providers"),
    ("GET", "/auth/google/start"),
    ("GET", "/auth/google/callback"),
    ("POST", "/auth/google/finish"),
    ("POST", "/auth/google/signup-info"),
    ("POST", "/auth/google/signup"),
    ("POST", "/invitations/preview"),
    ("POST", "/invitations/accept"),
    ("POST", "/invitations/accept-sign-in"),
    # The browser returns from Google; nothing is exchanged here (tested in test_connections_google).
    ("GET", "/connections/google/callback"),
    # The browser returns from Shopify; only the HMAC is checked here (tested in test_connections_shopify).
    ("GET", "/connections/shopify/callback"),
    # The browser returns from Zoho; only the state and data centre are checked (test_connections_zoho).
    ("GET", "/connections/zoho/callback"),
}

# Signed in, but about the person rather than a workspace: tested in test_auth/test_two_factor/test_devices,
# test_workspaces and test_invitations (each with 401 and, where ids are involved, 404 for someone else's).
SELF = {
    ("GET", "/auth/me"),
    ("POST", "/auth/switch-organization"),
    ("POST", "/auth/2fa/setup"),
    ("POST", "/auth/2fa/setup/resend-code"),
    ("POST", "/auth/2fa/setup/confirm"),
    ("POST", "/auth/2fa/send-code"),
    ("POST", "/auth/2fa/recovery-codes"),
    ("POST", "/auth/2fa/disable"),
    ("GET", "/auth/sessions"),
    ("POST", "/auth/sessions/revoke-others"),
    ("DELETE", "/auth/sessions/{session_id}"),
    ("POST", "/invitations/accept-signed-in"),
}

# A person's own conversations and saved questions by id (M8): someone else's in the same workspace is 404 too, so
# one id cannot serve every role here. Tested in test_chat.py (TestPersonalRoutes): 401, 403 for Viewers, 404 for
# another person's and another workspace's ids, 422 for malformed ids, success for one's own.
PERSONAL = {
    ("GET", "/conversations/{conversation_id}"),
    ("PATCH", "/conversations/{conversation_id}"),
    ("DELETE", "/conversations/{conversation_id}"),
    ("POST", "/conversations/{conversation_id}/messages"),
    ("GET", "/conversations/{conversation_id}/messages/{message_id}"),
    ("DELETE", "/saved-questions/{question_id}"),
    # M10: long tasks, reports, exports and shares are personal too. Tested in test_tasks.py and
    # test_reports_sharing.py (TestPersonalRoutes): 401, 403 by role, 404 for others' and other workspaces' ids, 422.
    ("GET", "/tasks/{task_id}"),
    ("POST", "/tasks/{task_id}/cancel"),
    ("GET", "/reports/{report_id}"),
    ("GET", "/reports/{report_id}/export"),
    ("GET", "/conversations/{conversation_id}/messages/{message_id}/export"),
    ("GET", "/shares"),
    ("POST", "/shares"),
    ("DELETE", "/shares/{share_id}"),
    ("GET", "/shared/{share_id}"),
    ("GET", "/shared/{share_id}/export"),
    # M15: dashboard tiles are personal (someone else's tile is 404 for changes) and pinning needs one's own answer.
    # Tested in test_dashboard.py (TestPersonalRoutes): 401, 403 for Viewers, 404 for others' and other
    # workspaces' ids, 422 for malformed ids and bodies, success for one's own.
    ("POST", "/dashboard/tiles"),
    ("PATCH", "/dashboard/tiles/{tile_id}"),
    ("DELETE", "/dashboard/tiles/{tile_id}"),
}

# The Tally bridge's API (M16): a Tally PC calls these with a device token, not a person's session (pairing uses a
# one-time code). Tested in test_tally_bridge.py: 401 without a token, with a bad or revoked one; 400/429 for
# pairing codes; 413/415/422 for bodies; 409 for another company; rows always land in the device's own workspace.
DEVICE = {
    ("POST", "/bridge/tally/pair"),
    ("POST", "/bridge/tally/poll"),
    ("POST", "/bridge/tally/batches"),
    ("POST", "/bridge/tally/syncs/{sync_id}/complete"),
}

# Routes whose success depends on the workspace's state (M10): cancelling a deletion needs one to be requested.
# Tested in test_today_privacy.py (TestDeletion: success, 409, 401, 403 for Analysts, Viewers and Owners without 2FA).
# M18: confirming a plan change needs the state the preview allows (a saved key to switch to your own key).
# Tested in test_billing.py (TestPlanChange: 401, 403 for Analysts, Viewers and Owners without 2FA, 409, 422, success).
STATEFUL = {
    ("DELETE", "/org/deletion"),
    ("PUT", "/org/billing/plan"),
}


# What-if routes that read live data (M9): they need an uploaded connection and a dictionary, so the matrix cannot
# give them a 200. Tested in test_whatif.py (TestAccess: 401, 403 for Viewers and Owners without 2FA; TestWithData:
# success, another workspace gets 409/no data; TestValidation: 422).
DATA_ROUTES = {
    ("GET", "/whatif/values"),
    ("POST", "/whatif/run"),
}


@dataclass
class Fixtures:
    """Ids the cases fill into paths: ``mine`` belong to UrbanCart (org A), ``theirs`` to Chaiwala (org B)."""

    world: World
    member_a: str
    member_b: str
    invitation_a: str
    invitation_b: str
    connection_a: str = ""
    connection_b: str = ""
    dataset_a: str = ""
    dataset_b: str = ""
    sheets_a: str = ""
    sheets_b: str = ""
    razorpay_a: str = ""
    razorpay_b: str = ""
    zoho_a: str = ""
    zoho_b: str = ""
    database_a: str = ""
    database_b: str = ""
    tally_a: str = ""
    tally_b: str = ""
    version_a: str = ""  # a readable file version that is not in use
    version_b: str = ""
    dict_version_a: str = ""  # a confirmed version of the business dictionary
    dict_version_b: str = ""
    goal_a: str = ""  # a goal on a metric of the workspace's dictionary
    goal_b: str = ""
    metric_a: str = ""
    draft_a: dict[str, Any] = field(default_factory=dict)  # org A's dictionary draft (content)
    google_state: str = ""
    shopify_state: str = ""
    zoho_state: str = ""

    def placeholders(self, *, theirs: bool) -> dict[str, str]:
        side = "b" if theirs else "a"
        return {
            name: getattr(self, f"{name}_{side}")
            for name in (
                "member",
                "invitation",
                "connection",
                "dataset",
                "sheets",
                "razorpay",
                "zoho",
                "database",
                "tally",
                "version",
                "dict_version",
                "goal",
            )
        }


@dataclass(frozen=True)
class Case:
    method: str
    path: str  # with {member} / {invitation} placeholders
    permission: Permission
    success: int
    body: dict[str, Any] | None = None
    # A request that fails validation (422): a bad body, or a bad query string appended to the path.
    invalid_body: dict[str, Any] | None = None
    invalid_query: str | None = None
    # Strings from org A that must never appear in org B's answer (for routes without ids).
    check_leak: bool = False
    params: dict[str, str] = field(default_factory=dict)
    # A file upload (multipart): the success call sends a small CSV, the validation call sends no file.
    upload: bool = False
    # A body that depends on the fixtures (e.g. a Google sign-in started by owner_a).
    body_from: Callable[[Fixtures], dict[str, Any]] | None = None

    @property
    def has_id(self) -> bool:
        return "{" in self.path

    def url(self, ids: Fixtures, *, theirs: bool = False) -> str:
        return self.path.format(**ids.placeholders(theirs=theirs))


DATABASE_BODY = {
    "engine": "postgresql",
    "host": "db.example.com",
    "database": "customer_demo",
    "username": READER[0],
    "password": READER[1],
}

CASES: dict[tuple[str, str], Case] = {
    ("GET", "/team/members"): Case("GET", "/team/members", P.MEMBERS_MANAGE, 200, check_leak=True),
    ("PATCH", "/team/members/{membership_id}"): Case(
        "PATCH",
        "/team/members/{member}",
        P.MEMBERS_MANAGE,
        200,
        body={"role": "viewer"},
        invalid_body={"role": "boss"},
    ),
    ("DELETE", "/team/members/{membership_id}"): Case(
        "DELETE", "/team/members/{member}", P.MEMBERS_MANAGE, 204
    ),
    ("GET", "/team/invitations"): Case(
        "GET", "/team/invitations", P.MEMBERS_MANAGE, 200, check_leak=True
    ),
    ("POST", "/team/invitations"): Case(
        "POST",
        "/team/invitations",
        P.MEMBERS_MANAGE,
        201,
        body={"email": "dev@urbancart.in", "role": "analyst"},
        invalid_body={"email": "not-an-email", "role": "owner"},
    ),
    ("POST", "/team/invitations/{invitation_id}/resend"): Case(
        "POST", "/team/invitations/{invitation}/resend", P.MEMBERS_MANAGE, 200
    ),
    ("DELETE", "/team/invitations/{invitation_id}"): Case(
        "DELETE", "/team/invitations/{invitation}", P.MEMBERS_MANAGE, 204
    ),
    ("GET", "/org"): Case("GET", "/org", P.SHARED_VIEW, 200, check_leak=True),
    # M17: the industry templates are reference data, the same for everyone (needs a signed-in member).
    ("GET", "/org/templates"): Case("GET", "/org/templates", P.SHARED_VIEW, 200, check_leak=True),
    ("PATCH", "/org"): Case(
        "PATCH",
        "/org",
        P.ORG_MANAGE,
        200,
        body={"name": "UrbanCart India"},
        invalid_body={"timezone": "Mars/Olympus"},
    ),
    ("GET", "/audit"): Case(
        "GET", "/audit", P.AUDIT_VIEW_OWN, 200, invalid_query="?limit=0", check_leak=True
    ),
    ("GET", "/org/ai"): Case("GET", "/org/ai", P.ORG_MANAGE, 200, check_leak=True),
    ("PUT", "/org/ai/plan"): Case(
        "PUT",
        "/org/ai/plan",
        P.ORG_MANAGE,
        200,
        body={"plan": "managed"},
        invalid_body={"plan": "free"},
    ),
    ("PUT", "/org/ai/key"): Case(
        "PUT",
        "/org/ai/key",
        P.ORG_MANAGE,
        200,
        body={"provider": "gemini", "api_key": "AIzaSyMatrixTestKey000000000000000000"},
        invalid_body={"provider": "openai", "api_key": "short"},
    ),
    ("DELETE", "/org/ai/key"): Case("DELETE", "/org/ai/key", P.ORG_MANAGE, 204),
    ("POST", "/org/ai/check"): Case("POST", "/org/ai/check", P.ORG_MANAGE, 200),
    # M18: billing hooks (Owners only; nothing is charged). Behaviour in test_billing.py.
    ("GET", "/org/billing"): Case("GET", "/org/billing", P.ORG_MANAGE, 200, check_leak=True),
    ("POST", "/org/billing/plan-change/preview"): Case(
        "POST",
        "/org/billing/plan-change/preview",
        P.ORG_MANAGE,
        200,
        body={"plan": "byok"},
        invalid_body={"plan": "gold"},
    ),
    ("GET", "/org/billing/summary"): Case(
        "GET",
        "/org/billing/summary",
        P.ORG_MANAGE,
        200,
        invalid_query="?month=2026-13",
        check_leak=True,
    ),
    ("GET", "/org/billing/summary.csv"): Case(
        "GET",
        "/org/billing/summary.csv",
        P.ORG_MANAGE,
        200,
        invalid_query="?month=2026-13",
        check_leak=True,
    ),
    ("GET", "/org/usage"): Case(
        "GET", "/org/usage", P.ORG_MANAGE, 200, invalid_query="?month=2026-13", check_leak=True
    ),
    ("GET", "/audit/export.csv"): Case(
        "GET",
        "/audit/export.csv",
        P.AUDIT_VIEW_OWN,
        200,
        invalid_query="?from=yesterday",
        check_leak=True,
    ),
    ("GET", "/connections"): Case("GET", "/connections", P.CONNECTIONS_VIEW, 200, check_leak=True),
    ("GET", "/connections/access"): Case(
        "GET", "/connections/access", P.CONNECTIONS_MANAGE, 200, check_leak=True
    ),
    ("PUT", "/connections/access/{membership_id}"): Case(
        "PUT",
        "/connections/access/{member}",
        P.CONNECTIONS_MANAGE,
        200,
        body={"access": "all"},
        invalid_body={"access": "some"},
    ),
    ("POST", "/connections/files"): Case(
        "POST", "/connections/files", P.CONNECTIONS_MANAGE, 201, upload=True
    ),
    ("POST", "/connections/{connection_id}/files"): Case(
        "POST", "/connections/{connection}/files", P.CONNECTIONS_MANAGE, 200, upload=True
    ),
    ("GET", "/connections/{connection_id}"): Case(
        "GET", "/connections/{connection}", P.CONNECTIONS_VIEW, 200
    ),
    ("PATCH", "/connections/{connection_id}"): Case(
        "PATCH",
        "/connections/{connection}",
        P.CONNECTIONS_MANAGE,
        200,
        body={"name": "Outlet sales 2026"},
        invalid_body={"name": ""},
    ),
    ("DELETE", "/connections/{connection_id}"): Case(
        "DELETE", "/connections/{connection}", P.CONNECTIONS_MANAGE, 204
    ),
    ("POST", "/connections/{connection_id}/test"): Case(
        "POST", "/connections/{connection}/test", P.CONNECTIONS_MANAGE, 200
    ),
    ("POST", "/connections/{connection_id}/refresh"): Case(
        "POST", "/connections/{connection}/refresh", P.CONNECTIONS_MANAGE, 200
    ),
    ("GET", "/connections/{connection_id}/datasets/{dataset_id}/preview"): Case(
        "GET",
        "/connections/{connection}/datasets/{dataset}/preview",
        P.CONNECTIONS_VIEW,
        200,
    ),
    ("POST", "/connections/{connection_id}/versions/{version_id}/use"): Case(
        "POST",
        "/connections/{connection}/versions/{version}/use",
        P.CONNECTIONS_MANAGE,
        200,
    ),
    ("DELETE", "/connections/{connection_id}/versions/{version_id}"): Case(
        "DELETE", "/connections/{connection}/versions/{version}", P.CONNECTIONS_MANAGE, 204
    ),
    ("GET", "/dictionary"): Case("GET", "/dictionary", P.CONNECTIONS_VIEW, 200, check_leak=True),
    ("PUT", "/dictionary/draft"): Case(
        "PUT",
        "/dictionary/draft",
        P.DICTIONARY_EDIT,
        200,
        body_from=lambda ids: ids.draft_a,
        invalid_body={"datasets": 1},
    ),
    ("POST", "/dictionary/draft"): Case(
        "POST",
        "/dictionary/draft",
        P.DICTIONARY_EDIT,
        202,
        body={},
        invalid_body={"fresh_ai": "maybe"},
    ),
    ("DELETE", "/dictionary/draft"): Case("DELETE", "/dictionary/draft", P.DICTIONARY_EDIT, 204),
    ("POST", "/dictionary/draft/confirm"): Case(
        "POST", "/dictionary/draft/confirm", P.DICTIONARY_EDIT, 200
    ),
    ("GET", "/dictionary/versions/{version_id}"): Case(
        "GET", "/dictionary/versions/{dict_version}", P.CONNECTIONS_VIEW, 200
    ),
    ("GET", "/dictionary/export.yaml"): Case(
        "GET",
        "/dictionary/export.yaml",
        P.CONNECTIONS_VIEW,
        200,
        invalid_query="?version_id=nope",
        check_leak=True,
    ),
    ("POST", "/dictionary/replace"): Case(
        "POST",
        "/dictionary/replace",
        P.DICTIONARY_EDIT,
        200,
        body_from=lambda ids: {
            "from_connection_id": ids.connection_a,
            "to_connection_id": ids.sheets_a,
            "remove_old": False,
        },
        invalid_body={"from_connection_id": "not-a-uuid"},
    ),
    ("GET", "/conversations"): Case("GET", "/conversations", P.ASK, 200, check_leak=True),
    ("POST", "/conversations"): Case(
        "POST",
        "/conversations",
        P.ASK,
        201,
        body={"question": "How did sales do last month?"},
        invalid_body={"question": ""},
    ),
    ("GET", "/whatif/options"): Case("GET", "/whatif/options", P.ASK, 200, check_leak=True),
    ("GET", "/saved-questions"): Case(
        "GET", "/saved-questions", P.SAVED_QUESTIONS, 200, check_leak=True
    ),
    ("POST", "/saved-questions"): Case(
        "POST",
        "/saved-questions",
        P.SAVED_QUESTIONS,
        201,
        body={"text": "Why did South fall?"},
        invalid_body={"text": ""},
    ),
    ("GET", "/tasks"): Case("GET", "/tasks", P.ASK, 200, check_leak=True),
    ("POST", "/tasks"): Case(
        "POST",
        "/tasks",
        P.ASK,
        201,
        body={"prompt": "Review last quarter's sales by outlet."},
        invalid_body={"prompt": ""},
    ),
    ("GET", "/reports"): Case(
        "GET", "/reports", P.SHARED_VIEW, 200, invalid_query="?source=export", check_leak=True
    ),
    ("GET", "/shared"): Case("GET", "/shared", P.SHARED_VIEW, 200, check_leak=True),
    ("GET", "/briefings/settings"): Case(
        "GET", "/briefings/settings", P.SHARED_VIEW, 200, check_leak=True
    ),
    ("PUT", "/briefings/settings"): Case(
        "PUT",
        "/briefings/settings",
        P.SHARED_VIEW,
        200,
        body={"local_time": "07:00"},
        invalid_body={"frequency": "hourly"},
    ),
    ("GET", "/briefings"): Case("GET", "/briefings", P.SHARED_VIEW, 200, check_leak=True),
    ("POST", "/briefings/send-now"): Case("POST", "/briefings/send-now", P.SHARED_VIEW, 202),
    ("GET", "/today"): Case("GET", "/today", P.SHARED_VIEW, 200, check_leak=True),
    # M15: goals and the dashboard.
    ("GET", "/goals"): Case("GET", "/goals", P.GOALS_VIEW, 200, check_leak=True),
    ("GET", "/goals/options"): Case("GET", "/goals/options", P.GOALS_EDIT, 200, check_leak=True),
    ("POST", "/goals"): Case(
        "POST",
        "/goals",
        P.GOALS_EDIT,
        201,
        body_from=lambda ids: {
            "metric": ids.metric_a,
            "target": 100000,
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
        },
        invalid_body={"metric": "", "target": 1, "period_start": "x", "period_end": "y"},
    ),
    ("PATCH", "/goals/{goal_id}"): Case(
        "PATCH",
        "/goals/{goal}",
        P.GOALS_EDIT,
        200,
        body={"target": 250000},
        invalid_body={"target": -1},
    ),
    ("DELETE", "/goals/{goal_id}"): Case("DELETE", "/goals/{goal}", P.GOALS_EDIT, 204),
    ("POST", "/goals/refresh"): Case(
        "POST",
        "/goals/refresh",
        P.GOALS_VIEW,
        202,
        body={},
        invalid_body={"force": "maybe"},
        check_leak=True,
    ),
    ("GET", "/dashboard"): Case("GET", "/dashboard", P.DASHBOARD_VIEW, 200, check_leak=True),
    ("PUT", "/dashboard/order"): Case(
        "PUT",
        "/dashboard/order",
        P.DASHBOARD_EDIT,
        200,
        body={"tile_ids": []},
        invalid_body={"tile_ids": "all"},
        check_leak=True,
    ),
    ("POST", "/dashboard/refresh"): Case(
        "POST",
        "/dashboard/refresh",
        P.DASHBOARD_VIEW,
        202,
        body={},
        invalid_body={"force": "maybe"},
        check_leak=True,
    ),
    ("GET", "/org/export.zip"): Case("GET", "/org/export.zip", P.ORG_MANAGE, 200),
    ("POST", "/org/deletion"): Case(
        "POST",
        "/org/deletion",
        P.ORG_MANAGE,
        202,
        body={"confirm_name": "UrbanCart"},
        invalid_body={"confirm_name": ""},
    ),
    ("POST", "/connections/google/start"): Case(
        "POST",
        "/connections/google/start",
        P.CONNECTIONS_MANAGE,
        200,
        body={},
        invalid_body={"connection_id": "not-a-uuid"},
    ),
    ("POST", "/connections/google/finish"): Case(
        "POST",
        "/connections/google/finish",
        P.CONNECTIONS_MANAGE,
        200,
        body_from=lambda ids: {"state": ids.google_state, "code": "4/test-code"},
        invalid_body={"state": "short", "code": ""},
    ),
    ("POST", "/connections/shopify/start"): Case(
        "POST",
        "/connections/shopify/start",
        P.CONNECTIONS_MANAGE,
        200,
        body={"shop": "urbancart-demo"},
        invalid_body={"connection_id": "not-a-uuid"},
    ),
    ("POST", "/connections/shopify/finish"): Case(
        "POST",
        "/connections/shopify/finish",
        P.CONNECTIONS_MANAGE,
        200,
        body_from=lambda ids: {"state": ids.shopify_state, "code": shopify_code()},
        invalid_body={"state": "short", "code": ""},
    ),
    ("POST", "/connections/zoho/start"): Case(
        "POST",
        "/connections/zoho/start",
        P.CONNECTIONS_MANAGE,
        200,
        body={},
        invalid_body={"connection_id": "not-a-uuid"},
    ),
    ("POST", "/connections/zoho/finish"): Case(
        "POST",
        "/connections/zoho/finish",
        P.CONNECTIONS_MANAGE,
        200,
        body_from=lambda ids: {"state": ids.zoho_state, "code": zoho_code()},
        invalid_body={"state": "short", "code": ""},
    ),
    ("PUT", "/connections/{connection_id}/zoho/organization"): Case(
        "PUT",
        "/connections/{zoho}/zoho/organization",
        P.CONNECTIONS_MANAGE,
        200,
        body={"organization_id": ZOHO_OTHER_ORG},
        invalid_body={"organization_id": "not a number"},
    ),
    ("POST", "/connections/database"): Case(
        "POST",
        "/connections/database",
        P.CONNECTIONS_MANAGE,
        201,
        body=DATABASE_BODY,
        invalid_body={**DATABASE_BODY, "engine": "oracle"},
    ),
    ("PUT", "/connections/{connection_id}/database/credentials"): Case(
        "PUT",
        "/connections/{database}/database/credentials",
        P.CONNECTIONS_MANAGE,
        200,
        body=DATABASE_BODY,
        invalid_body={**DATABASE_BODY, "port": 0},
    ),
    ("GET", "/connections/{connection_id}/database/tables"): Case(
        "GET", "/connections/{database}/database/tables", P.CONNECTIONS_MANAGE, 200
    ),
    ("PUT", "/connections/{connection_id}/database/tables"): Case(
        "PUT",
        "/connections/{database}/database/tables",
        P.CONNECTIONS_MANAGE,
        200,
        body={"tables": ["public.orders"]},
        invalid_body={"tables": "public.orders"},
    ),
    ("POST", "/connections/tally"): Case(
        "POST",
        "/connections/tally",
        P.CONNECTIONS_MANAGE,
        201,
        body={"name": "Tally"},
        invalid_body={"name": ""},
    ),
    ("POST", "/connections/{connection_id}/tally/pairing-code"): Case(
        "POST", "/connections/{tally}/tally/pairing-code", P.CONNECTIONS_MANAGE, 200
    ),
    ("POST", "/connections/{connection_id}/tally/unpair"): Case(
        "POST", "/connections/{tally}/tally/unpair", P.CONNECTIONS_MANAGE, 200
    ),
    ("POST", "/connections/razorpay"): Case(
        "POST",
        "/connections/razorpay",
        P.CONNECTIONS_MANAGE,
        201,
        body={"key_id": KEY_ID, "key_secret": KEY_SECRET},
        invalid_body={"key_id": "not-a-key", "key_secret": "x"},
    ),
    ("PUT", "/connections/{connection_id}/razorpay/key"): Case(
        "PUT",
        "/connections/{razorpay}/razorpay/key",
        P.CONNECTIONS_MANAGE,
        200,
        body={"key_id": KEY_ID, "key_secret": KEY_SECRET},
        invalid_body={"key_id": "not-a-key", "key_secret": "x"},
    ),
    ("POST", "/connections/{connection_id}/google/picker-token"): Case(
        "POST", "/connections/{sheets}/google/picker-token", P.CONNECTIONS_MANAGE, 200
    ),
    ("PUT", "/connections/{connection_id}/google/spreadsheets"): Case(
        "PUT",
        "/connections/{sheets}/google/spreadsheets",
        P.CONNECTIONS_MANAGE,
        200,
        body={"spreadsheet_ids": [SHEET_ID]},
        invalid_body={"spreadsheet_ids": ["not a spreadsheet id!"]},
    ),
}


def _api_routes(client: TestClient) -> set[tuple[str, str]]:
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return {(method.upper(), path) for path, item in paths.items() for method in item}


def test_every_route_is_classified(client: TestClient) -> None:
    routes = _api_routes(client)
    unclassified = routes - PUBLIC - SELF - PERSONAL - DATA_ROUTES - STATEFUL - DEVICE - set(CASES)
    assert unclassified == set(), (
        "New routes need tests: add them to PUBLIC, SELF or CASES in tests/api/test_route_matrix.py"
    )
    assert (
        PUBLIC | SELF | PERSONAL | DATA_ROUTES | STATEFUL | DEVICE | set(CASES)
    ) - routes == set(), "stale entries"


@pytest.fixture
def ids(
    db: DbSession,
    settings: Settings,
    clock: FakeClock,
    runtime: ConnectorRuntime,
    fake_redis: fakeredis.FakeRedis,
    llm_factory: FakeLLMFactory,
) -> Fixtures:
    world = build_world(db, settings, clock.now)
    # An Owner who never turned 2FA on (e.g. promoted later): every workspace route must refuse them.
    nisha = add_member(db, world.org_a.id, email="nisha@urbancart.in", role="owner", name="Nisha")
    world.tokens["owner_no_2fa"] = token_for(db, settings, clock.now, nisha, world.org_a.id)
    invitations = {}
    for org, email in ((world.org_a, "dev@urbancart.in"), (world.org_b, "dev@chaiwala.in")):
        invitation = Invitation(
            org_id=org.id,
            email=email,
            role="viewer",
            token_hash=f"hash-{email}",
            expires_at=clock.now + timedelta(days=7),
            invited_by=world.owner_a.id if org is world.org_a else world.owner_b.id,
        )
        db.add(invitation)
        db.add(AuditLog(org_id=org.id, user_id=None, action="test.event", details={}, at=clock.now))
        db.flush()
        invitations[org.id] = str(invitation.id)
    # Org A has its own AI key and usage, so the leak checks have something to find.
    store = SecretStore(key_wrapper(settings))
    keys.store_key(
        db,
        org_id=world.org_a.id,
        provider="claude",
        api_key="sk-ant-urbancart-secret-key-0000000000",
        user_id=world.owner_a.id,
        secrets=store,
    )
    usage.record(
        db,
        org_id=world.org_a.id,
        user_id=world.owner_a.id,
        run_id=uuid.uuid4(),
        kind="question",
        provider="gemini",
        model="gemini-2.5-flash",
        usage=Usage(1000, 100),
        key_source="managed",
        at=clock.now,
    )
    # Org A has a conversation and a saved question, so the leak checks have something to find.
    conversation = Conversation(
        org_id=world.org_a.id,
        user_id=world.owner_a.id,
        title="UrbanCart Diwali plan",
        lens="business",
        last_message_at=clock.now,
    )
    db.add(conversation)
    db.add(SavedQuestion(org_id=world.org_a.id, user_id=world.owner_a.id, text="UrbanCart Diwali?"))
    db.commit()

    def membership(user_id: Any) -> str:
        return str(db.scalars(select(Membership.id).where(Membership.user_id == user_id)).one())

    # Connections in both workspaces: a file (with a dataset) and Google Sheets.
    files = {
        org.id: make_file_connection(db, runtime, org.id, name=f"{org.name} outlet sales")
        for org in (world.org_a, world.org_b)
    }
    sheets = {
        world.org_a.id: make_sheets_connection(db, runtime, world.org_a.id),
        world.org_b.id: make_sheets_connection(
            db,
            runtime,
            world.org_b.id,
            spreadsheet_ids=[NO_ACCESS_ID],
            spreadsheet_name="Chaiwala menu",
            sync=False,
        ),
    }

    def spare_version(org_id: Any) -> str:
        """A second, readable version of the file that is not in use (to use or discard)."""
        connection = files[org_id]
        version = FileVersion(
            org_id=org_id,
            connection_id=connection.id,
            storage_key=f"orgs/{org_id}/files/{connection.id}/spare",
            original_name="older.csv",
            sha256="0" * 64,
            size_bytes=10,
            version=2,
            status="ready",
        )
        db.add(version)
        db.commit()
        runtime.objects.put(version.storage_key, csv_bytes())
        return str(version.id)

    def dataset(org_id: Any) -> str:
        return str(
            db.scalars(select(Dataset.id).where(Dataset.connection_id == files[org_id].id)).one()
        )

    razorpay = {
        org.id: make_razorpay_connection(db, runtime, org.id, sync=False)
        for org in (world.org_a, world.org_b)
    }

    zoho = {
        world.org_a.id: make_zoho_connection(db, runtime, world.org_a.id, sync=False),
        # Workspace B's names differ, so the leak check can tell the two apart.
        world.org_b.id: make_zoho_connection(
            db,
            runtime,
            world.org_b.id,
            name="Zoho Books (Chaiwala)",
            org_names=("Chaiwala Books", "Chaiwala Labs"),
            sync=False,
        ),
    }

    databases = {
        org.id: make_database_connection(db, runtime, org.id, sync=False)
        for org in (world.org_a, world.org_b)
    }
    tallies = {org.id: make_tally_connection(db, org.id) for org in (world.org_a, world.org_b)}

    # Each workspace gets a confirmed dictionary (version 1) and a draft (version 2) continuing it.
    dictionaries: dict[Any, str] = {}
    drafts: dict[Any, dict[str, Any]] = {}
    for org, owner_user in ((world.org_a, world.owner_a), (world.org_b, world.owner_b)):
        dictionary_service.run_draft(runtime, llm_factory, org_id=org.id)
        with dictionary_service._session(runtime, org.id) as scoped:
            confirmed = dictionary_service.confirm(
                scoped, org.id, user_id=owner_user.id, at=clock.now
            )
            assert confirmed is not None
            dictionaries[org.id] = str(confirmed.id)
            scoped.commit()
        dictionary_service.run_draft(runtime, llm_factory, org_id=org.id, force=True)
        with dictionary_service._session(runtime, org.id) as scoped:
            draft = dictionary_service.current_draft(scoped, org.id)
            assert draft is not None
            drafts[org.id] = draft.content
            scoped.commit()
    db.expire_all()

    # A goal in each workspace on a metric of its dictionary; workspace A also shows a tile on everyone's
    # dashboard, so the leak checks have something to find.
    goals: dict[Any, str] = {}
    metrics: dict[Any, str] = {}
    for org, owner_user in ((world.org_a, world.owner_a), (world.org_b, world.owner_b)):
        with dictionary_service._session(runtime, org.id) as scoped:
            active = dictionary_service.active(scoped, org.id)
            assert active.dictionary is not None
            metric = next(
                key
                for key in active.dictionary.metrics
                if metric_info(active.dictionary, key).time_column
            )
            goal = Goal(
                org_id=org.id,
                name=f"{org.name} Diwali target",
                metric_key=metric,
                target_value=500000,
                direction="at_least",
                period_start=date(2026, 10, 1),
                period_end=date(2026, 10, 31),
                filters={},
                created_by=owner_user.id,
            )
            scoped.add(goal)
            if org is world.org_a:
                scoped.add(
                    DashboardTile(
                        org_id=org.id,
                        user_id=owner_user.id,
                        title="UrbanCart sales tile",
                        spec={"tables": [], "calls": [], "question": "UrbanCart?"},
                        position=0,
                        refresh_minutes=60,
                        shared=True,
                    )
                )
            scoped.flush()
            goals[org.id], metrics[org.id] = str(goal.id), metric
            scoped.commit()
    db.expire_all()

    # A Google sign-in started by owner_a, which finish() completes.
    state = "matrix-state-0000000000000000"
    fake_redis.set(
        f"{GOOGLE_STATE_PREFIX}{state}",
        json.dumps(
            {
                "org_id": str(world.org_a.id),
                "user_id": str(world.owner_a.id),
                "verifier": "v" * 64,
                "connection_id": None,
            }
        ),
    )
    # A Shopify sign-in started by owner_a whose callback passed the HMAC check, which finish() completes.
    shopify_state = "matrix-shopify-state-000000000"
    fake_redis.set(
        f"shopify:state:{shopify_state}",
        json.dumps(
            {
                "org_id": str(world.org_a.id),
                "user_id": str(world.owner_a.id),
                "shop": SHOP,
                "connection_id": None,
                "verified": True,
            }
        ),
    )
    # A Zoho sign-in started by owner_a whose callback came back from Zoho's India data centre.
    zoho_state = "matrix-zoho-state-0000000000000"
    fake_redis.set(
        f"zoho:state:{zoho_state}",
        json.dumps(
            {
                "org_id": str(world.org_a.id),
                "user_id": str(world.owner_a.id),
                "connection_id": None,
                "region": "in",
            }
        ),
    )
    return Fixtures(
        world=world,
        shopify_state=shopify_state,
        zoho_state=zoho_state,
        database_a=str(databases[world.org_a.id].id),
        database_b=str(databases[world.org_b.id].id),
        tally_a=str(tallies[world.org_a.id].id),
        tally_b=str(tallies[world.org_b.id].id),
        zoho_a=str(zoho[world.org_a.id].id),
        zoho_b=str(zoho[world.org_b.id].id),
        member_a=membership(world.analyst_a.id),
        member_b=membership(world.analyst_b.id),
        invitation_a=invitations[world.org_a.id],
        invitation_b=invitations[world.org_b.id],
        connection_a=str(files[world.org_a.id].id),
        connection_b=str(files[world.org_b.id].id),
        dataset_a=dataset(world.org_a.id),
        dataset_b=dataset(world.org_b.id),
        sheets_a=str(sheets[world.org_a.id].id),
        sheets_b=str(sheets[world.org_b.id].id),
        razorpay_a=str(razorpay[world.org_a.id].id),
        razorpay_b=str(razorpay[world.org_b.id].id),
        version_a=spare_version(world.org_a.id),
        version_b=spare_version(world.org_b.id),
        dict_version_a=dictionaries[world.org_a.id],
        dict_version_b=dictionaries[world.org_b.id],
        goal_a=goals[world.org_a.id],
        goal_b=goals[world.org_b.id],
        metric_a=metrics[world.org_a.id],
        draft_a=drafts[world.org_a.id],
        google_state=state,
    )


def call(
    client: TestClient,
    case: Case,
    url: str,
    headers: dict[str, str] | None,
    body: dict[str, Any] | None = None,
    *,
    ids: Fixtures | None = None,
    invalid: bool = False,
) -> Any:
    if case.upload:
        files = (
            None
            if invalid
            else {"file": ("sales.csv", b"Date,Sales\n01/01/2026,100\n", "text/csv")}
        )
        return client.request(
            case.method, url, headers=headers or {}, files=files, data={"name": "Sales"}
        )
    payload = body if body is not None else case.body
    if payload is None and case.body_from is not None and ids is not None:
        payload = case.body_from(ids)
    return client.request(case.method, url, headers=headers or {}, json=payload)


ROUTES = sorted(CASES.values(), key=lambda c: (c.path, c.method))
ROUTE_IDS = [f"{c.method} {c.path}" for c in ROUTES]
ROLES = ["owner_a", "analyst_a", "viewer_a"]


def _role(who: str) -> str:
    return who.split("_")[0]


@pytest.mark.parametrize("case", ROUTES, ids=ROUTE_IDS)
def test_signed_out_is_401(client: TestClient, ids: Fixtures, case: Case) -> None:
    response = call(client, case, case.url(ids), None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_signed_in"


@pytest.mark.parametrize("case", ROUTES, ids=ROUTE_IDS)
def test_a_bad_token_is_401(client: TestClient, ids: Fixtures, case: Case) -> None:
    response = call(client, case, case.url(ids), {"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


@pytest.mark.parametrize("who", ROLES)
@pytest.mark.parametrize("case", ROUTES, ids=ROUTE_IDS)
def test_roles(client: TestClient, ids: Fixtures, case: Case, who: str) -> None:
    response = call(client, case, case.url(ids), ids.world.headers(who), ids=ids)
    if case.permission in ROLE_PERMISSIONS[_role(who)]:
        assert response.status_code == case.success, response.text
    else:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("case", ROUTES, ids=ROUTE_IDS)
def test_owner_without_2fa_is_403(client: TestClient, ids: Fixtures, case: Case) -> None:
    response = call(client, case, case.url(ids), ids.world.headers("owner_no_2fa"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "two_factor_required"


@pytest.mark.parametrize(
    "case", [c for c in ROUTES if c.has_id], ids=lambda c: f"{c.method} {c.path}"
)
def test_another_workspaces_id_is_404(client: TestClient, ids: Fixtures, case: Case) -> None:
    response = call(client, case, case.url(ids, theirs=True), ids.world.headers("owner_a"), ids=ids)
    assert response.status_code == 404, response.text
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}


@pytest.mark.parametrize(
    "case", [c for c in ROUTES if c.has_id], ids=lambda c: f"{c.method} {c.path}"
)
def test_a_missing_id_looks_the_same(client: TestClient, ids: Fixtures, case: Case) -> None:
    missing = "0190c0de-0000-7000-8000-000000000000"
    url = case.path.format(**dict.fromkeys(ids.placeholders(theirs=False), missing))
    response = call(client, case, url, ids.world.headers("owner_a"), ids=ids)
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}


@pytest.mark.parametrize(
    "case", [c for c in ROUTES if c.has_id], ids=lambda c: f"{c.method} {c.path}"
)
def test_a_malformed_id_is_422(client: TestClient, ids: Fixtures, case: Case) -> None:
    url = case.path.format(**dict.fromkeys(ids.placeholders(theirs=False), "not-a-uuid"))
    response = call(client, case, url, ids.world.headers("owner_a"), ids=ids)
    assert response.status_code == 422


VALIDATED = [
    c for c in ROUTES if c.invalid_body is not None or c.invalid_query is not None or c.upload
]


@pytest.mark.parametrize("case", VALIDATED, ids=lambda c: f"{c.method} {c.path}")
def test_validation_error(client: TestClient, ids: Fixtures, case: Case) -> None:
    url = case.url(ids) + (case.invalid_query or "")
    response = call(
        client, case, url, ids.world.headers("owner_a"), case.invalid_body, ids=ids, invalid=True
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


LEAKY = [c for c in ROUTES if c.check_leak]


@pytest.mark.parametrize("case", LEAKY, ids=lambda c: f"{c.method} {c.path}")
@pytest.mark.parametrize("who", ["owner_b", "analyst_b"])
def test_nothing_from_another_workspace_appears(
    client: TestClient, ids: Fixtures, case: Case, who: str
) -> None:
    response = call(client, case, case.url(ids), ids.world.headers(who), ids=ids)
    text = response.text
    for marker in (
        "UrbanCart",
        "urbancart.in",
        str(ids.world.org_a.id),
        ids.member_a,
        ids.invitation_a,
        "sk-ant-urbancart",
        "Asha Rao",
    ):
        assert marker not in text


def test_every_permission_used_by_a_route_exists() -> None:
    used: set[Callable[..., Any] | Permission] = {c.permission for c in CASES.values()}
    assert used <= set(Permission)
