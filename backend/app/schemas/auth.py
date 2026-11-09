"""Request and response bodies for ``/auth/*``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

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
    has_password: bool
    google_linked: bool


class TwoFactorOut(BaseModel):
    enabled: bool
    method: Literal["totp", "email"] | None
    # Owners must have it on (BUILD_SPEC 6.1); the web app sends them to set-up until it is.
    required: bool
    recovery_codes_remaining: int


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
    two_factor: TwoFactorOut


class SessionOut(Me):
    status: Literal["signed_in"] = "signed_in"
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    csrf_token: str


class CsrfOut(BaseModel):
    csrf_token: str


# ---------------------------------------------------------------------------------------- sign-in challenges
TwoFactorMethodIn = Literal["totp", "email"]
_Code = Annotated[str, Field(min_length=1, max_length=32)]
_ChallengeToken = Annotated[str, Field(min_length=10, max_length=128)]


class ChallengeOut(BaseModel):
    """The first factor passed. ``two_factor_required``: enter a code. ``two_factor_setup_required``: an Owner
    without 2FA sets it up now, before the sign-in can finish."""

    status: Literal["two_factor_required", "two_factor_setup_required"]
    challenge_token: str
    method: Literal["totp", "email"] | None
    email_hint: str | None
    code_sent: bool
    expires_in: int


LoginOut = Annotated[SessionOut | ChallengeOut, Field(discriminator="status")]


class ChallengeRequest(_Strict):
    challenge_token: _ChallengeToken


class ChallengeCodeRequest(_Strict):
    challenge_token: _ChallengeToken
    code: _Code


class ChallengeSetupRequest(_Strict):
    challenge_token: _ChallengeToken
    method: TwoFactorMethodIn


class SetupOut(BaseModel):
    method: Literal["totp", "email"]
    # Authenticator app: the secret (to type in), the otpauth:// URI and the QR code modules (rows of 0/1).
    secret: str | None = None
    otpauth_uri: str | None = None
    qr: list[str] | None = None
    # Email: where the code went (masked).
    email_hint: str | None = None


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class SetupDoneOut(SessionOut):
    recovery_codes: list[str]


# ------------------------------------------------------------------------------------- signed-in 2FA settings
class SetupRequest(_Strict):
    method: TwoFactorMethodIn
    # Needed when 2FA is already on: a code from the current method (or a recovery code).
    code: _Code | None = None


class CodeRequest(_Strict):
    code: _Code


class OptionalCodeRequest(_Strict):
    code: _Code | None = None


# ----------------------------------------------------------------------------------------------- Google
class ProvidersOut(BaseModel):
    google: bool
    # True in local development without Google credentials: "Google" is a stand-in page on the API.
    google_stand_in: bool


class GoogleTokenRequest(_Strict):
    token: _ChallengeToken


class GoogleSignupInfo(BaseModel):
    email: str
    name: str


class GoogleSignupRequest(_Strict):
    token: _ChallengeToken
    name: str = Field(min_length=1, max_length=120)
    organization_name: str = Field(min_length=1, max_length=120)
    accept_terms: bool

    _clean = field_validator("name", "organization_name")(_clean_text)

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Accept the privacy policy and terms to create an account.")
        return value


# ---------------------------------------------------------------------------------------------- sessions
class SessionItem(BaseModel):
    id: uuid.UUID
    current: bool
    browser: str
    ip: str | None
    created_at: datetime
    last_seen_at: datetime


class SessionList(BaseModel):
    sessions: list[SessionItem]


class Revoked(BaseModel):
    status: Literal["ok"] = "ok"
    sessions_revoked: int
