"""Email sending behind a small interface.

* ``SmtpEmailSender``: SMTP (Mailpit locally, SES/Resend SMTP in production).
* ``MemoryEmailSender``: keeps messages in a list; used by tests so nothing leaves the process.

Auth emails are sent from FastAPI background tasks, after the response, so response time does not reveal
whether an account exists.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


class SmtpEmailSender:
    def __init__(self, settings: Settings, *, timeout_seconds: float = 10.0) -> None:
        self._settings = settings
        self._timeout = timeout_seconds

    def _build(self, message: OutgoingEmail) -> EmailMessage:
        email = EmailMessage()
        email["From"] = self._settings.email_from
        email["To"] = message.to
        email["Subject"] = message.subject
        email["Message-ID"] = make_msgid(domain="analyst.local")
        email.set_content(message.text)
        email.add_alternative(message.html, subtype="html")
        return email

    def send(self, message: OutgoingEmail) -> None:
        settings = self._settings
        try:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=self._timeout
            ) as smtp:
                if settings.smtp_user:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
                smtp.send_message(self._build(message))
        except (OSError, smtplib.SMTPException):
            # The recipient address is personal data: log the subject only (the redactor would mask it anyway).
            logger.exception("could not send email: %s", message.subject)


@dataclass
class MemoryEmailSender:
    sent: list[OutgoingEmail] = field(default_factory=list)

    def send(self, message: OutgoingEmail) -> None:
        self.sent.append(message)

    def to(self, address: str) -> list[OutgoingEmail]:
        return [m for m in self.sent if m.to.lower() == address.lower()]
