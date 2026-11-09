"""Render transactional emails (HTML + plain text) from Jinja templates in ``templates/``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.email.sender import OutgoingEmail

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
}


def render(template: str, *, to: str, **context: Any) -> OutgoingEmail:
    subject = SUBJECTS[template]
    context = {"subject": subject, **context}
    html = _env.get_template(f"{template}.html").render(**context)
    text = _env.get_template(f"{template}.txt").render(**context)
    return OutgoingEmail(to=to, subject=subject, text=text, html=html)
