"""Request and response bodies for the team, invitation, workspace-settings and activity-log routes (M4)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.auth.passwords import MAX_LENGTH
from app.schemas.auth import _clean_text, _Strict

RoleName = Literal["owner", "analyst", "viewer"]
InviteRoleName = Literal["analyst", "viewer"]
_Token = Field(min_length=10, max_length=128)


# ------------------------------------------------------------------------------------------------- members
class MemberOut(BaseModel):
    id: uuid.UUID  # the membership id
    user_id: uuid.UUID
    name: str
    email: str
    role: RoleName
    two_factor_enabled: bool
    joined_at: datetime
    is_you: bool


class MemberList(BaseModel):
    members: list[MemberOut]


class RoleChangeRequest(_Strict):
    role: RoleName


# --------------------------------------------------------------------------------------------- invitations
class InviteRequest(_Strict):
    email: EmailStr
    role: InviteRoleName


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: InviteRoleName
    invited_by_name: str | None
    created_at: datetime
    expires_at: datetime
    expired: bool


class InvitationList(BaseModel):
    invitations: list[InvitationOut]


class InvitationTokenRequest(_Strict):
    token: str = _Token


class InvitationPreviewOut(BaseModel):
    organization_name: str
    invited_by_name: str | None
    email: str
    role: InviteRoleName
    expires_at: datetime
    # Decides the form: name + password + terms (new person) or the password of the existing account.
    account_exists: bool
    has_password: bool


class AcceptNewAccountRequest(_Strict):
    token: str = _Token
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=MAX_LENGTH)
    accept_terms: bool

    _clean = field_validator("name")(_clean_text)

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Accept the privacy policy and terms to create an account.")
        return value


class AcceptWithPasswordRequest(_Strict):
    token: str = _Token
    password: str = Field(min_length=1, max_length=MAX_LENGTH)


# -------------------------------------------------------------------------------------- workspace settings
class OrganizationSettingsOut(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    currency: str
    plan: str


class OrganizationSettingsRequest(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is not None and value not in _ZONES:
            raise ValueError("Choose a time zone from the list.")
        return value


_ZONES = frozenset(available_timezones())


# ---------------------------------------------------------------------------------------------- activity
class AuditEntryOut(BaseModel):
    id: uuid.UUID
    at: datetime
    action: str
    user_id: uuid.UUID | None
    user_name: str | None
    user_email: str | None
    target_type: str | None
    target_id: str | None
    ip: str | None
    browser: str | None
    details: dict[str, Any]


class AuditPage(BaseModel):
    entries: list[AuditEntryOut]
    next_cursor: str | None
    # Owners see everyone's entries; Analysts only their own.
    scope: Literal["workspace", "own"]


class AuditQuery(BaseModel):
    """Query-string filters (validated like a body)."""

    user_id: uuid.UUID | None = None
    action: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_.]+$")
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")
    cursor: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)
