"""Render transactional emails (HTML + plain text) from Jinja templates in ``templates/``."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.email.sender import Attachment, OutgoingEmail

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

SUBJECTS = {
    "verify_email": "Confirm your email address",
    "reset_password": "Reset your password",
    "password_changed": "Your password was changed",
    "account_exists": "You already have an account",
    "two_factor_code": "Your sign-in code",
    "new_sign_in": "New sign-in to your account",
    "two_factor_changed": "Your two-step verification changed",
    "invitation": "You are invited to join a workspace on Analyst",
    "role_changed": "Your role changed",
    "access_removed": "Your access to a workspace was removed",
    "task_done": "Your analysis is ready",
    "task_failed": "Your analysis could not finish",
    "shared": "Someone shared an answer with you",
    "dictionary_draft": "Your data changed: check the business dictionary",
    "deletion_requested": "Your workspace will be deleted",
    "deletion_cancelled": "Your workspace will not be deleted",
    "briefing": "Your morning briefing",
    "usage_warning": "Your monthly question limit",
}


def render(template: str, *, to: str, subject: str | None = None, **context: Any) -> OutgoingEmail:
    subject = subject or SUBJECTS[template]
    context = {"subject": subject, **context}
    html = _env.get_template(f"{template}.html").render(**context)
    text = _env.get_template(f"{template}.txt").render(**context)
    return OutgoingEmail(to=to, subject=subject, text=text, html=html)


def with_attachments(message: OutgoingEmail, *attachments: Attachment) -> OutgoingEmail:
    return replace(message, attachments=(*message.attachments, *attachments))
