"""How a one-time sign-in code reaches the person (BUILD_SPEC R15).

Email is the only channel today. SMS codes are a later phase (BUILD_SPEC 2, out of scope): they will be another
``CodeDelivery`` (for example backed by an SMS gateway), chosen per person, without changing how codes are
created, stored (keyed hash), limited or checked in ``TwoFactorManager``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.email import templates
from app.email.sender import OutgoingEmail
from app.models import User
from app.models.two_factor import CodePurpose

Channel = Literal["email", "sms"]


class CodeDelivery(Protocol):
    channel: Channel

    def deliver(self, user: User, code: str, *, purpose: CodePurpose, minutes: int) -> None: ...


@dataclass
class EmailCodeDelivery:
    """Queues the code email in the request's outbox; it is sent after the response (see ``app/api/auth``)."""

    outbox: list[OutgoingEmail]
    channel: Channel = "email"

    def deliver(self, user: User, code: str, *, purpose: CodePurpose, minutes: int) -> None:
        self.outbox.append(
            templates.render(
                "two_factor_code",
                to=user.email,
                subject="Your sign-in code" if purpose == "login" else "Your verification code",
                name=user.name,
                code=code,
                minutes=minutes,
                purpose=purpose,
            )
        )
