"""Requests to TallyPrime's XML server, **export only**, and reading its answers.

Every request is built here from a fixed template (an inline TDL collection with ``ISMODIFY="No"``) and checked by
``check_export_only`` before it leaves the bridge. The check is strict on purpose: it accepts only the tags the
templates use, the request type ``Export``, no ``ACTION`` attributes, no ``TALLYMESSAGE`` (what Tally imports) and
formulas from a short allow-list. Anything else raises ``ReadOnlyViolation``, which is never caught.

Answers are flattened into plain ``{TAG: text}`` records. The bridge does not interpret amounts, dates or
quantities: the platform does (``app/connectors/tally.py``), so a parsing fix never needs a new bridge install.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element
from xml.sax.saxutils import escape

from defusedxml import ElementTree as SafeET

MAX_REQUEST_BYTES = 64 * 1024
MAX_VALUE_CHARS = 512


class ReadOnlyViolation(Exception):
    """A request that is not a plain export was about to be sent to Tally. Always a bug: never caught."""


class TallyError(Exception):
    """Tally answered, but with an error (unknown request, no company open, a TDL error)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CollectionSpec:
    """One export: a TDL collection of a Tally object type with the fields the platform needs (nothing more)."""

    ident: str  # the collection's name in the inline TDL
    tally_type: str  # Tally object type: Ledger, Voucher ...
    object_tag: str  # the element of each object in the answer
    methods: tuple[str, ...]  # NATIVEMETHOD entries (Tally's own fields)
    computes: tuple[tuple[str, str], ...] = ()  # (name, method): COMPUTE "Name : $Method"
    fetch: tuple[str, ...] = ()  # FETCH entries for sub-objects (voucher lines)
    nested: tuple[str, ...] = ()  # list elements read as lists of sub-records
    nested_fields: tuple[str, ...] = ()  # fields kept from the sub-records

    @property
    def wanted(self) -> frozenset[str]:
        return frozenset(
            [m.upper() for m in self.methods] + [name.upper() for name, _ in self.computes]
        )


COMPANIES = CollectionSpec(
    "AABridgeCompanies",
    "Company",
    "COMPANY",
    ("Name", "GUID", "BooksFrom", "StartingFrom"),
)
VOUCHER_TYPES = CollectionSpec(
    "AABridgeVoucherTypes",
    "VoucherType",
    "VOUCHERTYPE",
    ("Name", "GUID", "Parent", "AlterID"),
)
GROUPS = CollectionSpec(
    "AABridgeGroups",
    "Group",
    "GROUP",
    ("Name", "GUID", "Parent", "IsRevenue", "AffectsGrossProfit", "IsDeemedPositive", "AlterID"),
    computes=(("PrimaryGroup", "_PrimaryGroup"),),
)
LEDGERS = CollectionSpec(
    "AABridgeLedgers",
    "Ledger",
    "LEDGER",
    ("Name", "GUID", "Parent", "OpeningBalance", "ClosingBalance", "AlterID"),
    computes=(("PrimaryGroup", "_PrimaryGroup"),),
)
STOCK_ITEMS = CollectionSpec(
    "AABridgeStockItems",
    "StockItem",
    "STOCKITEM",
    (
        "Name",
        "GUID",
        "Parent",
        "Category",
        "BaseUnits",
        "OpeningBalance",
        "OpeningValue",
        "ClosingBalance",
        "ClosingValue",
        "AlterID",
    ),
)
LEDGER_LISTS = ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST")
INVENTORY_LISTS = ("ALLINVENTORYENTRIES.LIST", "INVENTORYENTRIES.LIST")
ALLOCATION_LISTS = ("ACCOUNTINGALLOCATIONS.LIST",)
VOUCHERS = CollectionSpec(
    "AABridgeVouchers",
    "Voucher",
    "VOUCHER",
    (
        "GUID",
        "AlterID",
        "MasterID",
        "Date",
        "VoucherTypeName",
        "VoucherNumber",
        "Reference",
        "PartyLedgerName",
        "IsCancelled",
        "IsOptional",
        "IsInvoice",
    ),
    fetch=(
        "AllLedgerEntries.LedgerName",
        "AllLedgerEntries.Amount",
        "AllLedgerEntries.IsDeemedPositive",
        "AllInventoryEntries.StockItemName",
        "AllInventoryEntries.ActualQty",
        "AllInventoryEntries.BilledQty",
        "AllInventoryEntries.Rate",
        "AllInventoryEntries.Amount",
        "AllInventoryEntries.Discount",
        "AllInventoryEntries.AccountingAllocations.LedgerName",
        "AllInventoryEntries.AccountingAllocations.Amount",
        "AllInventoryEntries.AccountingAllocations.IsDeemedPositive",
    ),
    nested=LEDGER_LISTS + INVENTORY_LISTS + ALLOCATION_LISTS,
    nested_fields=(
        "LEDGERNAME",
        "AMOUNT",
        "ISDEEMEDPOSITIVE",
        "STOCKITEMNAME",
        "ACTUALQTY",
        "BILLEDQTY",
        "RATE",
        "DISCOUNT",
    ),
)
SPECS: dict[str, CollectionSpec] = {
    "companies": COMPANIES,
    "voucher_types": VOUCHER_TYPES,
    "groups": GROUPS,
    "ledgers": LEDGERS,
    "stock_items": STOCK_ITEMS,
    "vouchers": VOUCHERS,
}


# --------------------------------------------------------------------------------------------- requests
def _tally_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def alter_filter(alter_id_above: int) -> str:
    return f"$AlterID > {int(alter_id_above)}"


def build_export(
    spec: CollectionSpec,
    *,
    company: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    alter_id_above: int | None = None,
) -> bytes:
    """The XML of one export request. Values are escaped; numbers and dates are formatted, never pasted."""
    statics = ["<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"]
    if company:
        statics.append(f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>")
    if from_date is not None:
        statics.append(f"<SVFROMDATE>{_tally_date(from_date)}</SVFROMDATE>")
    if to_date is not None:
        statics.append(f"<SVTODATE>{_tally_date(to_date)}</SVTODATE>")
    parts = [f"<TYPE>{spec.tally_type}</TYPE>"]
    parts += [f"<NATIVEMETHOD>{m}</NATIVEMETHOD>" for m in spec.methods]
    parts += [f"<FETCH>{f}</FETCH>" for f in spec.fetch]
    parts += [f"<COMPUTE>{name} : ${method}</COMPUTE>" for name, method in spec.computes]
    system = ""
    if alter_id_above is not None:
        parts.append("<FILTER>AABridgeAlterFilter</FILTER>")
        system = (
            '<SYSTEM TYPE="Formulae" NAME="AABridgeAlterFilter">'
            f"{escape(alter_filter(alter_id_above))}</SYSTEM>"
        )
    xml = (
        "<ENVELOPE>"
        "<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
        f"<TYPE>Collection</TYPE><ID>{spec.ident}</ID></HEADER>"
        "<BODY><DESC>"
        f"<STATICVARIABLES>{''.join(statics)}</STATICVARIABLES>"
        "<TDL><TDLMESSAGE>"
        f'<COLLECTION NAME="{spec.ident}" ISMODIFY="No">{"".join(parts)}</COLLECTION>'
        f"{system}"
        "</TDLMESSAGE></TDL>"
        "</DESC></BODY>"
        "</ENVELOPE>"
    )
    return xml.encode("utf-8")


# -------------------------------------------------------------------------------- the read-only guard
_ALLOWED_TAGS = frozenset(
    {
        "ENVELOPE",
        "HEADER",
        "VERSION",
        "TALLYREQUEST",
        "TYPE",
        "ID",
        "BODY",
        "DESC",
        "STATICVARIABLES",
        "SVEXPORTFORMAT",
        "SVCURRENTCOMPANY",
        "SVFROMDATE",
        "SVTODATE",
        "TDL",
        "TDLMESSAGE",
        "COLLECTION",
        "NATIVEMETHOD",
        "FETCH",
        "COMPUTE",
        "FILTER",
        "SYSTEM",
    }
)
_ALLOWED_ATTRIBUTES = {
    "COLLECTION": {"NAME", "ISMODIFY"},
    "SYSTEM": {"TYPE", "NAME"},
}
_ALLOWED_TYPES = frozenset(spec.tally_type for spec in SPECS.values())
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_COMPUTE = re.compile(r"^[A-Za-z][A-Za-z0-9]* : \$_?[A-Za-z][A-Za-z0-9]*$")
_FORMULA = re.compile(r"^\$AlterID > [0-9]{1,12}$")
_DATE = re.compile(r"^[0-9]{8}$")


def _violation(reason: str) -> ReadOnlyViolation:
    return ReadOnlyViolation(
        f"Refused to send a request to Tally that is not a plain export: {reason}"
    )


def check_export_only(body: bytes) -> None:
    """Raise ``ReadOnlyViolation`` unless ``body`` is an export request of the shape ``build_export`` makes.

    This is an allow-list, so requests Tally would treat as a change (``Import``/``Import Data``, a
    ``TALLYMESSAGE`` with objects, ``ACTION="Create"``/``"Alter"``/``"Delete"``, TDL functions or actions, any
    ``ISMODIFY="Yes"``) are all refused without having to be named.
    """
    if len(body) > MAX_REQUEST_BYTES:
        raise _violation("too large")
    try:
        root = SafeET.fromstring(body)
    except Exception as error:  # defusedxml raises several kinds; any of them means "not ours"
        raise _violation(f"not readable XML ({type(error).__name__})") from error
    if root.tag != "ENVELOPE":
        raise _violation("not an ENVELOPE")
    for element in root.iter():
        if element.tag not in _ALLOWED_TAGS:
            raise _violation(f"tag {element.tag!r} is not allowed")
        allowed = _ALLOWED_ATTRIBUTES.get(element.tag, set())
        for name in element.attrib:
            if name.upper() not in allowed:
                raise _violation(f"attribute {name!r} on {element.tag!r} is not allowed")
    request = (root.findtext("HEADER/TALLYREQUEST") or "").strip()
    if request != "Export":
        raise _violation(f"request type {request!r}")
    if (root.findtext("HEADER/TYPE") or "").strip() != "Collection":
        raise _violation("only collection exports are allowed")
    collections = root.findall("BODY/DESC/TDL/TDLMESSAGE/COLLECTION")
    if len(collections) != 1:
        raise _violation("exactly one collection is allowed")
    collection = collections[0]
    if collection.get("ISMODIFY") != "No":
        raise _violation('collections must be ISMODIFY="No"')
    if collection.get("NAME") != (root.findtext("HEADER/ID") or "").strip():
        raise _violation("the collection must be the one requested")
    if (collection.findtext("TYPE") or "").strip() not in _ALLOWED_TYPES:
        raise _violation("object type not allowed")
    for tag in ("NATIVEMETHOD", "FETCH"):
        for item in collection.findall(tag):
            if not _FIELD.match((item.text or "").strip()):
                raise _violation(f"{tag} value not allowed")
    for item in collection.findall("COMPUTE"):
        if not _COMPUTE.match((item.text or "").strip()):
            raise _violation("COMPUTE value not allowed")
    filters = collection.findall("FILTER")
    systems = root.findall("BODY/DESC/TDL/TDLMESSAGE/SYSTEM")
    if len(filters) > 1 or len(systems) != len(filters):
        raise _violation("one filter with its formula at most")
    for system in systems:
        if system.get("TYPE") != "Formulae" or system.get("NAME") != (filters[0].text or ""):
            raise _violation("only the alter-id formula is allowed")
        if not _FORMULA.match((system.text or "").strip()):
            raise _violation("formula not allowed")
    for name in ("SVFROMDATE", "SVTODATE"):
        value = root.findtext(f"BODY/DESC/STATICVARIABLES/{name}")
        if value is not None and not _DATE.match(value.strip()):
            raise _violation(f"{name} must be a date")
    export_format = root.findtext("BODY/DESC/STATICVARIABLES/SVEXPORTFORMAT")
    if export_format is not None and export_format.strip() != "$$SysName:XML":
        raise _violation("only XML exports are allowed")


# --------------------------------------------------------------------------------------------- answers
# Tally writes character references XML 1.0 forbids (e.g. "&#4;") and sometimes raw control characters.
_BAD_REFERENCE = re.compile(
    r"&#(?:x0*(?:[0-8bcef]|1[0-9a-f])|0*(?:[0-8]|1[124-9]|2[0-9]|3[01]));", re.I
)
_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


def decode(raw: bytes) -> str:
    """Text of a Tally answer. Tally answers in UTF-8 or UTF-16 (with or without a byte order mark)."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    elif len(raw) >= 2 and raw[1:2] == b"\x00" and raw[0:1] != b"\x00":
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace").lstrip("﻿")
    text = _BAD_REFERENCE.sub("", text)
    text = _BAD_CHARS.sub("", text)
    return _DECLARATION.sub("", text, count=1)


def _text(element: Element) -> str:
    return (element.text or "").strip()[:MAX_VALUE_CHARS]


def _flatten(element: Element, wanted: frozenset[str], nested: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    name = element.get("NAME")
    if name is not None and "NAME" in wanted:
        out["NAME"] = name.strip()[:MAX_VALUE_CHARS]
    for child in element:
        tag = child.tag
        if tag in nested:
            out.setdefault(tag, []).append(_flatten(child, wanted, nested))
        elif len(child) == 0:
            if tag in wanted:
                out[tag] = _text(child)
        elif tag.endswith(".LIST"):
            base = tag[: -len(".LIST")]
            first = next((g for g in child if len(g) == 0), None)
            if base in wanted and base not in out and first is not None:
                out[base] = _text(first)
    return out


def _error(root: Element) -> TallyError | None:
    line_error = root.findtext(".//LINEERROR")
    if line_error:
        return TallyError("tally_error", line_error.strip()[:200])
    if root.tag == "RESPONSE":
        message = (root.text or "").strip()
        if "unknown request" in message.lower():
            return TallyError("unknown_request", message[:200])
        if message:
            return TallyError("tally_error", message[:200])
    return None


def parse_objects(raw: bytes, spec: CollectionSpec) -> list[dict[str, Any]]:
    """The objects of an export answer as flat records, keeping only the fields ``spec`` asks for."""
    text = decode(raw)
    if not text.strip():
        raise TallyError("empty_answer", "Tally sent an empty answer.")
    try:
        root = SafeET.fromstring(text)
    except Exception as error:
        raise TallyError(
            "unreadable_answer", "Tally sent an answer that could not be read."
        ) from error
    problem = _error(root)
    if problem is not None:
        raise problem
    container = root.find(".//COLLECTION")
    elements = (
        [c for c in container if c.tag == spec.object_tag]
        if container is not None
        else list(root.iter(spec.object_tag))
    )
    wanted = spec.wanted | frozenset(spec.nested_fields)
    return [_flatten(element, wanted, spec.nested) for element in elements]


# --------------------------------------------------------------------------------- records for the platform
@dataclass
class Record:
    key: str
    fields: dict[str, str]
    ledger_entries: list[dict[str, str]] = field(default_factory=list)
    inventory_entries: list[dict[str, str]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "fields": self.fields}
        if self.ledger_entries:
            out["ledger_entries"] = self.ledger_entries
        if self.inventory_entries:
            out["inventory_entries"] = self.inventory_entries
        return out


def _plain(item: dict[str, Any]) -> dict[str, str]:
    return {k: v for k, v in item.items() if isinstance(v, str)}


def _lines(item: dict[str, Any], tags: tuple[str, ...]) -> list[dict[str, Any]]:
    for tag in tags:
        found = item.get(tag)
        if isinstance(found, list) and found:
            return [x for x in found if isinstance(x, dict)]
    return []


def to_record(item: dict[str, Any]) -> Record | None:
    """One object as the platform receives it. Vouchers carry their lines; an inventory line's accounting
    allocations (e.g. the sales ledger of an invoice) become ledger lines, so a voucher's debits equal its credits.
    Objects with neither a GUID nor a name are skipped."""
    fields = _plain(item)
    key = fields.get("GUID") or fields.get("NAME") or ""
    if not key:
        return None
    ledger = [_plain(line) for line in _lines(item, LEDGER_LISTS)]
    inventory = []
    for line in _lines(item, INVENTORY_LISTS):
        inventory.append(_plain(line))
        ledger += [_plain(a) for a in _lines(line, ALLOCATION_LISTS)]
    return Record(key=key[:200], fields=fields, ledger_entries=ledger, inventory_entries=inventory)
