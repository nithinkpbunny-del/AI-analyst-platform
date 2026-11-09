"""The single permission check (BUILD_SPEC 6.2): ``require(auth, permission, resource=None)``.

Every route that touches workspace data calls it (through ``app.api.deps.requires``); roles are never compared
inline anywhere else. The matrix below is BUILD_SPEC 6.2 written as data, so the web app gets the same answer
from ``/auth/me`` (``permissions``) instead of keeping its own copy.

Two rules on top of the matrix:

* **Owners without two-step verification may do nothing** until they turn it on (BUILD_SPEC 6.1, DECISIONS D25).
  That covers people who were made Owner after signing in.
* **Viewers may export only items shared with them.** Callers pass the item as ``resource``; it must say whether
  it was shared with the caller (``SharedResource``). Sharing arrives in M10.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.api.errors import ApiError

if TYPE_CHECKING:
    from app.auth.service import AuthContext


class Permission(StrEnum):
    ORG_MANAGE = "org.manage"  # org settings, plan, BYOK key
    MEMBERS_MANAGE = "members.manage"  # invite/remove people, change roles
    CONNECTIONS_MANAGE = "connections.manage"  # add/edit/remove connections and grants
    DICTIONARY_EDIT = "dictionary.edit"  # confirm/edit the business dictionary
    DICTIONARY_SUGGEST = "dictionary.suggest"
    ASK = "ask"  # questions, what-if, long tasks
    SAVED_QUESTIONS = "saved_questions"
    SHARE = "share"
    EXPORT = "export"
    GOALS_EDIT = "goals.edit"
    GOALS_VIEW = "goals.view"
    DASHBOARD_EDIT = "dashboard.edit"  # pin/edit own tiles
    DASHBOARD_VIEW = "dashboard.view"  # view shared tiles
    SHARED_VIEW = "shared.view"  # shared answers/reports, own briefings
    AUDIT_VIEW_ALL = "audit.view_all"
    AUDIT_VIEW_OWN = "audit.view_own"


P = Permission
_EVERYONE = {P.GOALS_VIEW, P.DASHBOARD_VIEW, P.SHARED_VIEW, P.EXPORT}
_ANALYST = _EVERYONE | {
    P.DICTIONARY_SUGGEST,
    P.ASK,
    P.SAVED_QUESTIONS,
    P.SHARE,
    P.GOALS_EDIT,
    P.DASHBOARD_EDIT,
    P.AUDIT_VIEW_OWN,
}
_OWNER = _ANALYST | {
    P.ORG_MANAGE,
    P.MEMBERS_MANAGE,
    P.CONNECTIONS_MANAGE,
    P.DICTIONARY_EDIT,
    P.AUDIT_VIEW_ALL,
}

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "owner": frozenset(_OWNER),
    "analyst": frozenset(_ANALYST),
    "viewer": frozenset(_EVERYONE),
}


@runtime_checkable
class SharedResource(Protocol):
    def is_shared_with(self, user_id: uuid.UUID) -> bool: ...


def _resource_allows(auth: AuthContext, permission: Permission, resource: object | None) -> bool:
    if permission is P.EXPORT and auth.role == "viewer":
        return isinstance(resource, SharedResource) and resource.is_shared_with(auth.user.id)
    return True


def _blocked_by_two_factor(auth: AuthContext) -> bool:
    return auth.role == "owner" and not auth.two_factor_enabled


def allowed(auth: AuthContext, permission: Permission, resource: object | None = None) -> bool:
    if _blocked_by_two_factor(auth):
        return False
    if permission not in ROLE_PERMISSIONS.get(auth.role, frozenset()):
        return False
    return _resource_allows(auth, permission, resource)


def require(auth: AuthContext, permission: Permission, resource: object | None = None) -> None:
    """Raise 403 unless the caller may do ``permission`` (on ``resource``, when given)."""
    if _blocked_by_two_factor(auth):
        raise ApiError(
            403,
            "two_factor_required",
            "Turn on two-step verification to manage this workspace.",
        )
    if not allowed(auth, permission, resource):
        raise ApiError(
            403,
            "forbidden",
            "Your role does not allow this. Ask an Owner of this workspace.",
        )


def permissions_for(role: str, *, two_factor_enabled: bool) -> list[str]:
    """What someone with this role may do now, for the web app (resource-specific rules aside)."""
    if role == "owner" and not two_factor_enabled:
        return []
    return sorted(ROLE_PERMISSIONS.get(role, frozenset()))
