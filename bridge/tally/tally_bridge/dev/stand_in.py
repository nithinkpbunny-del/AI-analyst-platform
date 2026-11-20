"""A stand-in for TallyPrime's XML server (development and tests; real Tally cannot run here).

It answers the export requests the bridge makes (collections of companies, voucher types, groups, ledgers, stock
items and vouchers, with date ranges and the alter-id filter) in Tally's XML shape, including Tally's quirks the
bridge must survive: ``ACTION="Create"`` on exported vouchers, ``NAME.LIST`` elements, quantities like
``" 2 Nos"``, rates like ``"499.00/Nos"``, debits as negative amounts, fields nobody asked for, optional UTF-16
answers and forbidden characters like ``&#4;``.

**Any request that is not an export is refused and recorded** in ``refused`` (real Tally would apply it). Tests
assert that list stays empty: the bridge never sends an import, alter, create or delete request.

The demo company "UrbanCart Retail (demo)" is built from the demo fixtures in ``eval/fixtures/`` when present:
one sales voucher per Shopify order (party = customers of the order's region, sales ledger per category), credit
notes for refunds, monthly receipts, purchases at cost, supplier payments and the departments' actual expenses from
``zoho_budget_expenses.xlsx``. Without the fixtures a small built-in sample is used. Run it on its own with::

    python -m tally_bridge.dev.stand_in --port 9000 [--live 60]
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import httpx
from defusedxml import ElementTree as SafeET

COMPANY = "UrbanCart Retail (demo)"
COMPANY_GUID = "5b1d3c00-7a11-4c0e-9d2a-00000000c0de"
FIXTURES = Path(__file__).resolve().parents[4] / "eval" / "fixtures"
REGIONS = ("North", "South", "East", "West")
DEPARTMENTS = ("Marketing", "Operations", "Technology", "Sales", "HR")

# (name, parent, primary group, is revenue, affects gross profit, is deemed positive)
GROUPS: tuple[tuple[str, str, str, bool, bool, bool], ...] = (
    ("Capital Account", "", "Capital Account", False, False, False),
    ("Current Assets", "", "Current Assets", False, False, True),
    ("Current Liabilities", "", "Current Liabilities", False, False, False),
    ("Bank Accounts", "Current Assets", "Current Assets", False, False, True),
    ("Cash-in-Hand", "Current Assets", "Current Assets", False, False, True),
    ("Sundry Debtors", "Current Assets", "Current Assets", False, False, True),
    ("Sundry Creditors", "Current Liabilities", "Current Liabilities", False, False, False),
    ("Sales Accounts", "", "Sales Accounts", True, True, False),
    ("Purchase Accounts", "", "Purchase Accounts", True, True, True),
    ("Indirect Expenses", "", "Indirect Expenses", True, False, True),
)
VOUCHER_TYPES = (
    ("Sales", ""),
    ("Online Sales", "Sales"),
    ("Purchase", ""),
    ("Receipt", ""),
    ("Payment", ""),
    ("Journal", ""),
    ("Contra", ""),
    ("Credit Note", ""),
    ("Debit Note", ""),
)


def _amount(value: float) -> str:
    return f"{value:.2f}"


def _date(value: date) -> str:
    return value.strftime("%Y%m%d")


@dataclass
class Line:
    ledger: str
    amount: float  # Tally's sign: debit negative, credit positive


@dataclass
class Item:
    stock_item: str
    quantity: float
    rate: float
    discount: float
    amount: float  # positive
    ledger: str  # the accounting allocation (sales or purchase ledger)


@dataclass
class Voucher:
    guid: str
    master_id: int
    alter_id: int
    date: date
    type_name: str
    number: str
    party: str
    lines: list[Line]
    items: list[Item] = field(default_factory=list)
    cancelled: bool = False
    optional: bool = False


@dataclass
class Ledger:
    name: str
    parent: str
    guid: str
    alter_id: int
    opening: float  # Tally's sign


@dataclass
class StockItem:
    name: str
    parent: str
    guid: str
    alter_id: int
    quantity: float
    value: float


@dataclass
class DemoCompany:
    name: str = COMPANY
    guid: str = COMPANY_GUID
    books_from: date = date(2025, 1, 1)
    ledgers: dict[str, Ledger] = field(default_factory=dict)
    stock_items: dict[str, StockItem] = field(default_factory=dict)
    vouchers: dict[str, Voucher] = field(default_factory=dict)
    next_id: int = 1

    def new_id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value

    def guid_for(self, master_id: int) -> str:
        return f"{self.guid[:24]}{master_id:012x}"

    def add_ledger(self, name: str, parent: str, opening: float = 0.0) -> None:
        master = self.new_id()
        self.ledgers[name] = Ledger(name, parent, self.guid_for(master), master, opening)

    def add_voucher(
        self,
        when: date,
        type_name: str,
        party: str,
        lines: list[Line],
        items: list[Item] | None = None,
        *,
        cancelled: bool = False,
        optional: bool = False,
    ) -> Voucher:
        master = self.new_id()
        prefix = {"Online Sales": "UC", "Credit Note": "CN", "Purchase": "PUR"}.get(type_name, "V")
        voucher = Voucher(
            guid=self.guid_for(master),
            master_id=master,
            alter_id=master,
            date=when,
            type_name=type_name,
            number=f"{prefix}/{master}",
            party=party,
            lines=lines,
            items=items or [],
            cancelled=cancelled,
            optional=optional,
        )
        self.vouchers[voucher.guid] = voucher
        return voucher

    # ----------------------------------------------------------------------------- changes (dev, tests)
    def alter(self, guid: str, *, factor: float = 1.1) -> Voucher:
        """Change a voucher's amounts (as a person editing it in Tally would): it gets a new alter id."""
        voucher = self.vouchers[guid]
        voucher.lines = [
            Line(line.ledger, round(line.amount * factor, 2)) for line in voucher.lines
        ]
        voucher.items = [
            Item(
                i.stock_item, i.quantity, i.rate, i.discount, round(i.amount * factor, 2), i.ledger
            )
            for i in voucher.items
        ]
        voucher.alter_id = self.new_id()
        return voucher

    def delete(self, guid: str) -> None:
        del self.vouchers[guid]

    def add_sale(
        self, when: date, region: str, product: str, category: str, amount: float
    ) -> Voucher:
        return self.add_voucher(
            when,
            "Online Sales",
            f"Customers - {region}",
            [Line(f"Customers - {region}", -amount), Line(f"Sales - {category}", amount)],
            [Item(product, 1, amount, 0.0, amount, f"Sales - {category}")],
        )


# ------------------------------------------------------------------------------------------ building
def _base(company: DemoCompany, categories: list[str]) -> None:
    company.add_ledger("Capital Account", "Capital Account", 10_000_000.0)
    company.add_ledger("HDFC Bank", "Bank Accounts", -10_000_000.0)
    company.add_ledger("Cash", "Cash-in-Hand")
    for region in REGIONS:
        company.add_ledger(f"Customers - {region}", "Sundry Debtors")
    for category in categories:
        company.add_ledger(f"Sales - {category}", "Sales Accounts")
        company.add_ledger(f"Purchase - {category}", "Purchase Accounts")
        company.add_ledger(f"Suppliers - {category}", "Sundry Creditors")
    for department in DEPARTMENTS:
        company.add_ledger(f"{department} Expenses", "Indirect Expenses")


def _sample_orders() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """A small built-in shop (tests): 3 products, ~90 orders in Jul-Sep 2026."""
    products = [
        {
            "title": "Wireless Earbuds",
            "product_type": "Electronics",
            "price": "1699",
            "cost_per_item": "1257.26",
            "inventory_quantity": "120",
        },
        {
            "title": "Cotton Kurta",
            "product_type": "Fashion",
            "price": "999",
            "cost_per_item": "419.58",
            "inventory_quantity": "300",
        },
        {
            "title": "Face Serum",
            "product_type": "Beauty",
            "price": "599",
            "cost_per_item": "227.62",
            "inventory_quantity": "200",
        },
    ]
    rng = random.Random(7)  # noqa: S311 - demo data
    orders = []
    day = date(2026, 7, 1)
    while day <= date(2026, 9, 30):
        if rng.random() < 0.95:
            product = products[rng.randrange(3)]
            quantity = rng.choice([1, 1, 2])
            price = float(product["price"])
            discount = round(price * quantity * rng.choice([0.0, 0.05, 0.1]), 2)
            total = round(price * quantity - discount, 2)
            refund = total if rng.random() < 0.05 else 0.0
            orders.append(
                {
                    "created_at": f"{day.isoformat()}T10:00:00+05:30",
                    "updated_at": f"{(day + timedelta(days=3)).isoformat()}T10:00:00+05:30",
                    "product_title": product["title"],
                    "product_type": product["product_type"],
                    "quantity": str(quantity),
                    "unit_price": product["price"],
                    "total_line_items_price": str(price * quantity),
                    "total_discounts": str(discount),
                    "total_price": str(total),
                    "total_refunds": str(refund),
                    "shipping_region": REGIONS[rng.randrange(4)],
                }
            )
        day += timedelta(days=1)
    return orders, products


def _read_fixtures(folder: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]] | None:
    orders_path, products_path = folder / "shopify_orders.csv", folder / "shopify_products.csv"
    if not (orders_path.exists() and products_path.exists()):
        return None
    with orders_path.open(newline="", encoding="utf-8") as handle:
        orders = list(csv.DictReader(handle))
    with products_path.open(newline="", encoding="utf-8") as handle:
        products = list(csv.DictReader(handle))
    return orders, products


def _expenses(folder: Path) -> dict[tuple[date, str], float]:
    path = folder / "zoho_budget_expenses.xlsx"
    if not path.exists():
        return {}
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - the stand-in also works without it
        return {}
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[tuple[date, str], float] = {}
    for row in workbook.worksheets[0].iter_rows(min_row=2, values_only=True):
        month, department, _budget, actual = row[:4]
        if isinstance(month, datetime) and isinstance(actual, int | float):
            out[(month.date().replace(day=1), str(department))] = float(actual)
    workbook.close()
    return out


def _month_end(value: date) -> date:
    following = (value.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def build_company(folder: Path | None = FIXTURES) -> DemoCompany:
    """The demo company. Deterministic: the same fixtures always give the same GUIDs and alter ids."""
    data = _read_fixtures(folder) if folder is not None else None
    orders, products = data if data is not None else _sample_orders()
    expenses = _expenses(folder) if data is not None and folder is not None else {}
    company = DemoCompany()
    categories = sorted({p["product_type"] for p in products})
    _base(company, categories)
    cost = {p["title"]: float(p["cost_per_item"]) for p in products}
    for product in products:
        master = company.new_id()
        quantity = float(product.get("inventory_quantity") or 0)
        company.stock_items[product["title"]] = StockItem(
            product["title"],
            product["product_type"],
            company.guid_for(master),
            master,
            quantity,
            round(quantity * cost[product["title"]], 2),
        )
    received: dict[tuple[date, str], float] = defaultdict(float)
    bought: dict[tuple[date, str], float] = defaultdict(float)
    refunds: list[tuple[date, str, str, float]] = []
    for order in sorted(orders, key=lambda o: o["created_at"]):
        when = date.fromisoformat(order["created_at"][:10])
        region, category = order["shipping_region"], order["product_type"]
        total = round(float(order["total_price"]), 2)
        quantity = float(order["quantity"])
        gross = float(order["total_line_items_price"]) or 1.0
        discount = round(float(order["total_discounts"]) / gross * 100, 2)
        party, sales = f"Customers - {region}", f"Sales - {category}"
        company.add_voucher(
            when,
            "Online Sales",
            party,
            [Line(party, -total)],
            [
                Item(
                    order["product_title"],
                    quantity,
                    float(order["unit_price"]),
                    discount,
                    total,
                    sales,
                )
            ],
        )
        received[(when.replace(day=1), region)] += total
        bought[(when.replace(day=1), category)] += quantity * cost.get(order["product_title"], 0.0)
        refund = round(float(order.get("total_refunds") or 0), 2)
        if refund > 0:
            refunds.append((date.fromisoformat(order["updated_at"][:10]), region, category, refund))
    for when, region, category, amount in sorted(refunds):
        party = f"Customers - {region}"
        company.add_voucher(
            when, "Credit Note", party, [Line(f"Sales - {category}", -amount), Line(party, amount)]
        )
        received[(when.replace(day=1), region)] -= amount
    months = sorted({month for month, _ in received} | {month for month, _ in expenses})
    for month in months:
        end = _month_end(month)
        for region in REGIONS:
            amount = round(received.get((month, region), 0.0), 2)
            if amount > 0:
                party = f"Customers - {region}"
                company.add_voucher(
                    end, "Receipt", party, [Line("HDFC Bank", -amount), Line(party, amount)]
                )
        for category in categories:
            amount = round(bought.get((month, category), 0.0), 2)
            if amount > 0:
                supplier = f"Suppliers - {category}"
                company.add_voucher(
                    end,
                    "Purchase",
                    supplier,
                    [Line(f"Purchase - {category}", -amount), Line(supplier, amount)],
                )
                company.add_voucher(
                    end, "Payment", supplier, [Line(supplier, -amount), Line("HDFC Bank", amount)]
                )
        for department in DEPARTMENTS:
            amount = round(expenses.get((month, department), 0.0), 2)
            if amount > 0:
                ledger = f"{department} Expenses"
                company.add_voucher(
                    end, "Payment", "HDFC Bank", [Line(ledger, -amount), Line("HDFC Bank", amount)]
                )
    # Two vouchers that are not in the books (cancelled, optional): the platform must leave them out.
    if months:
        first = months[0]
        company.add_voucher(
            first,
            "Payment",
            "HDFC Bank",
            [Line("HR Expenses", -99_999.0), Line("HDFC Bank", 99_999.0)],
            cancelled=True,
        )
        company.add_voucher(
            first,
            "Journal",
            "HR Expenses",
            [Line("HR Expenses", -55_555.0), Line("Cash", 55_555.0)],
            optional=True,
        )
    return company


# ------------------------------------------------------------------------------------------ answering
def _balances(company: DemoCompany, until: date | None, before: date | None) -> dict[str, float]:
    """Each ledger's balance (Tally's sign) from the opening and the vouchers in the books up to a date."""
    totals = {name: ledger.opening for name, ledger in company.ledgers.items()}
    for voucher in company.vouchers.values():
        if voucher.cancelled or voucher.optional:
            continue
        if until is not None and voucher.date > until:
            continue
        if before is not None and voucher.date >= before:
            continue
        for line in voucher.lines:
            totals[line.ledger] = totals.get(line.ledger, 0.0) + line.amount
        for item in voucher.items:
            totals[item.ledger] = totals.get(item.ledger, 0.0) + item.amount
    return totals


def _yes(value: bool) -> str:
    return "Yes" if value else "No"


def _el(tag: str, value: str) -> str:
    return f"<{tag}>{escape(value)}</{tag}>"


class StandInTally:
    def __init__(self, company: DemoCompany | None = None) -> None:
        self.company = company or build_company()
        self.requests: list[bytes] = []
        self.refused: list[bytes] = []
        self.offline = False
        self.no_company = False
        self.utf16 = False
        self.junk_characters = True
        self._lock = threading.Lock()

    # ------------------------------------------------------------------------------------- requests
    def handle(self, body: bytes) -> bytes:
        with self._lock:
            self.requests.append(body)
            try:
                root = SafeET.fromstring(body)
            except Exception:
                return self._encode("<RESPONSE>Unknown Request, cannot be processed</RESPONSE>")
            request = (root.findtext("HEADER/TALLYREQUEST") or "").strip()
            changes = (
                request.lower() != "export"
                or root.find(".//TALLYMESSAGE") is not None
                or root.find(".//IMPORTDATA") is not None
                or any("ACTION" in {k.upper() for k in el.attrib} for el in root.iter())
                or any(el.get("ISMODIFY", "No") != "No" for el in root.iter("COLLECTION"))
            )
            if changes:
                self.refused.append(body)
                return self._encode(
                    "<RESPONSE><LINEERROR>The stand-in refuses changes</LINEERROR></RESPONSE>"
                )
            kind = (root.findtext(".//COLLECTION/TYPE") or "").strip()
            statics = root.find("BODY/DESC/STATICVARIABLES")

            def static(name: str) -> str | None:
                value = statics.findtext(name) if statics is not None else None
                return value.strip() if value else None

            company = static("SVCURRENTCOMPANY")
            if self.no_company or (company is not None and company != self.company.name):
                return self._encode(
                    "<RESPONSE><LINEERROR>Could not find Company</LINEERROR></RESPONSE>"
                )
            from_date = _parse(static("SVFROMDATE"))
            to_date = _parse(static("SVTODATE"))
            formula = root.findtext(".//SYSTEM") or ""
            match = re.match(r"^\$AlterID > (\d+)$", formula.strip())
            alter_above = int(match.group(1)) if match else None
            objects = self._objects(kind, from_date, to_date, alter_above)
            return self._encode(
                "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
                f"<BODY><DESC></DESC><DATA><COLLECTION>{objects}</COLLECTION></DATA></BODY></ENVELOPE>"
            )

    def _encode(self, xml: str) -> bytes:
        return xml.encode("utf-16") if self.utf16 else xml.encode("utf-8")

    def _objects(
        self, kind: str, from_date: date | None, to_date: date | None, alter_above: int | None
    ) -> str:
        c = self.company
        if kind == "Company":
            return (
                f"<COMPANY NAME={quoteattr(c.name)}>{_el('NAME', c.name)}{_el('GUID', c.guid)}"
                f"{_el('BOOKSFROM', _date(c.books_from))}{_el('STARTINGFROM', '20250401')}</COMPANY>"
            )
        if kind == "VoucherType":
            return "".join(
                f"<VOUCHERTYPE NAME={quoteattr(name)}>{_el('GUID', c.guid_for(9000 + i))}"
                f"{_el('PARENT', parent or name)}{_el('ALTERID', str(9000 + i))}</VOUCHERTYPE>"
                for i, (name, parent) in enumerate(VOUCHER_TYPES)
            )
        if kind == "Group":
            return "".join(
                f"<GROUP NAME={quoteattr(name)} RESERVEDNAME={quoteattr(name)}>"
                f"{_el('GUID', c.guid_for(8000 + i))}{_el('PARENT', parent)}"
                f"{_el('ISREVENUE', _yes(revenue))}{_el('AFFECTSGROSSPROFIT', _yes(gross))}"
                f"{_el('ISDEEMEDPOSITIVE', _yes(positive))}{_el('ALTERID', str(8000 + i))}"
                f"{_el('PRIMARYGROUP', primary)}</GROUP>"
                for i, (name, parent, primary, revenue, gross, positive) in enumerate(GROUPS)
            )
        if kind == "Ledger":
            primary = {name: p for name, _parent, p, *_ in GROUPS}
            opening = _balances(c, None, from_date) if from_date else {}
            closing = _balances(c, to_date, None)
            return "".join(
                f'<LEDGER NAME={quoteattr(l.name)} RESERVEDNAME="">{_el("GUID", l.guid)}'
                f"{_el('PARENT', l.parent)}"
                f"{_el('OPENINGBALANCE', _amount(opening.get(l.name, l.opening)))}"
                f"{_el('CLOSINGBALANCE', _amount(closing.get(l.name, 0.0)))}"
                f"{_el('ALTERID', str(l.alter_id))}{_el('PRIMARYGROUP', primary.get(l.parent, ''))}"
                f"<LEDGERPHONE>98450 00000</LEDGERPHONE>"  # never asked for: the bridge must drop it
                f'<NAME.LIST TYPE="String"><NAME>{escape(l.name)}</NAME></NAME.LIST></LEDGER>'
                for l in c.ledgers.values()  # noqa: E741
            )
        if kind == "StockItem":
            return "".join(
                f"<STOCKITEM NAME={quoteattr(s.name)}>{_el('GUID', s.guid)}{_el('PARENT', s.parent)}"
                f"{_el('CATEGORY', '')}{_el('BASEUNITS', 'Nos')}"
                f"{_el('OPENINGBALANCE', f' {s.quantity:g} Nos')}{_el('OPENINGVALUE', _amount(-s.value))}"
                f"{_el('CLOSINGBALANCE', f' {s.quantity:g} Nos')}{_el('CLOSINGVALUE', _amount(-s.value))}"
                f"{_el('ALTERID', str(s.alter_id))}</STOCKITEM>"
                for s in c.stock_items.values()
            )
        if kind == "Voucher":
            chosen = [
                v
                for v in c.vouchers.values()
                if (from_date is None or v.date >= from_date)
                and (to_date is None or v.date <= to_date)
                and (alter_above is None or v.alter_id > alter_above)
            ]
            chosen.sort(key=lambda v: (v.date, v.master_id))
            return "".join(self._voucher(v, i) for i, v in enumerate(chosen))
        return ""

    def _voucher(self, v: Voucher, index: int) -> str:
        lines = "".join(
            "<ALLLEDGERENTRIES.LIST>"
            f"{_el('LEDGERNAME', line.ledger)}{_el('ISDEEMEDPOSITIVE', _yes(line.amount < 0))}"
            f"{_el('AMOUNT', _amount(line.amount))}</ALLLEDGERENTRIES.LIST>"
            for line in v.lines
        )
        items = "".join(
            "<ALLINVENTORYENTRIES.LIST>"
            f"{_el('STOCKITEMNAME', item.stock_item)}{_el('ISDEEMEDPOSITIVE', 'No')}"
            f"{_el('RATE', f'{item.rate:.2f}/Nos')}{_el('DISCOUNT', f'{item.discount:g}')}"
            f"{_el('AMOUNT', _amount(item.amount))}"
            f"{_el('ACTUALQTY', f' {item.quantity:g} Nos')}{_el('BILLEDQTY', f' {item.quantity:g} Nos')}"
            "<ACCOUNTINGALLOCATIONS.LIST>"
            f"{_el('LEDGERNAME', item.ledger)}{_el('ISDEEMEDPOSITIVE', 'No')}"
            f"{_el('AMOUNT', _amount(item.amount))}</ACCOUNTINGALLOCATIONS.LIST>"
            "</ALLINVENTORYENTRIES.LIST>"
            for item in v.items
        )
        junk = "&#4;" if self.junk_characters and index % 97 == 0 else ""
        return (
            f'<VOUCHER REMOTEID="{v.guid}" VCHTYPE={quoteattr(v.type_name)} ACTION="Create">'
            f"{_el('GUID', v.guid)}{_el('ALTERID', str(v.alter_id))}{_el('MASTERID', str(v.master_id))}"
            f"{_el('DATE', _date(v.date))}{_el('VOUCHERTYPENAME', v.type_name)}"
            f"<VOUCHERNUMBER>{escape(v.number)}{junk}</VOUCHERNUMBER>"
            f"{_el('REFERENCE', '')}{_el('PARTYLEDGERNAME', v.party)}"
            f"{_el('ISCANCELLED', _yes(v.cancelled))}{_el('ISOPTIONAL', _yes(v.optional))}"
            f"{_el('ISINVOICE', _yes(bool(v.items)))}"
            "<NARRATION>Paid by Asha Rao, 98450 00000</NARRATION>"  # never asked for: dropped
            f"{lines}{items}</VOUCHER>"
        )


def _parse(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


class StandInTallyTransport(httpx.BaseTransport):
    """The stand-in behind an httpx transport (tests and the fake helper; no port needed)."""

    def __init__(self, tally: StandInTally) -> None:
        self.tally = tally

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.tally.offline:
            raise httpx.ConnectError("Tally is not running", request=request)
        if request.method != "POST":
            return httpx.Response(200, content=b"<RESPONSE>TallyPrime Server is Running</RESPONSE>")
        return httpx.Response(200, content=self.tally.handle(request.read()))


# ------------------------------------------------------------------------------------- running it
def serve(tally: StandInTally, *, host: str = "127.0.0.1", port: int = 9000) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send(b"<RESPONSE>TallyPrime Server is Running</RESPONSE>")

        def do_POST(self) -> None:
            length = min(int(self.headers.get("Content-Length") or 0), 1024 * 1024)
            self._send(tally.handle(self.rfile.read(length)))

        def _send(self, data: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def live(tally: StandInTally, every_seconds: float, stop: threading.Event) -> None:
    """Add a sale every ``every_seconds`` (so incremental syncs have something new)."""
    rng = random.Random(11)  # noqa: S311 - demo data
    items = list(tally.company.stock_items.values())
    while not stop.wait(every_seconds):
        item = items[rng.randrange(len(items))]
        with tally._lock:
            tally.company.add_sale(
                date.today(),
                REGIONS[rng.randrange(4)],
                item.name,
                item.parent,
                float(rng.randrange(500, 5000)),
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A stand-in for TallyPrime's XML server (development)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--live", type=float, default=0, help="add a sale every N seconds")
    parser.add_argument("--sample", action="store_true", help="use the small built-in company")
    args = parser.parse_args(argv)
    tally = StandInTally(build_company(None if args.sample else FIXTURES))
    server = serve(tally, host=args.host, port=args.port)
    stop = threading.Event()
    if args.live > 0:
        threading.Thread(target=live, args=(tally, args.live, stop), daemon=True).start()
    print(
        f"Tally stand-in on http://{args.host}:{args.port}: {tally.company.name}, "
        f"{len(tally.company.vouchers)} vouchers. Ctrl+C to stop."
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if tally.refused:
            print(f"WARNING: {len(tally.refused)} request(s) that would change data were refused.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
