"""The billing provider interface and its only implementation, a **stand-in** (BUILD_SPEC R7, M18; D118).

A real payment provider is **not built and not chosen**. When it is, it
implements ``BillingProvider`` and is selected by ``BILLING_PROVIDER``; the rest of the app already offers it every
billing event through ``deliver``. Until then ``StandInProvider`` is the only choice and it does nothing:
no network, no key, no payment, no customer record anywhere.

STAND-IN: everything marked this way must be replaced or removed when a real provider is added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.config import Settings

DeliveryStatus = Literal["not_sent", "sent", "failed"]
STAND_IN = "stand-in"


@dataclass(frozen=True)
class Offered:
    """One billing event as the provider sees it. Never holds customer data rows or credentials."""

    kind: str
    org_id: str
    period: str | None
    details: dict[str, object]


@dataclass(frozen=True)
class Delivery:
    status: DeliveryStatus
    note: str


class BillingProvider(Protocol):
    name: str
    live: bool  # True only for a provider that can really charge

    def deliver(self, event: Offered) -> Delivery: ...


class StandInProvider:
    """STAND-IN: accepts every event and sends it nowhere."""

    name = STAND_IN
    live = False

    def deliver(self, event: Offered) -> Delivery:
        return Delivery(
            "not_sent", "No payment provider is connected: nothing was sent or charged."
        )


def get_provider(settings: Settings) -> BillingProvider:
    """The configured provider. ``Settings`` only accepts ``stand_in`` today, so no other value can get here."""
    if settings.billing_provider == "stand_in":
        return StandInProvider()
    raise RuntimeError(  # pragma: no cover - the setting's type refuses any other value
        f"Unknown billing provider {settings.billing_provider!r}"
    )
