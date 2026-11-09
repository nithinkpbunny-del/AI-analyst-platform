"""The route-test rule (BUILD_SPEC 0.6, 15) for every workspace route: success, validation error, 401 (signed
out), 403 (wrong role, and Owners without 2FA) and 404 for another workspace's ids.

``test_every_route_is_classified`` lists the whole API: a new route fails it until it is added below as public,
person-scoped ("self", tested in its own file) or a workspace route with a ``Case``. That keeps the rule enforced
as later milestones add routes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth.permissions import ROLE_PERMISSIONS, Permission
from app.config import Settings
from app.llm import keys, usage
from app.llm.base import Usage
from app.models import AuditLog, Invitation, Membership
from app.security.secrets import SecretStore, key_wrapper
from tests.conftest import FakeClock
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


@dataclass
class Fixtures:
    """Ids the cases fill into paths: ``mine`` belong to UrbanCart (org A), ``theirs`` to Chaiwala (org B)."""

    world: World
    member_a: str
    member_b: str
    invitation_a: str
    invitation_b: str


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

    @property
    def has_id(self) -> bool:
        return "{" in self.path

    def url(self, ids: Fixtures, *, theirs: bool = False) -> str:
        return self.path.format(
            member=ids.member_b if theirs else ids.member_a,
            invitation=ids.invitation_b if theirs else ids.invitation_a,
        )


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
}


def _api_routes(client: TestClient) -> set[tuple[str, str]]:
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return {(method.upper(), path) for path, item in paths.items() for method in item}


def test_every_route_is_classified(client: TestClient) -> None:
    routes = _api_routes(client)
    unclassified = routes - PUBLIC - SELF - set(CASES)
    assert unclassified == set(), (
        "New routes need tests: add them to PUBLIC, SELF or CASES in tests/api/test_route_matrix.py"
    )
    assert (PUBLIC | SELF | set(CASES)) - routes == set(), "stale entries"


@pytest.fixture
def ids(db: DbSession, settings: Settings, clock: FakeClock) -> Fixtures:
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
    db.commit()

    def membership(user_id: Any) -> str:
        return str(db.scalars(select(Membership.id).where(Membership.user_id == user_id)).one())

    return Fixtures(
        world=world,
        member_a=membership(world.analyst_a.id),
        member_b=membership(world.analyst_b.id),
        invitation_a=invitations[world.org_a.id],
        invitation_b=invitations[world.org_b.id],
    )


def call(
    client: TestClient,
    case: Case,
    url: str,
    headers: dict[str, str] | None,
    body: dict[str, Any] | None = None,
) -> Any:
    payload = body if body is not None else case.body
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
    response = call(client, case, case.url(ids), ids.world.headers(who))
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
    response = call(client, case, case.url(ids, theirs=True), ids.world.headers("owner_a"))
    assert response.status_code == 404, response.text
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}


@pytest.mark.parametrize(
    "case", [c for c in ROUTES if c.has_id], ids=lambda c: f"{c.method} {c.path}"
)
def test_a_missing_id_looks_the_same(client: TestClient, ids: Fixtures, case: Case) -> None:
    url = case.path.format(
        member="0190c0de-0000-7000-8000-000000000000",
        invitation="0190c0de-0000-7000-8000-000000000000",
    )
    response = call(client, case, url, ids.world.headers("owner_a"))
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}


@pytest.mark.parametrize(
    "case", [c for c in ROUTES if c.has_id], ids=lambda c: f"{c.method} {c.path}"
)
def test_a_malformed_id_is_422(client: TestClient, ids: Fixtures, case: Case) -> None:
    url = case.path.format(member="not-a-uuid", invitation="not-a-uuid")
    response = call(client, case, url, ids.world.headers("owner_a"))
    assert response.status_code == 422


VALIDATED = [c for c in ROUTES if c.invalid_body is not None or c.invalid_query is not None]


@pytest.mark.parametrize("case", VALIDATED, ids=lambda c: f"{c.method} {c.path}")
def test_validation_error(client: TestClient, ids: Fixtures, case: Case) -> None:
    url = case.url(ids) + (case.invalid_query or "")
    response = call(client, case, url, ids.world.headers("owner_a"), case.invalid_body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


LEAKY = [c for c in ROUTES if c.check_leak]


@pytest.mark.parametrize("case", LEAKY, ids=lambda c: f"{c.method} {c.path}")
@pytest.mark.parametrize("who", ["owner_b", "analyst_b"])
def test_nothing_from_another_workspace_appears(
    client: TestClient, ids: Fixtures, case: Case, who: str
) -> None:
    response = call(client, case, case.url(ids), ids.world.headers(who))
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
