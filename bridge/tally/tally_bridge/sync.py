"""One sync: read Tally (export only), send the rows to the platform in batches, then say the sync is complete.

* **Full** read: every master, and vouchers month by month (small exports keep Tally responsive). When the
  platform hears "complete" for a full read, it removes rows Tally no longer has (deleted vouchers).
* **Incremental** read: every master (they are small), and only the vouchers whose ``AlterID`` is higher than the
  highest one already sent. Nothing is removed. A full read runs at least every ``full_sync_hours`` (24 h), when
  the company changes, and the first time.
* **Resuming:** progress is saved after every batch the platform accepted. A batch is saved to the outbox before
  it is sent, and re-sent unchanged after a crash or lost network; the platform ignores a batch number it already
  has, and rows are keyed by Tally's GUID, so nothing is ever counted twice.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeVar

from tally_bridge.platform_client import (
    PlatformClient,
    PlatformRefused,
    PlatformUnavailable,
    Settings,
)
from tally_bridge.store import PendingSync, Store, SyncState
from tally_bridge.tally_client import Company, TallyClient
from tally_bridge.tally_xml import SPECS, Record

MASTERS = ("voucher_types", "groups", "ledgers", "stock_items")
PERIOD_MASTERS = ("ledgers", "stock_items")
VOUCHERS = "vouchers"
FUTURE_DAYS = 366  # vouchers dated ahead (post-dated cheques, planned entries) are read too
T = TypeVar("T")


class SyncAborted(Exception):
    """The sync cannot go on (no company open, another company, refused by the platform). Starts over later."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SyncInterrupted(Exception):
    """The platform could not be reached for a while. Progress is kept; the next attempt continues."""


@dataclass
class SyncReport:
    sync_id: str
    mode: str
    resumed: bool
    records: dict[str, int] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_tally_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def month_start(value: date) -> date:
    return value.replace(day=1)


def add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


class SyncEngine:
    def __init__(
        self,
        tally: TallyClient,
        platform: PlatformClient,
        store: Store,
        *,
        settings: Settings,
        company: str | None = None,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], object] = time.sleep,
        attempts: int = 6,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.tally = tally
        self.platform = platform
        self.store = store
        self.settings = settings
        self.company = company
        self._clock = clock
        self._sleep = sleep
        self._attempts = attempts
        self._jitter = jitter

    # ---------------------------------------------------------------------------------------- company
    def pick_company(self, companies: list[Company]) -> Company:
        if not companies:
            raise SyncAborted("no_company", "No company is open in Tally.")
        if self.company:
            for company in companies:
                if company.name == self.company:
                    return company
            raise SyncAborted(
                "company_not_open", f"The company {self.company!r} is not open in Tally."
            )
        return companies[0]

    def _full_due(self, state: SyncState) -> bool:
        if state.last_full_sync_at is None or state.last_alter_id is None:
            return True
        last = datetime.fromisoformat(state.last_full_sync_at)
        return self._clock() - last >= timedelta(hours=self.settings.full_sync_hours)

    def _year_start(self, starting_from: str) -> date:
        """The current financial year's first day: the month and day of the company's year start (1 April
        in India when Tally does not say)."""
        today = self._clock().date()
        first = _parse_tally_date(starting_from)
        month, day = (first.month, first.day) if first else (4, 1)
        try:
            start = date(today.year, month, day)
        except ValueError:
            start = date(today.year, month, 1)
        return start if start <= today else date(start.year - 1, start.month, start.day)

    def _history_start(self, books_from: str) -> date:
        today = self._clock().date()
        earliest = add_months(month_start(today), -self.settings.history_months)
        books = _parse_tally_date(books_from)
        return max(earliest, month_start(books)) if books else earliest

    # ------------------------------------------------------------------------------------------- sync
    def sync(self, *, full: bool = False) -> SyncReport:
        state = self.store.state()
        company = self.pick_company(self.tally.companies())
        pending = state.pending
        if pending is not None and pending.company_guid != company.guid:
            pending = (
                None  # Tally shows another company now: what was half sent belongs to the old one
            )
            self.store.clear_outbox()
        resumed = pending is not None
        if pending is None:
            changed = state.company_guid is not None and state.company_guid != company.guid
            if changed:
                state.last_alter_id = None
                state.last_full_sync_at = None
            mode = "full" if full or changed or self._full_due(state) else "incremental"
            pending = PendingSync(
                sync_id=str(uuid.uuid4()),
                mode=mode,
                started_at=self._clock().isoformat(),
                company=company.name,
                company_guid=company.guid,
                alter_floor=None if mode == "full" else state.last_alter_id,
            )
            state.pending = pending
            self.store.save_state(state)
        self._flush_outbox(state, pending)
        for name in MASTERS:
            progress = pending.dataset(name)
            if progress.finished:
                continue
            if name in PERIOD_MASTERS:
                # Balances for the current financial year up to today, stated on every row.
                start, today = self._year_start(company.starting_from), self._clock().date()
                records = self.tally.export(
                    SPECS[name], company=pending.company, from_date=start, to_date=today
                )
                for record in records:
                    record.fields["PERIODFROM"] = start.strftime("%Y%m%d")
                    record.fields["PERIODTO"] = today.strftime("%Y%m%d")
            else:
                records = self.tally.export(SPECS[name], company=pending.company)
            self._send_records(state, pending, name, records)
            progress.finished = True
            self.store.save_state(state)
        self._vouchers(state, pending, company)
        self._complete(state, pending)
        return SyncReport(
            sync_id=pending.sync_id,
            mode=pending.mode,
            resumed=resumed,
            records={name: p.records for name, p in pending.progress.items()},
        )

    def _vouchers(self, state: SyncState, pending: PendingSync, company: Company) -> None:
        progress = pending.dataset(VOUCHERS)
        if progress.finished:
            return
        today = self._clock().date()
        start = self._history_start(company.books_from)
        future = today + timedelta(days=FUTURE_DAYS)
        if pending.mode == "incremental":
            records = self.tally.export(
                SPECS[VOUCHERS],
                company=pending.company,
                from_date=start,
                to_date=future,
                alter_id_above=pending.alter_floor or 0,
            )
            self._send_records(state, pending, VOUCHERS, records)
        else:
            month = date.fromisoformat(progress.next_from) if progress.next_from else start
            while month <= today:
                following = add_months(month, 1)
                last_day = following - timedelta(days=1)
                to_date = future if last_day >= today else last_day
                records = self.tally.export(
                    SPECS[VOUCHERS], company=pending.company, from_date=month, to_date=to_date
                )
                self._send_records(state, pending, VOUCHERS, records)
                month = following
                progress.next_from = month.isoformat()
                self.store.save_state(state)
        progress.finished = True
        self.store.save_state(state)

    # ---------------------------------------------------------------------------------------- sending
    def _retrying(self, call: Callable[[], T]) -> T:
        for attempt in range(self._attempts):
            try:
                return call()
            except PlatformUnavailable as error:
                if attempt == self._attempts - 1:
                    raise SyncInterrupted(str(error)) from error
                delay = error.retry_after or min(60.0, 2.0**attempt) * (0.5 + self._jitter())
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _send_records(
        self, state: SyncState, pending: PendingSync, name: str, records: list[Record]
    ) -> None:
        if name == VOUCHERS:
            for record in records:
                alter = record.fields.get("ALTERID", "").strip()
                if alter.isdigit():
                    pending.alter_id_max = max(pending.alter_id_max, int(alter))
        chunk: list[dict[str, Any]] = []
        size = 2
        for record in records:
            item = record.as_json()
            item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 1
            if chunk and (
                len(chunk) >= self.settings.max_batch_rows
                or size + item_size > self.settings.max_batch_bytes
            ):
                self._deliver(state, pending, name, chunk)
                chunk, size = [], 2
            chunk.append(item)
            size += item_size
        if chunk:
            self._deliver(state, pending, name, chunk)

    def _deliver(
        self, state: SyncState, pending: PendingSync, name: str, records: list[dict[str, Any]]
    ) -> None:
        batch = {
            "sync_id": pending.sync_id,
            "seq": pending.dataset(name).batches,
            "dataset": name,
            "mode": pending.mode,
            "company": pending.company,
            "company_guid": pending.company_guid,
            "records": records,
        }
        self.store.save_outbox(batch)
        self._send_outbox(state, pending, batch)

    def _send_outbox(self, state: SyncState, pending: PendingSync, batch: dict[str, Any]) -> None:
        name = str(batch["dataset"])
        records = list(batch["records"])
        try:
            self._retrying(lambda: self.platform.send_batch(batch))
        except PlatformRefused as error:
            if error.status == 413 and len(records) > 1:
                # Too large for the platform: send it as two smaller batches (the refused number is reused).
                self.store.clear_outbox()
                half = len(records) // 2
                self._deliver(state, pending, name, records[:half])
                self._deliver(state, pending, name, records[half:])
                return
            self._abort(state)
            raise SyncAborted(error.code, error.message) from error
        progress = pending.dataset(name)
        progress.batches = max(progress.batches, int(batch["seq"]) + 1)
        progress.records += len(records)
        self.store.save_state(
            state
        )  # first the progress, then the outbox: a crash in between re-sends
        self.store.clear_outbox()

    def _flush_outbox(self, state: SyncState, pending: PendingSync) -> None:
        batch = self.store.outbox()
        if batch is None:
            return
        if batch.get("sync_id") != pending.sync_id or not isinstance(batch.get("records"), list):
            self.store.clear_outbox()
            return
        self._send_outbox(state, pending, batch)

    def _abort(self, state: SyncState) -> None:
        state.pending = None
        self.store.save_state(state)
        self.store.clear_outbox()

    def _complete(self, state: SyncState, pending: PendingSync) -> None:
        body = {
            "mode": pending.mode,
            "company": pending.company,
            "company_guid": pending.company_guid,
            "alter_id_max": pending.alter_id_max,
            "datasets": {
                name: {
                    "batches": pending.dataset(name).batches,
                    "records": pending.dataset(name).records,
                    # Only a dataset read whole lets the platform remove rows Tally no longer has.
                    "whole": name in MASTERS or pending.mode == "full",
                }
                for name in (*MASTERS, VOUCHERS)
            },
        }
        try:
            self._retrying(lambda: self.platform.complete(pending.sync_id, body))
        except PlatformRefused as error:
            self._abort(state)
            raise SyncAborted(error.code, error.message) from error
        now = self._clock().isoformat()
        state.pending = None
        state.company_guid = pending.company_guid
        state.last_sync_at = now
        if pending.mode == "full":
            state.last_full_sync_at = now
            state.last_alter_id = pending.alter_id_max
        else:
            state.last_alter_id = max(state.last_alter_id or 0, pending.alter_id_max)
        self.store.save_state(state)
