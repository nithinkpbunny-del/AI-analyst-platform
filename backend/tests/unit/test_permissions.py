"""The permission matrix is BUILD_SPEC 6.2, cell by cell, and ``require()`` is its only enforcer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, cast

import pytest

from app.api.errors import ApiError
from app.auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    allowed,
    permissions_for,
    require,
)
from app.auth.service import AuthContext

P = Permission
OWN, A, V = "owner", "analyst", "viewer"

# BUILD_SPEC 6.2, row by row: which roles have each permission.
SPEC: dict[Permission, set[str]] = {
    P.ORG_MANAGE: {OWN},  # Manage org settings, plan, BYOK key
    P.MEMBERS_MANAGE: {OWN},  # Invite/remove users, change roles
    P.CONNECTIONS_MANAGE: {OWN},  # Add/edit/remove connections, grants
    P.CONNECTIONS_VIEW: {
        OWN,
        A,
    },  # See usable connections (DECISIONS D49; grants filter which ones)
    P.DICTIONARY_EDIT: {OWN},  # Confirm/edit business dictionary
    P.DICTIONARY_SUGGEST: {OWN, A},  # Analyst "can suggest"
    P.ASK: {OWN, A},  # Ask questions, what-if, long tasks
    P.SAVED_QUESTIONS: {OWN, A},
    P.SHARE: {OWN, A},
    P.EXPORT: {OWN, A, V},  # Viewer: export of shared items only (resource rule)
    P.GOALS_EDIT: {OWN, A},
    P.GOALS_VIEW: {OWN, A, V},
    P.DASHBOARD_EDIT: {OWN, A},
    P.DASHBOARD_VIEW: {OWN, A, V},  # Viewer: view shared
    P.SHARED_VIEW: {OWN, A, V},  # View shared answers/reports, own briefings
    P.AUDIT_VIEW_ALL: {OWN},
    P.AUDIT_VIEW_OWN: {OWN, A},  # Analyst: own entries; Viewer: none
}


@dataclass
class FakeUser:
    id: uuid.UUID


def ctx(role: str, *, two_factor: bool = True) -> AuthContext:
    fake: Any = AuthContext(
        user=cast(Any, FakeUser(uuid.uuid4())),
        organization=cast(Any, None),
        role=role,
        session=cast(Any, None),
        memberships=[],
        two_factor_enabled=two_factor,
    )
    return cast(AuthContext, fake)


@dataclass
class Shared:
    shared_with: set[uuid.UUID]

    def is_shared_with(self, user_id: uuid.UUID) -> bool:
        return user_id in self.shared_with


def test_spec_covers_every_permission() -> None:
    assert set(SPEC) == set(Permission)


@pytest.mark.parametrize("permission", list(Permission), ids=str)
@pytest.mark.parametrize("role", [OWN, A, V])
def test_matrix_matches_the_spec(role: str, permission: Permission) -> None:
    assert (permission in ROLE_PERMISSIONS[role]) == (role in SPEC[permission])


@pytest.mark.parametrize("permission", list(Permission), ids=str)
def test_require_refuses_what_a_role_lacks(permission: Permission) -> None:
    for role in (OWN, A, V):
        auth = ctx(role)
        resource = Shared({auth.user.id})
        if role in SPEC[permission]:
            require(auth, permission, resource)
        else:
            with pytest.raises(ApiError) as caught:
                require(auth, permission, resource)
            assert (caught.value.status_code, caught.value.code) == (403, "forbidden")


@pytest.mark.parametrize("permission", list(Permission), ids=str)
def test_owner_without_two_step_verification_may_do_nothing(permission: Permission) -> None:
    with pytest.raises(ApiError) as caught:
        require(ctx(OWN, two_factor=False), permission)
    assert (caught.value.status_code, caught.value.code) == (403, "two_factor_required")


def test_others_without_two_step_verification_keep_their_permissions() -> None:
    require(ctx(A, two_factor=False), P.ASK)
    require(ctx(V, two_factor=False), P.SHARED_VIEW)


def test_viewer_exports_only_items_shared_with_them() -> None:
    viewer = ctx(V)
    assert allowed(viewer, P.EXPORT, Shared({viewer.user.id}))
    assert not allowed(viewer, P.EXPORT, Shared({uuid.uuid4()}))
    assert not allowed(viewer, P.EXPORT, None)
    assert not allowed(viewer, P.EXPORT, object())
    # Owners and Analysts are not limited to shared items.
    assert allowed(ctx(A), P.EXPORT, None)
    assert allowed(ctx(OWN), P.EXPORT, object())


def test_unknown_role_has_nothing() -> None:
    assert not allowed(ctx("guest"), P.SHARED_VIEW)


def test_permissions_for_the_web_app() -> None:
    assert permissions_for(V, two_factor_enabled=False) == sorted(
        p.value for p in Permission if V in SPEC[p]
    )
    assert permissions_for(OWN, two_factor_enabled=True) == sorted(p.value for p in Permission)
    assert permissions_for(OWN, two_factor_enabled=False) == []
