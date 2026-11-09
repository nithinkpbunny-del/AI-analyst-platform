"""Dependencies for workspace (tenant) routes: permission check first, then a tenant-scoped database session.

    @router.get("/team/members")
    def members(tenant: Annotated[Tenant, requires(Permission.MEMBERS_MANAGE)]) -> ...

``requires`` runs ``require()`` (403 on failure) and scopes the request's database session to the caller's
workspace, so every later query runs under Row-Level Security (``app.db.tenant``). Services still filter by
``org_id`` themselves (defence in depth, BUILD_SPEC 6.2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import AuthServiceDep, CurrentAuth
from app.auth.permissions import Permission, require
from app.auth.service import AuthContext, AuthService
from app.db.tenant import scope_to_org


@dataclass(frozen=True)
class Tenant:
    """The caller, their workspace and a database session scoped to it."""

    auth: AuthContext
    db: DbSession
    service: AuthService  # request metadata, clock, email outbox, session revocation

    @property
    def org_id(self) -> uuid.UUID:
        return self.auth.organization.id


def requires(permission: Permission) -> Any:
    """``Depends(...)`` that checks ``permission`` and returns the scoped ``Tenant``."""

    def dependency(auth: CurrentAuth, service: AuthServiceDep) -> Tenant:
        require(auth, permission)
        scope_to_org(service.db, auth.organization.id)
        return Tenant(auth=auth, db=service.db, service=service)

    dependency.__name__ = f"requires_{permission.name.lower()}"
    return Depends(dependency)
