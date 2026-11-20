"""The Tally connector (BUILD_SPEC 8.8, M16; DECISIONS D108-D113).

Tally runs on the customer's PC and has no cloud API, so this connector does not reach out to anything: the Tally
bridge on that PC pushes Tally's own text into ``tally_records`` (``app.tally.ingest``), and this connector reads
those rows (SELECT only, the connection's own rows under RLS) and types them into normal tables:

* ``groups``, ``ledgers``, ``stock_items`` (masters; ledger and stock balances for the current financial year up
  to the sync day, as Tally reports them),
* ``vouchers`` (the day book: one row per voucher, cancelled and optional vouchers left out),
* ``voucher_ledger_entries`` (one row per ledger line: debit, credit, the ledger's group and nature),
* ``voucher_inventory_entries`` (one row per stock line: quantity, unit, rate, discount, amount),
* ``trial_balance`` (every ledger's opening and closing balance split into debit and credit).

**Money is in rupees** (Tally keeps amounts in the company's currency with two decimals), declared with
``stored_in="rupees"`` so the dictionary never divides (D95 pattern). Tally writes a debit as a negative number;
here a **debit is positive** (``amount``/``closing_balance``), and ``debit``/``credit`` columns are both positive.

Typing happens here and not on the PC, so a fix to how Tally's text is read never needs a new bridge install.
The parts of Tally's format least certain without a real Tally are listed in docs/SETUP_TALLY.md.

Freshness: ``FetchResult.data_as_of`` is the time of the bridge's last completed sync, so provenance says how old
the data really is ("Tally · vouchers · sent 3 h ago"), not when this table was built.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.connectors.base import (
    ConnectionTestResult,
    ConnectorError,
    DatasetInfo,
    FetchResult,
    TestCheck,
)
from app.connectors.tabular import ColumnInfo, ColumnType
from app.db.tenant import scope_to_org
from app.models import Connection, TallyDevice, TallyRecord

# What the bridge sends (dataset -> Tally fields kept). Anything else is dropped on arrival (data minimisation).
SENT_DATASETS = ("voucher_types", "groups", "ledgers", "stock_items", "vouchers")
VOUCHER_LINES = ("voucher_ledger_entries", "voucher_inventory_entries")
RAW_FIELDS: dict[str, frozenset[str]] = {
    "voucher_types": frozenset({"NAME", "GUID", "PARENT", "ALTERID"}),
    "groups": frozenset(
        {
            "NAME",
            "GUID",
            "PARENT",
            "PRIMARYGROUP",
            "ISREVENUE",
            "AFFECTSGROSSPROFIT",
            "ISDEEMEDPOSITIVE",
            "ALTERID",
        }
    ),
    "ledgers": frozenset(
        {
            "NAME",
            "GUID",
            "PARENT",
            "PRIMARYGROUP",
            "OPENINGBALANCE",
            "CLOSINGBALANCE",
            "ALTERID",
            "PERIODFROM",
            "PERIODTO",
        }
    ),
    "stock_items": frozenset(
        {
            "NAME",
            "GUID",
            "PARENT",
            "CATEGORY",
            "BASEUNITS",
            "OPENINGBALANCE",
            "OPENINGVALUE",
            "CLOSINGBALANCE",
            "CLOSINGVALUE",
            "ALTERID",
            "PERIODFROM",
            "PERIODTO",
        }
    ),
    "vouchers": frozenset(
        {
            "GUID",
            "ALTERID",
            "MASTERID",
            "DATE",
            "VOUCHERTYPENAME",
            "VOUCHERNUMBER",
            "REFERENCE",
            "PARTYLEDGERNAME",
            "ISCANCELLED",
            "ISOPTIONAL",
            "ISINVOICE",
        }
    ),
    "voucher_ledger_entries": frozenset({"LEDGERNAME", "AMOUNT", "ISDEEMEDPOSITIVE"}),
    "voucher_inventory_entries": frozenset(
        {
            "STOCKITEMNAME",
            "ACTUALQTY",
            "BILLEDQTY",
            "RATE",
            "AMOUNT",
            "DISCOUNT",
            "ISDEEMEDPOSITIVE",
        }
    ),
}

# Tally's predefined voucher types; custom ones ("Online Sales") point to one of them through their parent.
VOUCHER_CLASSES = {
    "sales": "sales",
    "purchase": "purchase",
    "receipt": "receipt",
    "payment": "payment",
    "journal": "journal",
    "contra": "contra",
    "credit note": "credit_note",
    "debit note": "debit_note",
    "sales order": "sales_order",
    "purchase order": "purchase_order",
    "delivery note": "delivery_note",
    "receipt note": "receipt_note",
    "stock journal": "stock_journal",
    "physical stock": "physical_stock",
    "memorandum": "memorandum",
    "reversing journal": "reversing_journal",
}
# Nature of Tally's primary groups (the 28 predefined groups that are not sub-groups).
NATURE = {
    "capital account": "Liabilities",
    "loans (liability)": "Liabilities",
    "current liabilities": "Liabilities",
    "branch / divisions": "Liabilities",
    "suspense a/c": "Liabilities",
    "fixed assets": "Assets",
    "investments": "Assets",
    "current assets": "Assets",
    "misc. expenses (asset)": "Assets",
    "sales accounts": "Income",
    "direct incomes": "Income",
    "indirect incomes": "Income",
    "purchase accounts": "Expenses",
    "direct expenses": "Expenses",
    "indirect expenses": "Expenses",
}


# --------------------------------------------------------------------------------- reading Tally's text
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def tally_number(text: Any) -> float | None:
    """Tally's amount text as a number with Tally's sign (debit negative): "-1281.72", "1,23,456.00",
    "₹ 500.00", "1000.00 Dr", "$ 10.00 @ ₹ 83.00/$ = ₹ 830.00" (the rupee value after "=" is used)."""
    if text is None:
        return None
    value = str(text).strip()
    if not value:
        return None
    if "=" in value:
        value = value.rsplit("=", 1)[1]
    suffix = value[-2:].lower()
    value = value.replace(",", "").replace("₹", "").replace("Rs.", "").replace(" ", "")
    negative = value.startswith("-") or (value.startswith("(") and value.endswith(")"))
    match = _NUMBER.search(value.replace("-", "", 1) if value.startswith("-") else value)
    if match is None:
        return None
    number = abs(float(match.group(0)))
    if suffix == "dr":
        return -number
    if suffix == "cr":
        return number
    return -number if negative else number


def quantity(text: Any) -> tuple[float | None, str | None]:
    """ " 2 Nos" -> (2.0, "Nos"); "2.500 kg = 2 Box" -> (2.5, "kg"); "-3 Nos" -> (-3.0, "Nos")."""
    if text is None:
        return None, None
    value = str(text).split("=", 1)[0].strip()
    match = re.match(r"^(-?[\d,]*\.?\d+)\s*(.*)$", value)
    if match is None:
        return None, None
    unit = match.group(2).strip() or None
    return float(match.group(1).replace(",", "")), unit


def rate(text: Any) -> float | None:
    """ "499.00/Nos" -> 499.0."""
    if text is None or not str(text).strip():
        return None
    number = tally_number(str(text).split("/", 1)[0])
    return abs(number) if number is not None else None


def tally_date(text: Any) -> date | None:
    value = str(text or "").strip()
    for pattern in ("%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def yes(text: Any) -> bool | None:
    value = str(text or "").strip().lower()
    if value in ("yes", "true", "1"):
        return True
    if value in ("no", "false", "0"):
        return False
    return None


def whole(text: Any) -> int | None:
    value = str(text or "").strip()
    return int(value) if value.isdigit() else None


def debit_positive(text: Any) -> float | None:
    number = tally_number(text)
    return None if number is None else round(-number, 2)


# ----------------------------------------------------------------------------------------- the tables
@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType
    money: bool = False


TABLES: dict[str, tuple[Column, ...]] = {
    "groups": (
        Column("name", "text"),
        Column("parent", "text"),
        Column("primary_group", "text"),
        Column("nature", "text"),
        Column("is_revenue", "boolean"),
        Column("affects_gross_profit", "boolean"),
        Column("guid", "text"),
    ),
    "ledgers": (
        Column("name", "text"),
        Column("ledger_group", "text"),
        Column("primary_group", "text"),
        Column("nature", "text"),
        Column("opening_balance", "number", money=True),
        Column("closing_balance", "number", money=True),
        Column("closing_dr_cr", "text"),
        Column("period_from", "date"),
        Column("period_to", "date"),
        Column("guid", "text"),
    ),
    "stock_items": (
        Column("name", "text"),
        Column("stock_group", "text"),
        Column("category", "text"),
        Column("unit", "text"),
        Column("opening_quantity", "number"),
        Column("opening_value", "number", money=True),
        Column("closing_quantity", "number"),
        Column("closing_value", "number", money=True),
        Column("period_from", "date"),
        Column("period_to", "date"),
        Column("guid", "text"),
    ),
    "vouchers": (
        Column("guid", "text"),
        Column("date", "date"),
        Column("voucher_type", "text"),
        Column("voucher_class", "text"),
        Column("voucher_number", "text"),
        Column("reference", "text"),
        Column("party_ledger", "text"),
        Column("amount", "number", money=True),
        Column("is_invoice", "boolean"),
        Column("alter_id", "integer"),
    ),
    "voucher_ledger_entries": (
        Column("voucher_guid", "text"),
        Column("line", "integer"),
        Column("date", "date"),
        Column("voucher_type", "text"),
        Column("voucher_class", "text"),
        Column("voucher_number", "text"),
        Column("party_ledger", "text"),
        Column("ledger", "text"),
        Column("ledger_group", "text"),
        Column("primary_group", "text"),
        Column("nature", "text"),
        Column("debit", "number", money=True),
        Column("credit", "number", money=True),
        Column("amount", "number", money=True),
    ),
    "voucher_inventory_entries": (
        Column("voucher_guid", "text"),
        Column("line", "integer"),
        Column("date", "date"),
        Column("voucher_type", "text"),
        Column("voucher_class", "text"),
        Column("voucher_number", "text"),
        Column("party_ledger", "text"),
        Column("stock_item", "text"),
        Column("stock_group", "text"),
        Column("quantity", "number"),
        Column("unit", "text"),
        Column("rate", "number", money=True),
        Column("discount_pct", "number"),
        Column("amount", "number", money=True),
    ),
    "trial_balance": (
        Column("ledger", "text"),
        Column("ledger_group", "text"),
        Column("primary_group", "text"),
        Column("nature", "text"),
        Column("opening_balance", "number", money=True),
        Column("closing_debit", "number", money=True),
        Column("closing_credit", "number", money=True),
        Column("period_from", "date"),
        Column("period_to", "date"),
    ),
}
TABLE_ORDER = tuple(TABLES)
KEYS = {
    "groups": "guid",
    "ledgers": "guid",
    "stock_items": "guid",
    "vouchers": "guid",
    "trial_balance": "ledger",
}


def _frame(table: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    data: dict[str, pd.Series] = {}
    for column in TABLES[table]:
        values = [row.get(column.name) for row in rows]
        if column.type == "date":
            data[column.name] = pd.to_datetime(pd.Series(values, dtype="object"), errors="coerce")
        elif column.type == "integer":
            data[column.name] = pd.Series(values, dtype="object").astype("Int64")
        elif column.type == "number":
            data[column.name] = pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce")
        elif column.type == "boolean":
            data[column.name] = pd.Series(values, dtype="object").astype("boolean")
        else:
            data[column.name] = pd.Series(
                [None if v in (None, "") else str(v) for v in values], dtype="string"
            )
    return pd.DataFrame(data)


def column_infos(table: str, frame: pd.DataFrame) -> list[ColumnInfo]:
    return [
        ColumnInfo(
            name=c.name,
            label=c.name,
            type=c.type,
            unit="INR" if c.money else None,
            # Tally keeps amounts in rupees (two decimals): known, never divided (D95, D111).
            stored_in="rupees" if c.money else None,
            date_format="Tally date" if c.type == "date" else None,
            null_count=int(frame[c.name].isna().sum()),
        )
        for c in TABLES[table]
    ]


@dataclass
class Raw:
    """Every row the bridge sent for one connection, by dataset (Tally's text)."""

    rows: dict[str, list[tuple[str, str | None, dict[str, Any]]]]

    def fields(self, dataset: str) -> list[dict[str, Any]]:
        return [f for _key, _parent, f in self.rows.get(dataset, [])]


class Shaper:
    """Turns the raw rows into the tables. Lookups (groups, ledgers, voucher types) are built once."""

    def __init__(self, raw: Raw) -> None:
        self.raw = raw
        self.groups = {str(g.get("NAME", "")): g for g in raw.fields("groups")}
        self.ledgers = {str(lg.get("NAME", "")): lg for lg in raw.fields("ledgers")}
        self.stock = {str(s.get("NAME", "")): s for s in raw.fields("stock_items")}
        self.types = {
            str(t.get("NAME", "")): str(t.get("PARENT", "")) for t in raw.fields("voucher_types")
        }
        self._index: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------------------------ lookups
    def primary_group(self, group: str) -> str:
        found = self.groups.get(group)
        if found is not None and found.get("PRIMARYGROUP"):
            return str(found["PRIMARYGROUP"])
        seen = set()
        while (
            group and group not in seen
        ):  # follow parents when Tally did not send the primary group
            seen.add(group)
            parent = str((self.groups.get(group) or {}).get("PARENT", ""))
            if not parent or parent.lower().startswith("primary"):
                return group
            group = parent
        return group

    @staticmethod
    def nature(primary: str) -> str | None:
        return NATURE.get(primary.strip().lower())

    def voucher_class(self, type_name: str) -> str:
        name, seen = type_name, set()
        while name and name not in seen:
            seen.add(name)
            found = VOUCHER_CLASSES.get(name.strip().lower())
            if found:
                return found
            name = self.types.get(name, "")
        return "other"

    def vouchers(self) -> dict[str, dict[str, Any]]:
        """Vouchers in the books (not cancelled, not optional) by GUID, with their typed header."""
        if self._index is None:
            out: dict[str, dict[str, Any]] = {}
            for key, _parent, f in self.raw.rows.get("vouchers", []):
                if yes(f.get("ISCANCELLED")) or yes(f.get("ISOPTIONAL")):
                    continue
                type_name = str(f.get("VOUCHERTYPENAME", ""))
                out[key] = {
                    "guid": key,
                    "date": tally_date(f.get("DATE")),
                    "voucher_type": type_name,
                    "voucher_class": self.voucher_class(type_name),
                    "voucher_number": f.get("VOUCHERNUMBER"),
                    "reference": f.get("REFERENCE"),
                    "party_ledger": f.get("PARTYLEDGERNAME"),
                    "is_invoice": yes(f.get("ISINVOICE")),
                    "alter_id": whole(f.get("ALTERID")),
                    "amount": 0.0,
                }
            for voucher_key, debit in self._debits().items():
                if voucher_key in out:
                    out[voucher_key]["amount"] = round(debit, 2)
            self._index = out
        return self._index

    def _debits(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for _key, parent, f in self.raw.rows.get("voucher_ledger_entries", []):
            amount = debit_positive(f.get("AMOUNT"))
            if parent and amount is not None and amount > 0:
                totals[parent] = totals.get(parent, 0.0) + amount
        return totals

    # ------------------------------------------------------------------------------------- tables
    def table(self, name: str) -> list[dict[str, Any]]:
        return getattr(self, f"_{name}")()  # type: ignore[no-any-return]

    def _groups(self) -> list[dict[str, Any]]:
        out = []
        for f in self.raw.fields("groups"):
            primary = str(f.get("PRIMARYGROUP") or self.primary_group(str(f.get("NAME", ""))))
            out.append(
                {
                    "name": f.get("NAME"),
                    "parent": f.get("PARENT") or None,
                    "primary_group": primary,
                    "nature": self.nature(primary),
                    "is_revenue": yes(f.get("ISREVENUE")),
                    "affects_gross_profit": yes(f.get("AFFECTSGROSSPROFIT")),
                    "guid": f.get("GUID"),
                }
            )
        return out

    def _ledger_group(self, ledger: str) -> tuple[str | None, str | None, str | None]:
        found = self.ledgers.get(ledger)
        if found is None:
            return None, None, None
        group = str(found.get("PARENT") or "")
        primary = str(found.get("PRIMARYGROUP") or self.primary_group(group))
        return group or None, primary or None, self.nature(primary)

    def _ledgers(self) -> list[dict[str, Any]]:
        out = []
        for f in self.raw.fields("ledgers"):
            group, primary, nature = self._ledger_group(str(f.get("NAME", "")))
            closing = debit_positive(f.get("CLOSINGBALANCE"))
            out.append(
                {
                    "name": f.get("NAME"),
                    "ledger_group": group,
                    "primary_group": primary,
                    "nature": nature,
                    "opening_balance": debit_positive(f.get("OPENINGBALANCE")),
                    "closing_balance": closing,
                    "closing_dr_cr": None if closing is None else ("Dr" if closing >= 0 else "Cr"),
                    "period_from": tally_date(f.get("PERIODFROM")),
                    "period_to": tally_date(f.get("PERIODTO")),
                    "guid": f.get("GUID"),
                }
            )
        return out

    def _stock_items(self) -> list[dict[str, Any]]:
        out = []
        for f in self.raw.fields("stock_items"):
            opening_qty, unit = quantity(f.get("OPENINGBALANCE"))
            closing_qty, closing_unit = quantity(f.get("CLOSINGBALANCE"))
            opening_value, closing_value = (
                tally_number(f.get("OPENINGVALUE")),
                tally_number(f.get("CLOSINGVALUE")),
            )
            out.append(
                {
                    "name": f.get("NAME"),
                    "stock_group": f.get("PARENT") or None,
                    "category": f.get("CATEGORY") or None,
                    "unit": f.get("BASEUNITS") or unit or closing_unit,
                    "opening_quantity": opening_qty,
                    "opening_value": None if opening_value is None else abs(opening_value),
                    "closing_quantity": closing_qty,
                    "closing_value": None if closing_value is None else abs(closing_value),
                    "period_from": tally_date(f.get("PERIODFROM")),
                    "period_to": tally_date(f.get("PERIODTO")),
                    "guid": f.get("GUID"),
                }
            )
        return out

    def _vouchers(self) -> list[dict[str, Any]]:
        return sorted(self.vouchers().values(), key=lambda v: (v["date"] or date.min, v["guid"]))

    def _header(self, parent: str | None) -> dict[str, Any] | None:
        return self.vouchers().get(parent or "")

    def _voucher_ledger_entries(self) -> list[dict[str, Any]]:
        out = []
        for key, parent, f in self.raw.rows.get("voucher_ledger_entries", []):
            header = self._header(parent)
            amount = debit_positive(f.get("AMOUNT"))
            if header is None or amount is None:
                continue
            ledger = str(f.get("LEDGERNAME", ""))
            group, primary, nature = self._ledger_group(ledger)
            out.append(
                {
                    "voucher_guid": parent,
                    "line": _line(key),
                    "date": header["date"],
                    "voucher_type": header["voucher_type"],
                    "voucher_class": header["voucher_class"],
                    "voucher_number": header["voucher_number"],
                    "party_ledger": header["party_ledger"],
                    "ledger": ledger,
                    "ledger_group": group,
                    "primary_group": primary,
                    "nature": nature,
                    "debit": amount if amount > 0 else 0.0,
                    "credit": -amount if amount < 0 else 0.0,
                    "amount": amount,
                }
            )
        return out

    def _voucher_inventory_entries(self) -> list[dict[str, Any]]:
        out = []
        for key, parent, f in self.raw.rows.get("voucher_inventory_entries", []):
            header = self._header(parent)
            if header is None:
                continue
            item = str(f.get("STOCKITEMNAME", ""))
            billed, unit = quantity(f.get("BILLEDQTY") or f.get("ACTUALQTY"))
            amount = tally_number(f.get("AMOUNT"))
            discount = tally_number(f.get("DISCOUNT"))
            out.append(
                {
                    "voucher_guid": parent,
                    "line": _line(key),
                    "date": header["date"],
                    "voucher_type": header["voucher_type"],
                    "voucher_class": header["voucher_class"],
                    "voucher_number": header["voucher_number"],
                    "party_ledger": header["party_ledger"],
                    "stock_item": item,
                    "stock_group": (self.stock.get(item) or {}).get("PARENT") or None,
                    "quantity": None if billed is None else abs(billed),
                    "unit": unit,
                    "rate": rate(f.get("RATE")),
                    "discount_pct": discount,
                    "amount": None if amount is None else abs(amount),
                }
            )
        return out

    def _trial_balance(self) -> list[dict[str, Any]]:
        out = []
        for row in self._ledgers():
            closing = row["closing_balance"] or 0.0
            out.append(
                {
                    "ledger": row["name"],
                    "ledger_group": row["ledger_group"],
                    "primary_group": row["primary_group"],
                    "nature": row["nature"],
                    "opening_balance": row["opening_balance"],
                    "closing_debit": closing if closing > 0 else 0.0,
                    "closing_credit": -closing if closing < 0 else 0.0,
                    "period_from": row["period_from"],
                    "period_to": row["period_to"],
                }
            )
        return out


def _line(key: str) -> int | None:
    tail = key.rsplit("#", 1)[-1]
    digits = tail[1:]
    return int(digits) if digits.isdigit() else None


# ------------------------------------------------------------------------------------------ connector
def read_raw(db: DbSession, connection_id: uuid.UUID, datasets: tuple[str, ...]) -> Raw:
    rows: dict[str, list[tuple[str, str | None, dict[str, Any]]]] = {d: [] for d in datasets}
    query = (
        select(TallyRecord.dataset, TallyRecord.key, TallyRecord.parent_key, TallyRecord.fields)
        .where(TallyRecord.connection_id == connection_id, TallyRecord.dataset.in_(datasets))
        .order_by(TallyRecord.dataset, TallyRecord.key)
        .execution_options(yield_per=10_000)
    )
    for dataset, key, parent, fields in db.execute(query):
        rows[dataset].append((key, parent, fields))
    return Raw(rows)


NEEDS: dict[str, tuple[str, ...]] = {
    "groups": ("groups",),
    "ledgers": ("groups", "ledgers"),
    "stock_items": ("stock_items",),
    "vouchers": ("voucher_types", "vouchers", "voucher_ledger_entries"),
    "voucher_ledger_entries": (
        "groups",
        "ledgers",
        "voucher_types",
        "vouchers",
        "voucher_ledger_entries",
    ),
    "voucher_inventory_entries": (
        "stock_items",
        "voucher_types",
        "vouchers",
        "voucher_ledger_entries",
        "voucher_inventory_entries",
    ),
    "trial_balance": ("groups", "ledgers"),
}


class TallyConnector:
    type = "tally"
    pushed = True  # the bridge pushes; freshness is its last push (D111)

    def __init__(
        self,
        *,
        session_factory: sessionmaker[DbSession],
        org_id: uuid.UUID,
        connection_id: uuid.UUID,
        max_rows: int,
        clock: Callable[[], datetime],
        offline_after_minutes: int,
    ) -> None:
        self._sessions = session_factory
        self._org_id = org_id
        self._connection_id = connection_id
        self._max_rows = max_rows
        self._clock = clock
        self._offline_after = offline_after_minutes

    def _session(self) -> DbSession:
        db = self._sessions()
        scope_to_org(db, self._org_id)
        return db

    def _state(self) -> tuple[dict[str, Any], TallyDevice | None]:
        with self._session() as db:
            connection = db.get(Connection, self._connection_id)
            if connection is None:
                db.commit()
                raise ConnectorError("not_found", "This Tally connection no longer exists.")
            device = db.scalar(
                select(TallyDevice).where(
                    TallyDevice.connection_id == self._connection_id,
                    TallyDevice.status == "active",
                )
            )
            config = dict(connection.config.get("tally") or {})
            db.commit()
        return config, device

    def test(self) -> ConnectionTestResult:
        config, device = self._state()
        now = self._clock()
        paired = device is not None
        online = bool(
            device is not None
            and device.last_seen_at is not None
            and (now - device.last_seen_at).total_seconds() <= self._offline_after * 60
        )
        tally_ok = bool(device is not None and (device.status_json or {}).get("tally") == "ok")
        synced = bool(config.get("last_sync_id"))
        checks = [
            TestCheck("pc_paired", paired),
            TestCheck("pc_online", online),
            TestCheck("tally_reachable", tally_ok),
            TestCheck("data_received", synced),
            TestCheck("read_only", True),
        ]
        if not paired:
            message = "No Tally PC is paired. Get a pairing code and pair the PC."
        elif not online:
            message = "The Tally PC is offline. Its data is shown as last sent."
        elif not tally_ok:
            message = "The Tally PC is online, but Tally is not answering. Open Tally on that PC."
        elif not synced:
            message = "The Tally PC is online and reading Tally for the first time."
        else:
            message = "The Tally PC is online and sending data. This platform only reads."
        return ConnectionTestResult(
            ok=paired and online and tally_ok, message=message, checks=checks
        )

    def list_datasets(self) -> list[DatasetInfo]:
        config, _ = self._state()
        if not config.get("last_sync_id"):
            return []  # nothing received yet: no empty tables in the dictionary
        return [DatasetInfo(ref=name, name=name) for name in TABLE_ORDER]

    def source_version(self, dataset: DatasetInfo) -> str | None:
        config, _ = self._state()
        last = config.get("last_sync_id")
        return f"sync:{last}" if last else None

    def supports_incremental(self) -> bool:
        return False  # the rows are on the platform already; a full read of them is cheap

    def supports_pushdown(self) -> bool:
        return False

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        if dataset.ref not in TABLES:
            raise ConnectorError("not_found", "This Tally table does not exist.")
        config, _ = self._state()
        last = config.get("last_sync_id")
        as_of = config.get("last_sync_at")
        with self._session() as db:
            raw = read_raw(db, self._connection_id, NEEDS[dataset.ref])
            db.commit()
        rows = Shaper(raw).table(dataset.ref)
        truncated = len(rows) > self._max_rows
        frame = _frame(dataset.ref, rows[: self._max_rows])
        return FetchResult(
            frame=frame,
            columns=column_infos(dataset.ref, frame),
            source_version=f"sync:{last}" if last else None,
            key_column=KEYS.get(dataset.ref),
            truncated=truncated,
            data_as_of=datetime.fromisoformat(as_of) if as_of else None,
        )
