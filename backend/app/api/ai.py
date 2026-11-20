"""``/org/ai`` and ``/org/usage``: the workspace's AI plan, its own key (BYOK), usage and a connection check.

Everything here needs ``org.manage`` (Owners; BUILD_SPEC 6.2 "Manage org settings, plan, BYOK key"), and usage is
Owner-only too (DECISIONS D42). Keys are write-only: responses show the provider and the last 4 characters.
Changes are written to the activity log (``org.plan_changed``, ``llm.key_set``, ``llm.key_removed``).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.billing import limits
from app.billing import provider as billing_provider
from app.config import Settings
from app.llm import keys
from app.llm.base import LLMError, LLMFactory, UserMessage, close_client
from app.llm.usage import (
    MeteredLLM,
    UsageLimitReached,
    UsageSummary,
    current_month,
    month_of,
    summary,
)
from app.models import Organization, User
from app.schemas.ai import (
    AiCheckOut,
    AiSettingsOut,
    KeyRequest,
    ManagedOut,
    MonthUsageOut,
    OwnKeyOut,
    PersonUsageOut,
    PlanRequest,
    UsageOut,
    UsageTotalsOut,
)
from app.security.secrets import SecretStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/org", tags=["ai"])

CHECKS_PER_HOUR = 10
# Each save asks the provider whether the key works: a limit stops anyone using us to test stolen keys.
KEY_SAVES_PER_HOUR = 10
CHECK_SYSTEM = "You are checking that an API connection works. Answer in as few words as possible."
CHECK_PROMPT = "Reply with the single word OK."

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Only Owners can manage the AI plan and see usage"},
    409: {"description": "Not possible in the current state (e.g. no key saved)"},
    422: {"description": "Validation error, or the provider did not accept the key"},
    429: {"description": "Too many checks, or the monthly question limit is reached"},
    502: {"description": "The AI provider returned an error"},
}

ManageOrg = Annotated[Tenant, requires(Permission.ORG_MANAGE)]

PROVIDER_NAMES = {"gemini": "Google Gemini", "claude": "Anthropic Claude"}
_LLM_MESSAGES = {
    "invalid_key": "{provider} did not accept the key. Check that the whole key was copied, or create a new one.",
    "quota": "The key has used up its {provider} quota. It resets with the provider's quota period.",
    "rate_limited": "{provider} is receiving too many requests right now. Try again in a minute.",
    "model_not_found": "The key cannot use any {provider} model this app supports. Check the key's account.",
    "unavailable": "{provider} is not responding right now. Try again in a few minutes.",
    "other": "{provider} returned an error. Try again in a few minutes.",
}
_UNAVAILABLE = {
    "ai_key_missing": (
        409,
        "This workspace is set to use its own AI key, but no key is saved. Add one in Settings.",
    ),
    "ai_key_unreadable": (409, "The saved AI key could not be opened. Paste the key again."),
    "ai_not_configured": (
        409,
        "Managed AI is not set up on this server yet. Add your own AI key instead.",
    ),
}


# ---------------------------------------------------------------------------------------------- helpers
def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _factory(request: Request) -> LLMFactory:
    factory: LLMFactory = request.app.state.llm_factory
    return factory


def _secrets(request: Request) -> SecretStore:
    store: SecretStore = request.app.state.secret_store
    return store


def _org(tenant: Tenant, *, lock: bool = False) -> Organization:
    query = select(Organization).where(Organization.id == tenant.org_id)
    org = tenant.db.scalar(query.with_for_update() if lock else query)
    if org is None:  # pragma: no cover - the caller's own workspace always exists
        raise ApiError(404, "not_found", "Not found.")
    return org


def llm_api_error(error: LLMError) -> ApiError:
    logger.warning("AI provider call failed: %s %s (%s)", error.provider, error.kind, error.detail)
    provider = PROVIDER_NAMES.get(error.provider, error.provider)
    message = _LLM_MESSAGES.get(error.kind, _LLM_MESSAGES["other"]).format(provider=provider)
    return ApiError(502, f"ai_{error.kind}", message)


def unavailable_api_error(error: keys.AiUnavailable) -> ApiError:
    status_code, message = _UNAVAILABLE[error.code]
    return ApiError(status_code, error.code, message)


def limit_api_error(error: UsageLimitReached) -> ApiError:
    resets = f"{error.resets_on.day} {error.resets_on:%B %Y}"
    return ApiError(
        429,
        "usage_limit_reached",
        f"This workspace has used all {error.limit} questions for this month. The limit resets on "
        f"{resets}. An Owner can add the workspace's own AI key in Settings to keep going.",
    )


def _settings_out(tenant: Tenant, org: Organization, settings: Settings) -> AiSettingsOut:
    row = keys.own_key(tenant.db, org.id)
    own = None
    if row is not None:
        added_by = tenant.db.get(User, row.created_by) if row.created_by else None
        own = OwnKeyOut(
            provider=row.provider,
            last4=row.last4,
            added_at=row.created_at,
            added_by_name=added_by.name if added_by else None,
        )
    return AiSettingsOut(
        plan=org.plan,
        managed=ManagedOut(
            provider=settings.llm_default_provider,
            available=bool(keys.platform_key(settings)),
            monthly_question_limit=org.monthly_question_limit,
        ),
        own_key=own,
    )


def change_plan(
    tenant: Tenant,
    org: Organization,
    plan: str,
    *,
    settings: Settings,
    reason: str | None = None,
    via: str = "ai_settings",
) -> None:
    """The one place a workspace's plan changes (M5). Written to the activity log and, since M18, offered to the
    billing provider as a ``plan_changed`` event (the stand-in sends nothing; nothing is charged)."""
    if org.plan == plan:
        return
    details: dict[str, Any] = {"from": org.plan, "to": plan}
    if reason:
        details["reason"] = reason
    audit.write(
        tenant.db,
        action="org.plan_changed",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="organization",
        target_id=org.id,
        details=details,
    )
    limits.record_event(
        tenant.db,
        billing_provider.get_provider(settings),
        org_id=org.id,
        kind="plan_changed",
        period=None,
        details={**details, "via": via},
        user_id=tenant.auth.user.id,
    )
    org.plan = plan


# ----------------------------------------------------------------------------------------------- routes
@router.get(
    "/ai",
    response_model=AiSettingsOut,
    responses=_ERRORS,
    summary="The AI plan and the workspace's own key (last 4 characters only)",
)
def get_ai(request: Request, tenant: ManageOrg) -> AiSettingsOut:
    return _settings_out(tenant, _org(tenant), _settings(request))


@router.put(
    "/ai/plan",
    response_model=AiSettingsOut,
    responses=_ERRORS,
    summary="Switch between Managed AI and the workspace's own key",
)
def set_plan(body: PlanRequest, request: Request, tenant: ManageOrg) -> AiSettingsOut:
    org = _org(tenant, lock=True)
    if body.plan == "byok" and keys.own_key(tenant.db, org.id) is None:
        raise ApiError(409, "ai_key_missing", "Add your own AI key first, then switch to it.")
    change_plan(tenant, org, body.plan, settings=_settings(request))
    tenant.db.commit()
    return _settings_out(tenant, org, _settings(request))


@router.put(
    "/ai/key",
    response_model=AiSettingsOut,
    responses=_ERRORS,
    summary="Save the workspace's own AI key (checked with the provider first)",
)
def set_key(body: KeyRequest, request: Request, tenant: ManageOrg) -> AiSettingsOut:
    limiter = FixedWindowLimiter(
        request.app.state.redis,
        prefix="ai-key",
        limit=KEY_SAVES_PER_HOUR,
        window=timedelta(hours=1),
    )
    if not limiter.hit(str(tenant.org_id), tenant.service.clock()):
        raise ApiError(
            429,
            "too_many_key_checks",
            f"Keys were checked {KEY_SAVES_PER_HOUR} times in the last hour. Try again later.",
        )
    settings = _settings(request)
    preferred = (
        settings.gemini_default_model
        if body.provider == "gemini"
        else settings.claude_default_model
    )
    try:
        keys.check_key(_factory(request), body.provider, body.api_key, preferred)
    except LLMError as error:
        if error.kind == "invalid_key":
            provider = PROVIDER_NAMES[body.provider]
            raise ApiError(
                422,
                "ai_invalid_key",
                f"{provider} did not accept this key.",
                fields={
                    "api_key": f"{provider} did not accept this key. Check that you copied all of it."
                },
            ) from error
        raise llm_api_error(error) from error
    org = _org(tenant, lock=True)
    row, replaced = keys.store_key(
        tenant.db,
        org_id=org.id,
        provider=body.provider,
        api_key=body.api_key,
        user_id=tenant.auth.user.id,
        secrets=_secrets(request),
    )
    details: dict[str, Any] = {"provider": row.provider, "last4": row.last4}
    if replaced is not None:
        details["replaced"] = {"provider": replaced.provider, "last4": replaced.last4}
    audit.write(
        tenant.db,
        action="llm.key_set",
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="llm_key",
        target_id=row.id,
        details=details,
    )
    tenant.db.commit()
    return _settings_out(tenant, org, settings)


@router.delete(
    "/ai/key",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Remove the workspace's own key (switches back to Managed AI)",
)
def remove_key(request: Request, tenant: ManageOrg) -> Response:
    org = _org(tenant, lock=True)
    row = keys.remove_key(tenant.db, org_id=org.id, secrets=_secrets(request))
    if row is not None:
        audit.write(
            tenant.db,
            action="llm.key_removed",
            at=tenant.service.clock(),
            meta=tenant.service.meta,
            org_id=tenant.org_id,
            user_id=tenant.auth.user.id,
            target_type="llm_key",
            target_id=row.id,
            details={"provider": row.provider, "last4": row.last4},
        )
        change_plan(tenant, org, "managed", settings=_settings(request), reason="key_removed")
    tenant.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/ai/check",
    response_model=AiCheckOut,
    responses=_ERRORS,
    summary="Send one tiny request with the workspace's current key and show the reply",
)
def check_ai(request: Request, tenant: ManageOrg) -> AiCheckOut:
    now = tenant.service.clock()
    limiter = FixedWindowLimiter(
        request.app.state.redis, prefix="ai-check", limit=CHECKS_PER_HOUR, window=timedelta(hours=1)
    )
    if not limiter.hit(str(tenant.org_id), now):
        raise ApiError(
            429,
            "too_many_checks",
            f"The AI connection was tested {CHECKS_PER_HOUR} times in the last hour. Try again later.",
        )
    settings = _settings(request)
    org = _org(tenant)
    try:
        resolved = keys.resolve(
            tenant.db,
            org,
            settings=settings,
            factory=_factory(request),
            secrets=_secrets(request),
            now=now,
            check_limit=False,  # a check is not a question (DECISIONS D42)
        )
    except keys.AiUnavailable as error:
        raise unavailable_api_error(error) from error
    metered = MeteredLLM(
        resolved.client,
        tenant.db,
        org_id=org.id,
        user_id=tenant.auth.user.id,
        kind="check",
        key_source=resolved.key_source,
        at=now,
    )
    try:
        response = metered.generate(
            CHECK_SYSTEM, [UserMessage(CHECK_PROMPT)], max_tokens=settings.llm_max_output_tokens
        )
    except LLMError as error:
        raise llm_api_error(error) from error
    finally:
        close_client(resolved.client)
    tenant.db.commit()
    return AiCheckOut(
        reply=response.text.strip()[:200],
        provider=response.provider,
        model=response.model,
        key_source=resolved.key_source,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def _usage_out(result: UsageSummary, *, is_current: bool) -> UsageOut:
    t = result.totals
    return UsageOut(
        month=result.month.label,
        resets_on=result.month.next_first_day,
        is_current_month=is_current,
        plan=result.plan,
        limit=result.limit,
        totals=UsageTotalsOut(
            questions=t.questions,
            limited_questions=t.limited_questions,
            tasks=t.tasks,
            briefings=t.briefings,
            checks=t.checks,
            input_tokens=t.input_tokens,
            output_tokens=t.output_tokens,
            cost_usd=t.cost_usd,
            cost_unknown_calls=t.cost_unknown_calls,
        ),
        people=[
            PersonUsageOut(
                user_id=p.user_id,
                name=p.name,
                email=p.email,
                questions=p.questions,
                input_tokens=p.input_tokens,
                output_tokens=p.output_tokens,
                cost_usd=p.cost_usd,
            )
            for p in result.people
        ],
        history=[
            MonthUsageOut(
                month=h.month,
                questions=h.questions,
                input_tokens=h.input_tokens,
                output_tokens=h.output_tokens,
                cost_usd=h.cost_usd,
            )
            for h in result.history
        ],
    )


@router.get(
    "/usage",
    response_model=UsageOut,
    responses=_ERRORS,
    summary="AI usage for a month (default: this month), per person, and the last 6 months",
)
def get_usage(
    tenant: ManageOrg,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
) -> UsageOut:
    org = _org(tenant)
    now_month = current_month(tenant.service.clock(), org.timezone)
    if month is None:
        chosen = now_month
    else:
        year, number = (int(part) for part in month.split("-"))
        chosen = month_of(date(year, number, 1), org.timezone)
    return _usage_out(
        summary(tenant.db, org, chosen), is_current=chosen.first_day == now_month.first_day
    )
