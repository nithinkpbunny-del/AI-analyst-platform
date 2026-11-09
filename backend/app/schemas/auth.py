"""Request and response bodies for ``/auth/*``."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.auth.passwords import MAX_LENGTH


def _clean_text(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("This field is required.")
    return cleaned


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=320)


class SignupRequest(_Strict):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_LENGTH)
    organization_name: str = Field(min_length=1, max_length=120)
    accept_terms: bool

    _clean = field_validator("name", "organization_name")(_clean_text)

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Accept the privacy policy and terms to create an account.")
        return value


class EmailRequest(_Strict):
    email: EmailStr


class TokenRequest(_Strict):
    token: str = Field(min_length=10, max_length=128)


class LoginRequest(_Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_LENGTH)
    organization_id: uuid.UUID | None = None


class ResetPasswordRequest(_Strict):
    token: str = Field(min_length=10, max_length=128)
    password: str = Field(min_length=1, max_length=MAX_LENGTH)


class Accepted(BaseModel):
    """For requests whose outcome must not reveal whether an account exists."""

    status: Literal["accepted"] = "accepted"
    message: str


class Done(BaseModel):
    status: Literal["ok"] = "ok"
    message: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    preferred_language: str


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    currency: str
    plan: str


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: Literal["owner", "analyst", "viewer"]


class Me(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: Literal["owner", "analyst", "viewer"]
    memberships: list[MembershipOut]
    session_expires_at: str
    idle_timeout_minutes: int


class SessionOut(Me):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    csrf_token: str


class CsrfOut(BaseModel):
    csrf_token: str
