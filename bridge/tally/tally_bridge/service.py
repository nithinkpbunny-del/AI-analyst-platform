"""The bridge's loop: sync every ``interval_minutes`` (15) and when the platform asks, long-poll in between.

* The poll is also the heartbeat: it tells the platform the bridge is alive, whether Tally answers, which company
  is open and the last sync's outcome. That is what the Connections screen shows.
* Nothing is lost when the PC sleeps, loses the network or restarts: times are wall-clock times (a sync is due
  right after waking), failures back off exponentially with jitter up to ``MAX_BACKOFF_SECONDS``, and a sync that
  was cut off continues from its saved progress.
* When the platform no longer accepts the token (unpaired, connection removed) the bridge stops and says so.
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tally_bridge import __version__
from tally_bridge.platform_client import (
    PlatformClient,
    PlatformUnavailable,
    Settings,
    Unauthorized,
)
from tally_bridge.store import Store
from tally_bridge.sync import SyncAborted, SyncEngine, SyncInterrupted
from tally_bridge.tally_client import TallyClient, TallyUnavailable
from tally_bridge.tally_xml import TallyError

logger = logging.getLogger("tally_bridge")

MAX_BACKOFF_SECONDS = 15 * 60
TALLY_RETRY_SECONDS = 5 * 60


def backoff_seconds(failures: int, jitter: float) -> float:
    """1, 2, 4 ... minutes of waiting after repeated failures, at most 15 minutes, with +/-50% jitter."""
    base = min(float(MAX_BACKOFF_SECONDS), 30.0 * 2.0 ** max(0, failures - 1))
    return base * (0.5 + jitter)


@dataclass
class Status:
    tally: str = "unknown"  # ok | unreachable | no_company | error
    tally_error: str | None = None
    company: str | None = None
    company_guid: str | None = None
    syncing: bool = False
    last_sync_at: str | None = None
    last_sync_mode: str | None = None
    last_error: str | None = None  # a short code for the platform to explain
    records: dict[str, int] = field(default_factory=dict)

    def as_json(self, next_sync_at: datetime | None) -> dict[str, Any]:
        return {
            "bridge_version": __version__,
            "tally": self.tally,
            "tally_error": self.tally_error,
            "company": self.company,
            "company_guid": self.company_guid,
            "syncing": self.syncing,
            "last_sync_at": self.last_sync_at,
            "last_sync_mode": self.last_sync_mode,
            "last_error": self.last_error,
            "next_sync_at": next_sync_at.isoformat() if next_sync_at else None,
        }


class Bridge:
    def __init__(
        self,
        tally: TallyClient,
        platform: PlatformClient,
        store: Store,
        *,
        settings: Settings | None = None,
        company: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.tally = tally
        self.platform = platform
        self.store = store
        self.settings = settings or Settings()
        self.company = company
        self.status = Status()
        self.unpaired = False
        self._clock = clock
        self._jitter = jitter
        self._failures = 0
        self._poll_failures = 0
        self.next_sync_at: datetime | None = None

    def engine(self, sleep: Callable[[float], object]) -> SyncEngine:
        return SyncEngine(
            self.tally,
            self.platform,
            self.store,
            settings=self.settings,
            company=self.company,
            clock=self._clock,
            sleep=sleep,
            jitter=self._jitter,
        )

    # ------------------------------------------------------------------------------------ one sync
    def sync_once(self, *, full: bool = False, sleep: Callable[[float], object]) -> bool:
        """Run (or continue) one sync. Returns True when it completed. Never raises for expected trouble."""
        self.status.syncing = True
        try:
            report = self.engine(sleep).sync(full=full)
        except TallyUnavailable:
            self.status.tally, self.status.tally_error = "unreachable", "not_reachable"
            self.status.last_error = "tally_unreachable"
            self._schedule(TALLY_RETRY_SECONDS)
            logger.warning("Tally is not reachable at %s; trying again later", self.tally.url)
            return False
        except TallyError as error:
            self.status.tally, self.status.tally_error = "error", error.code
            self.status.last_error = "tally_error"
            self._schedule(TALLY_RETRY_SECONDS)
            logger.warning("Tally answered with an error (%s)", error.code)
            return False
        except SyncAborted as error:
            if error.code in ("no_company", "company_not_open"):
                self.status.tally, self.status.tally_error = "no_company", error.code
            self.status.last_error = error.code
            self._failed()
            logger.warning("sync stopped: %s", error.message)
            return False
        except SyncInterrupted:
            self.status.last_error = "platform_unreachable"
            self._failed()
            logger.warning("the platform could not be reached; the sync continues later")
            return False
        finally:
            self.status.syncing = False
        state = self.store.state()
        self.status.tally, self.status.tally_error, self.status.last_error = "ok", None, None
        self.status.last_sync_at = state.last_sync_at
        self.status.last_sync_mode = report.mode
        self.status.records = report.records
        self.status.company_guid = state.company_guid
        self._failures = 0
        self._schedule(self.settings.interval_minutes * 60)
        logger.info(
            "sync %s finished (%s%s)",
            report.sync_id,
            report.mode,
            ", resumed" if report.resumed else "",
        )
        return True

    def _schedule(self, seconds: float) -> None:
        self.next_sync_at = self._clock() + timedelta(seconds=seconds)

    def _failed(self) -> None:
        self._failures += 1
        self._schedule(backoff_seconds(self._failures, self._jitter()))

    def probe_tally(self) -> None:
        try:
            companies = self.tally.companies()
        except TallyUnavailable:
            self.status.tally, self.status.tally_error = "unreachable", "not_reachable"
            return
        except TallyError as error:
            self.status.tally, self.status.tally_error = "error", error.code
            return
        try:
            company = self.engine(lambda _: None).pick_company(companies)
        except SyncAborted as error:
            self.status.tally, self.status.tally_error = "no_company", error.code
            return
        self.status.tally, self.status.tally_error = "ok", None
        self.status.company, self.status.company_guid = company.name, company.guid

    # ------------------------------------------------------------------------------------- the loop
    def poll(self, wait_seconds: int) -> tuple[bool, bool]:
        """Heartbeat + "read again now?". Returns (refresh, full). Raises Unauthorized / PlatformUnavailable."""
        answer = self.platform.poll(
            self.status.as_json(self.next_sync_at), wait_seconds=wait_seconds
        )
        self.settings = answer.settings
        return answer.refresh, answer.full

    def run(self, stop: threading.Event) -> None:
        """Until ``stop`` is set or the PC is unpaired."""
        self.next_sync_at = self._clock()
        self.probe_tally()
        refresh, full = False, False
        while not stop.is_set():
            due = self.next_sync_at is not None and self._clock() >= self.next_sync_at
            if refresh or due:
                try:
                    self.sync_once(full=full, sleep=stop.wait)
                except Unauthorized:
                    self._unpaired()
                    return
                refresh, full = False, False
                if stop.is_set():
                    return
            until_sync = (
                (self.next_sync_at - self._clock()).total_seconds() if self.next_sync_at else 0
            )
            wait = int(max(0, min(self.settings.poll_wait_seconds, until_sync)))
            try:
                refresh, full = self.poll(wait)
                self._poll_failures = 0
            except Unauthorized:
                self._unpaired()
                return
            except PlatformUnavailable:
                self._poll_failures += 1
                pause = min(backoff_seconds(self._poll_failures, self._jitter()), until_sync or 60)
                stop.wait(max(5.0, pause))
                continue
            if wait == 0 and not refresh:
                stop.wait(1.0)  # the platform answered at once (no long-poll): do not spin

    def _unpaired(self) -> None:
        self.unpaired = True
        logger.error(
            "The platform no longer accepts this PC (it was unpaired or the Tally connection was "
            "removed). Pair it again with a new code from the Connections screen."
        )
