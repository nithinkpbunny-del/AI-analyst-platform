"""Sign in with Google (BUILD_SPEC 6.1, R15). See ``app/auth/google_oidc.py`` and ``docs/SETUP_GOOGLE.md``.

Flow (all on the API host, then back to the web app):

1. ``GET /auth/google/start``: the browser is sent here (a normal page load). We create ``state``, ``nonce`` and
   a PKCE verifier, keep them in Redis for 10 minutes, bind ``state`` to this browser with a short-lived
   cookie, and redirect to Google.
2. ``GET /auth/google/callback``: Google sends the browser back. ``state`` must match the cookie (stops
   login CSRF) and is single-use. The code is exchanged and the ID token checked. The browser then goes to the
   web app with a one-time token in the URL fragment: ``/signin/google#token=…`` (existing account) or
   ``/signup/google#token=…`` (new person: company name and consent are still needed).
3. ``POST /auth/google/finish`` (or ``/auth/google/signup``) from the web app trades that token for the same
   answer as a password login: a session, or a 2FA step.

Errors go back to ``/signin?google=<code>``; the web app explains them.
"""

from __future__ import annotations

import html
import json
import logging
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.auth import login_response, send_queued
from app.api.errors import ApiError
from app.auth.deps import AuthServiceDep
from app.auth.google_oidc import (
    GoogleIdentity,
    GoogleProvider,
    GoogleSignInError,
    StandInGoogleProvider,
)
from app.auth.tokens import tokens_match
from app.config import Settings
from app.schemas.auth import (
    ChallengeOut,
    GoogleSignupInfo,
    GoogleSignupRequest,
    GoogleTokenRequest,
    LoginOut,
    ProvidersOut,
    SessionOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["google sign-in"])

STATE_COOKIE = "analyst_google_state"
_STATE_TTL_SECONDS = 600
_STATE_PREFIX = "oauth-google:"


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _provider(request: Request) -> GoogleProvider | None:
    provider: GoogleProvider | None = request.app.state.google_provider
    return provider


def _redirect_uri(request: Request) -> str:
    return f"{_settings(request).api_base_url.rstrip('/')}/auth/google/callback"


def _to_app(request: Request, path: str) -> RedirectResponse:
    response = RedirectResponse(f"{_settings(request).app_base_url.rstrip('/')}{path}", 302)
    response.delete_cookie(STATE_COOKIE, path="/auth/google")
    response.headers["Cache-Control"] = "no-store"
    return response


def _failed(request: Request, code: str) -> RedirectResponse:
    return _to_app(request, f"/signin?{urlencode({'google': code})}")


@router.get(
    "/providers", response_model=ProvidersOut, summary="Which sign-in options are available"
)
def providers(request: Request) -> ProvidersOut:
    provider = _provider(request)
    return ProvidersOut(
        google=provider is not None, google_stand_in=bool(provider and provider.kind == "stand_in")
    )


@router.get("/google/start", summary="Start signing in with Google (browser navigation)")
def start(request: Request) -> RedirectResponse:
    provider = _provider(request)
    if provider is None:
        return _failed(request, "google_unavailable")
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    request.app.state.redis.set(
        f"{_STATE_PREFIX}{state}",
        json.dumps({"nonce": nonce, "verifier": verifier}),
        ex=_STATE_TTL_SECONDS,
    )
    url = provider.authorization_url(
        redirect_uri=_redirect_uri(request), state=state, nonce=nonce, code_verifier=verifier
    )
    response = RedirectResponse(url, 302)
    # Lax, not Strict: the browser must send it when Google redirects back (a cross-site navigation).
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=_STATE_TTL_SECONDS,
        path="/auth/google",
        httponly=True,
        secure=_settings(request).cookies_secure,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/google/callback", summary="Google sends the browser back here")
def callback(
    request: Request,
    service: AuthServiceDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    provider = _provider(request)
    if provider is None:
        return _failed(request, "google_unavailable")
    if error:
        return _failed(request, "google_cancelled" if error == "access_denied" else "google_failed")
    cookie = request.cookies.get(STATE_COOKIE, "")
    if not code or not state or not cookie or not tokens_match(cookie, state):
        logger.warning("google callback with missing or mismatched state")
        return _failed(request, "google_failed")
    raw = request.app.state.redis.getdel(f"{_STATE_PREFIX}{state}")
    if raw is None:
        return _failed(request, "google_expired")
    saved = json.loads(raw)
    try:
        identity = provider.exchange(
            code=code,
            redirect_uri=_redirect_uri(request),
            code_verifier=saved["verifier"],
            nonce=saved["nonce"],
            now=request.app.state.clock(),
        )
        step, token = service.google_identity(identity)
    except GoogleSignInError as failure:
        logger.warning("google sign-in failed (%s): %s", failure.code, failure)
        return _failed(request, failure.code)
    path = "/signin/google" if step == "handoff" else "/signup/google"
    return _to_app(request, f"{path}#token={token}")


@router.post(
    "/google/finish",
    response_model=LoginOut,
    summary="Trade the one-time token for a session or the 2FA step",
)
def finish(
    body: GoogleTokenRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SessionOut | ChallengeOut:
    outcome = service.google_finish(body.token)
    send_queued(request, background, service)
    return login_response(request, response, service, outcome)


@router.post(
    "/google/signup-info",
    response_model=GoogleSignupInfo,
    summary="Name and email Google gave us, to show on the sign-up form",
)
def signup_info(body: GoogleTokenRequest, service: AuthServiceDep) -> GoogleSignupInfo:
    email, name = service.google_signup_details(body.token)
    return GoogleSignupInfo(email=email, name=name)


@router.post(
    "/google/signup",
    response_model=LoginOut,
    summary="Create the organisation for a new Google user (they become its Owner)",
)
def signup(
    body: GoogleSignupRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    service: AuthServiceDep,
) -> SessionOut | ChallengeOut:
    outcome = service.google_signup(
        body.token, name=body.name, organization_name=body.organization_name
    )
    send_queued(request, background, service)
    return login_response(request, response, service, outcome)


# ------------------------------------------------------------------------- local development stand-in
def _stand_in(request: Request) -> StandInGoogleProvider:
    provider = _provider(request)
    if not isinstance(provider, StandInGoogleProvider):
        raise ApiError(404, "not_found", "Not found.")
    return provider


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google sign-in stand-in</title>
<style>
body{{margin:0;font:16px/1.55 system-ui,sans-serif;background:#F5F6FA;color:#1E1C3A}}
main{{max-width:440px;margin:48px 16px;padding:24px;background:#fff;border:1px solid #E2E4EE;border-radius:16px}}
h1{{font-size:24px;line-height:1.15;margin:0 0 8px}} p{{margin:0 0 16px;color:#5A5874}}
label{{display:block;font-weight:500;margin:16px 0 6px}}
input[type=email],input[type=text]{{box-sizing:border-box;width:100%;min-height:44px;padding:0 12px;
border:1px solid #85839F;border-radius:10px;font:inherit}}
.check{{display:flex;gap:8px;align-items:center;margin-top:16px}}
button{{margin-top:24px;min-height:44px;padding:0 20px;border:0;border-radius:10px;background:#3A36A8;color:#fff;
font:inherit;font-weight:600}}
</style></head><body><main>
<h1>Google sign-in stand-in</h1>
<p>Local development only. No Google account is used: type the email address you want to sign in as.
Add real Google credentials with docs/SETUP_GOOGLE.md.</p>
<form method="post" action="/auth/google/stand-in">
<input type="hidden" name="state" value="{state}"><input type="hidden" name="nonce" value="{nonce}">
<label for="email">Email</label><input id="email" name="email" type="email" required autocomplete="email">
<label for="name">Name</label><input id="name" name="name" type="text" autocomplete="name">
<div class="check"><input id="verified" name="email_verified" type="checkbox" value="1" checked>
<label for="verified" style="margin:0;font-weight:400">Google says this email is verified</label></div>
<button type="submit">Continue</button>
</form></main></body></html>"""


@router.get("/google/stand-in", include_in_schema=False)
def stand_in_page(request: Request, state: str = "", nonce: str = "") -> HTMLResponse:
    _stand_in(request)
    page = _PAGE.format(state=html.escape(state), nonce=html.escape(nonce))
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@router.post("/google/stand-in", include_in_schema=False)
async def stand_in_submit(request: Request) -> RedirectResponse:
    provider = _stand_in(request)
    form: dict[str, Any] = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    email = str(form.get("email", "")).strip()
    if "@" not in email:
        raise ApiError(422, "validation_error", "Enter a valid email address.")
    identity = GoogleIdentity(
        sub=f"stand-in-{email.lower()}",
        email=email,
        email_verified=form.get("email_verified") == "1",
        name=str(form.get("name") or email.partition("@")[0]).strip()[:120],
    )
    code = provider.issue_code(identity, nonce=str(form.get("nonce", "")))
    query = urlencode({"code": code, "state": str(form.get("state", ""))})
    return RedirectResponse(f"{_redirect_uri(request)}?{query}", 303)
