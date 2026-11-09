"""Email templates and senders."""

from __future__ import annotations

import smtplib
from typing import Any, ClassVar

import pytest

from app.config import Settings
from app.email import templates
from app.email.sender import MemoryEmailSender, OutgoingEmail, SmtpEmailSender


def test_verification_email_has_link_in_both_parts() -> None:
    email = templates.render(
        "verify_email",
        to="a@x.in",
        name="Asha",
        org_name="UrbanCart",
        hours=24,
        link="http://app/verify-email#token=abc",
    )
    assert email.subject == "Confirm your email address"
    assert "http://app/verify-email#token=abc" in email.text
    assert 'href="http://app/verify-email#token=abc"' in email.html
    assert "24 hours" in email.text


def test_html_is_escaped() -> None:
    email = templates.render(
        "verify_email", to="a@x.in", name="<script>x</script>", org_name="A&B", hours=24, link="l"
    )
    assert "<script>x" not in email.html
    assert "&lt;script&gt;" in email.html
    assert "A&amp;B" in email.html


@pytest.mark.parametrize(
    ("name", "context"),
    [
        ("reset_password", {"name": "A", "minutes": 60, "link": "l"}),
        ("password_changed", {"name": "A", "link": "l"}),
        ("account_exists", {"link": "l", "reset_link": "r"}),
    ],
)
def test_every_template_renders(name: str, context: dict[str, Any]) -> None:
    email = templates.render(name, to="a@x.in", **context)
    assert email.text.strip() and "<html" in email.html


def test_memory_sender() -> None:
    sender = MemoryEmailSender()
    sender.send(OutgoingEmail(to="A@x.in", subject="s", text="t", html="h"))
    assert len(sender.to("a@X.in")) == 1


class _FakeSmtp:
    sent: ClassVar[list[Any]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def send_message(self, message: Any) -> None:
        _FakeSmtp.sent.append(message)


def test_smtp_sender_builds_a_multipart_message(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    SmtpEmailSender(settings).send(
        OutgoingEmail(to="a@x.in", subject="Hi", text="t", html="<p>h</p>")
    )
    message = _FakeSmtp.sent[-1]
    assert message["To"] == "a@x.in" and message["Subject"] == "Hi"
    assert message.is_multipart()


def test_smtp_failure_is_logged_not_raised(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", broken)
    SmtpEmailSender(settings).send(OutgoingEmail(to="a@x.in", subject="Hi", text="t", html="h"))
    assert "could not send email: Hi" in caplog.text
    assert "a@x.in" not in caplog.text
