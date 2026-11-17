"""A local stand-in for Zoho Books, used in development without app credentials (``APP_ENV=development``) and by
tests.

It imitates what the connector uses, in Zoho's own formats, for every Zoho data centre host:

* the accounts server: ``POST /oauth/v2/token`` (authorization code and refresh token grants; errors as HTTP 200
  with ``{"error": ...}``, like Zoho) and ``POST /oauth/v2/token/revoke``;
* the Books API ``/books/v3``: ``GET /organizations``, the list endpoints (``invoices``, ``bills``, ``expenses``,
  ``contacts``, ``items``, ``chartofaccounts``) with ``page``/``per_page``/``page_context``, ``date_start`` and
  ``last_modified_time`` filters, and ``GET /reports/profitandloss`` for one period. Anything that is not a
  ``GET`` to the Books API is refused and counted in ``writes_received`` (the connector must never send one).

Two demo organisations: **UrbanCart** (the default) built from the demo fixtures (``make fixtures``), and a small
**UrbanCart Labs**. UrbanCart's expenses are the monthly *actual* spend per department of
``eval/fixtures/zoho_budget_expenses.xlsx`` (expense account = department), so they add up to the answer key's
figures to the rupee; invoices are the monthly sales to Small Business customers, bills the monthly cost of goods
per category, and the profit and loss report sums them. Nothing here is a secret.
"""

from __future__ import annotations

import csv
import itertools
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.connectors.zoho_books import REGIONS

STAND_IN_CLIENT_ID = "1000.STANDINZOHO"
STAND_IN_CLIENT_SECRET = "stand-in-zoho-secret"  # noqa: S105 (not a real secret)
REFRESH_PREFIX = "1000.standin-refresh."
ACCESS_PREFIX = "1000.standin-access."
REVOKED_REFRESH_TOKEN = "1000.standin-refresh.revoked"  # noqa: S105 (tests: a token Zoho no longer accepts)
CODE_PREFIX = "1000.standin-code."
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "eval" / "fixtures"
MAIN_ORG = "60010000001"
LABS_ORG = "60010000002"
GRANTED_SCOPES = (
    "ZohoBooks.invoices.READ,ZohoBooks.bills.READ,ZohoBooks.expenses.READ,ZohoBooks.contacts.READ,"
    "ZohoBooks.settings.READ,ZohoBooks.accountants.READ"
)
DEPARTMENTS = ("Marketing", "Operations", "Technology", "Sales", "HR")
LIST_KEYS = {
    "invoices": "invoices",
    "bills": "bills",
    "expenses": "expenses",
    "contacts": "contacts",
    "items": "items",
    "chartofaccounts": "chartofaccounts",
}
_codes = itertools.count(1)


def issue_code(scope: str = GRANTED_SCOPES) -> str:
    """A one-time code the stand-in consent page hands back (the scope travels inside it)."""
    return f"{CODE_PREFIX}{next(_codes)}.{scope}"


def _stamp(day: date, hour: int = 10) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00+0530"


def _month_end(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


# --------------------------------------------------------------------------------------------- the data
@dataclass
class OrgData:
    organization_id: str
    name: str
    tables: dict[str, list[dict[str, Any]]]
    # (month start) -> section -> account -> amount
    pnl: dict[date, dict[str, dict[str, float]]]
    currency: str = "INR"
    default: bool = False


def _read_budget(path: Path) -> list[tuple[date, str, int, int]]:
    """(month, department, budget, actual) from the demo workbook, or a small built-in sample."""
    if path.exists():
        import openpyxl

        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = book["Budget vs Actual"]
        rows = []
        for row in itertools.islice(sheet.iter_rows(values_only=True), 1, None):
            month, department, budget, actual = row[:4]
            if month is None:
                continue
            day = (
                month.date() if isinstance(month, datetime) else date.fromisoformat(str(month)[:10])
            )
            rows.append((day, str(department), int(str(budget)), int(str(actual))))
        book.close()
        return rows
    out = []
    for index in range(21):
        month = date(2025 + (index // 12), index % 12 + 1, 1)
        for d_index, department in enumerate(DEPARTMENTS):
            budget = 200_000 + 100_000 * d_index
            actual = budget + (40_000 if department == "Marketing" and index >= 18 else 0)
            out.append((month, department, budget, actual))
    return out


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _expenses(
    budget: list[tuple[date, str, int, int]], accounts: dict[str, str]
) -> list[dict[str, Any]]:
    """Three expenses per department and month that add up exactly to the month's actual spend."""
    rows = []
    for number, (month, department, _budget, actual) in enumerate(budget, start=1):
        first = round(actual * 0.5)
        second = round(actual * 0.3)
        parts = (first, second, actual - first - second)
        for part_index, amount in enumerate(parts):
            day = month + timedelta(days=4 + 9 * part_index)
            rows.append(
                {
                    "expense_id": f"46000{number:05d}{part_index}",
                    "date": day.isoformat(),
                    "account_id": accounts[department],
                    "account_name": department,
                    "paid_through_account_name": "HDFC Current Account",
                    "vendor_id": "",
                    "vendor_name": "",
                    "reference_number": f"EXP-{month:%Y%m}-{department[:3].upper()}-{part_index + 1}",
                    "status": "unbilled",
                    "is_billable": False,
                    "currency_code": "INR",
                    "total": float(amount),
                    "bcy_total": float(amount),
                    "description": "Private note that the connector must never keep",
                    "created_time": _stamp(day),
                    "last_modified_time": _stamp(day, 11),
                }
            )
    return rows


def _sample_inputs() -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Orders, customers and products for the built-in sample (no fixture files): 21 months, 12 customers."""
    categories = ("Electronics", "Fashion", "Home & Kitchen", "Beauty", "Sports", "Fashion")
    products = [
        {
            "id": f"80000000{i:02d}",
            "title": f"Sample product {i}",
            "product_type": categories[i - 1],
            "sku": f"SMP-{i:03d}",
            "price": str(500 * i),
            "cost_per_item": str(300 * i),
            "status": "active",
            "inventory_quantity": str(10 * i),
        }
        for i in range(1, 7)
    ]
    customers = [
        {
            "id": f"70000000{i:02d}",
            "last_name": ("Rao", "Shah", "Iyer", "Das")[i % 4],
            "city": ("Pune", "Chennai", "Delhi", "Kolkata")[i % 4],
            "segment": "Small Business" if i % 2 else "Consumer",
        }
        for i in range(1, 13)
    ]
    orders = []
    for i in range(21 * 12):
        month = date(2025 + (i // 12) // 12, (i // 12) % 12 + 1, 1)
        day = month + timedelta(days=(i % 12) * 2)
        product = products[i % 6]
        quantity = 1 + i % 3
        orders.append(
            {
                "created_at": f"{day.isoformat()}T10:00:00+05:30",
                "customer_id": customers[i % 12]["id"],
                "product_id": product["id"],
                "product_type": product["product_type"],
                "quantity": str(quantity),
                "total_price": str(float(product["price"]) * quantity),
                "total_refunds": "0",
            }
        )
    return orders, customers, products


def _main_org(fixtures_dir: Path) -> OrgData:
    budget = _read_budget(fixtures_dir / "zoho_budget_expenses.xlsx")
    orders = _read_csv(fixtures_dir / "shopify_orders.csv")
    customer_rows = _read_csv(fixtures_dir / "shopify_customers.csv")
    products = _read_csv(fixtures_dir / "shopify_products.csv")
    if not orders or not customer_rows or not products:
        orders, customer_rows, products = _sample_inputs()
    customers = {r["id"]: r for r in customer_rows}

    accounts = [
        ("Sales", "income", "4000"),
        ("Cost of Goods Sold", "cost_of_goods_sold", "5000"),
        *((d, "expense", f"6{i:03d}") for i, d in enumerate(DEPARTMENTS, start=1)),
        ("Accounts Receivable", "accounts_receivable", "1200"),
        ("Accounts Payable", "accounts_payable", "2100"),
        ("HDFC Current Account", "bank", "1010"),
    ]
    chart = [
        {
            "account_id": f"4600000{index:04d}",
            "account_name": name,
            "account_code": code,
            "account_type": kind,
            "is_active": True,
            "parent_account_name": "",
            "last_modified_time": _stamp(date(2024, 12, 1)),
        }
        for index, (name, kind, code) in enumerate(accounts, start=1)
    ]
    account_ids = {str(row["account_name"]): str(row["account_id"]) for row in chart}

    costs = {p["id"]: float(p.get("cost_per_item") or 0) for p in products}
    categories = sorted({p["product_type"] for p in products}) or ["General"]
    vendors = {c: f"{c} Supplies Pvt Ltd" for c in categories}

    sales: dict[date, float] = {}
    cogs: dict[tuple[date, str], float] = {}
    sb_month: dict[tuple[str, date], dict[str, Any]] = {}
    for order in orders:
        day = date.fromisoformat(order["created_at"][:10])
        month = day.replace(day=1)
        net = float(order["total_price"]) - float(order["total_refunds"] or 0)
        sales[month] = sales.get(month, 0.0) + net
        category = order["product_type"]
        cost = costs.get(order["product_id"], 0.0) * int(order["quantity"])
        cogs[(month, category)] = cogs.get((month, category), 0.0) + cost
        customer = customers.get(order["customer_id"])
        if customer and customer.get("segment") == "Small Business":
            entry = sb_month.setdefault((order["customer_id"], month), {"total": 0.0, "first": day})
            entry["total"] += float(order["total_price"])
            entry["first"] = min(entry["first"], day)
    as_of = max(sales, default=date(2026, 9, 1))
    as_of = _month_end(as_of)

    suffixes = ("Traders", "Stores", "Enterprises", "Retail")
    contacts: dict[str, dict[str, Any]] = {}
    invoices = []
    for number, ((customer_id, _month), entry) in enumerate(
        sorted(sb_month.items(), key=lambda kv: (kv[0][1], kv[0][0])), start=1
    ):
        customer = customers[customer_id]
        contact_id = f"4700{customer_id[-6:]}"
        name = f"{customer['last_name']} {suffixes[int(customer_id) % 4]} ({customer['city']})"
        day = entry["first"]
        due = day + timedelta(days=30)
        total = round(entry["total"], 2)
        open_invoice = due > as_of - timedelta(days=15) or number % 23 == 0
        balance = total if open_invoice else 0.0
        status = "paid" if not open_invoice else ("overdue" if due < as_of else "sent")
        invoices.append(
            {
                "invoice_id": f"48000{number:06d}",
                "invoice_number": f"INV-{number:06d}",
                "reference_number": "",
                "date": day.isoformat(),
                "due_date": due.isoformat(),
                "customer_id": contact_id,
                "customer_name": name,
                "status": status,
                "currency_code": "INR",
                "total": total,
                "balance": balance,
                "email": "accounts@example.com",
                "created_time": _stamp(day),
                "last_modified_time": _stamp(day, 12),
            }
        )
        contact = contacts.setdefault(
            contact_id,
            {
                "contact_id": contact_id,
                "contact_name": name,
                "company_name": name,
                "contact_type": "customer",
                "status": "active",
                "currency_code": "INR",
                "outstanding_receivable_amount": 0.0,
                "outstanding_payable_amount": 0.0,
                "email": "accounts@example.com",
                "phone": "+91 98450 00000",
                "created_time": _stamp(day),
                "last_modified_time": _stamp(day),
            },
        )
        contact["outstanding_receivable_amount"] = round(
            contact["outstanding_receivable_amount"] + balance, 2
        )
    bills = []
    for number, ((month, category), amount) in enumerate(sorted(cogs.items()), start=1):
        day = _month_end(month)
        due = day + timedelta(days=30)
        open_bill = due > as_of
        vendor_id = f"4710{categories.index(category):06d}"
        bills.append(
            {
                "bill_id": f"49000{number:06d}",
                "bill_number": f"BILL-{month:%Y%m}-{category[:3].upper()}",
                "reference_number": "",
                "date": day.isoformat(),
                "due_date": due.isoformat(),
                "vendor_id": vendor_id,
                "vendor_name": vendors[category],
                "status": "open" if open_bill else "paid",
                "currency_code": "INR",
                "total": round(amount, 2),
                "balance": round(amount, 2) if open_bill else 0.0,
                "created_time": _stamp(day),
                "last_modified_time": _stamp(day, 12),
            }
        )
        contacts.setdefault(
            vendor_id,
            {
                "contact_id": vendor_id,
                "contact_name": vendors[category],
                "company_name": vendors[category],
                "contact_type": "vendor",
                "status": "active",
                "currency_code": "INR",
                "outstanding_receivable_amount": 0.0,
                "outstanding_payable_amount": 0.0,
                "created_time": _stamp(date(2024, 12, 1)),
                "last_modified_time": _stamp(date(2024, 12, 1)),
            },
        )
        if open_bill:
            vendor = contacts[vendor_id]
            vendor["outstanding_payable_amount"] = round(
                vendor["outstanding_payable_amount"] + amount, 2
            )

    items = [
        {
            "item_id": f"4720{p['id'][-6:]}",
            "name": p["title"],
            "sku": p["sku"],
            "status": p.get("status") or "active",
            "product_type": "goods",
            "item_type": "inventory",
            "unit": "pcs",
            "rate": float(p["price"]),
            "purchase_rate": float(p.get("cost_per_item") or 0),
            "stock_on_hand": float(p.get("inventory_quantity") or 0),
            "created_time": _stamp(date(2024, 5, 15)),
            "last_modified_time": _stamp(date(2024, 5, 15)),
        }
        for p in products
    ]

    pnl: dict[date, dict[str, dict[str, float]]] = {}
    for month, amount in sales.items():
        pnl.setdefault(month, {}).setdefault("Operating Income", {})["Sales"] = round(amount, 2)
    for (month, _category), amount in cogs.items():
        lines = pnl.setdefault(month, {}).setdefault("Cost of Goods Sold", {})
        lines["Purchases"] = round(lines.get("Purchases", 0.0) + amount, 2)
    for month, department, _budget, actual in budget:
        pnl.setdefault(month, {}).setdefault("Operating Expense", {})[department] = float(actual)

    return OrgData(
        organization_id=MAIN_ORG,
        name="UrbanCart",
        default=True,
        tables={
            "invoices": invoices,
            "bills": bills,
            "expenses": _expenses(budget, account_ids),
            "contacts": sorted(contacts.values(), key=lambda c: c["contact_id"]),
            "items": items,
            "chartofaccounts": chart,
        },
        pnl=pnl,
    )


def _labs_org() -> OrgData:
    day = date(2026, 8, 1)
    return OrgData(
        organization_id=LABS_ORG,
        name="UrbanCart Labs",
        tables={
            "invoices": [
                {
                    "invoice_id": "58000000001",
                    "invoice_number": "LAB-0001",
                    "date": day.isoformat(),
                    "due_date": (day + timedelta(days=15)).isoformat(),
                    "customer_id": "5700000001",
                    "customer_name": "Kumar Retail (Pune)",
                    "status": "paid",
                    "currency_code": "INR",
                    "total": 12_500.0,
                    "balance": 0.0,
                    "created_time": _stamp(day),
                    "last_modified_time": _stamp(day),
                }
            ],
            "bills": [],
            "expenses": [],
            "contacts": [],
            "items": [],
            "chartofaccounts": [],
        },
        pnl={day: {"Operating Income": {"Sales": 12_500.0}}},
    )


@lru_cache(maxsize=2)
def demo_orgs(fixtures_dir: Path = FIXTURES_DIR) -> dict[str, OrgData]:
    main = _main_org(fixtures_dir)
    labs = _labs_org()
    return {main.organization_id: main, labs.organization_id: labs}


def sample_orgs() -> dict[str, OrgData]:
    """A small organisation for tests: the built-in sample (no fixture files)."""
    return demo_orgs(Path("/nonexistent-zoho-fixtures"))


# ------------------------------------------------------------------------------------------- transport
def _json(body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )


def _books_error(status: int, code: int, message: str) -> httpx.Response:
    return _json({"code": code, "message": message}, status)


def _parse_time(text: str) -> datetime | None:
    text = text.strip().replace(" ", "+")
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class StandInZohoTransport(httpx.BaseTransport):
    """Zoho's accounts server and Books API for the demo organisations."""

    orgs: dict[str, OrgData] | None = None
    writes_received: int = 0
    revoked: set[str] = field(default_factory=set)
    # Tests: answer the next N Books calls with "token expired" (the connector refreshes once).
    expire_next: int = 0
    # Tests: Zoho's daily limit for every organisation (None = unlimited).
    daily_limit: int | None = None
    calls: int = 0
    token_calls: int = 0
    granted_scopes: str = GRANTED_SCOPES
    # Tests: an organisation whose Zoho refuses the profit and loss report to this access.
    refuse_reports: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _data(self) -> dict[str, OrgData]:
        if self.orgs is None:
            self.orgs = demo_orgs()
        return self.orgs

    # ------------------------------------------------------------------------------------------ OAuth
    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_calls += 1
        if (
            form.get("client_id") != STAND_IN_CLIENT_ID
            or form.get("client_secret") != STAND_IN_CLIENT_SECRET
        ):
            return _json({"error": "invalid_client"})
        api = next(
            (
                api
                for accounts, api in REGIONS.values()
                if request.url.host == accounts.split("//")[1]
            ),
            REGIONS["in"][1],
        )
        grant = form.get("grant_type")
        if grant == "authorization_code":
            code = form.get("code", "")
            if not code.startswith(CODE_PREFIX):
                return _json({"error": "invalid_code"})
            scope = code.split(".", 3)[-1] if code.count(".") >= 3 else self.granted_scopes
            number = code[len(CODE_PREFIX) :].split(".")[0]
            return _json(
                {
                    "access_token": f"{ACCESS_PREFIX}{number}",
                    "refresh_token": f"{REFRESH_PREFIX}{number}",
                    "scope": scope,
                    "api_domain": api,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        if grant == "refresh_token":
            token = form.get("refresh_token", "")
            if (
                not token.startswith(REFRESH_PREFIX)
                or token in self.revoked
                or token == REVOKED_REFRESH_TOKEN
            ):
                return _json({"error": "invalid_code"})
            return _json(
                {
                    "access_token": f"{ACCESS_PREFIX}{token[len(REFRESH_PREFIX) :]}.{self.token_calls}",
                    "api_domain": api,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        return _json({"error": "unsupported_grant_type"})

    # --------------------------------------------------------------------------------------- requests
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/v2/token" and request.method == "POST":
            return self._token(request)
        if path == "/oauth/v2/token/revoke" and request.method == "POST":
            token = request.url.params.get("token", "")
            self.revoked.add(token)
            return _json({"status": "success"})
        if not path.startswith("/books/v3/"):
            return _books_error(404, 5, "Invalid URL Passed")
        if request.method != "GET":
            with self.lock:
                self.writes_received += 1
            return _books_error(405, 37, "The HTTP method is not allowed for the resource")
        auth = request.headers.get("authorization", "")
        if not auth.startswith(f"Zoho-oauthtoken {ACCESS_PREFIX}"):
            return _books_error(401, 14, "Invalid OAuth token.")
        with self.lock:
            if self.expire_next > 0:
                self.expire_next -= 1
                return _books_error(401, 14, "Invalid OAuth token.")
            self.calls += 1
            if self.daily_limit is not None and self.calls > self.daily_limit:
                return _books_error(
                    429, 45, "You have reached the maximum API calls allowed per day."
                )
        name = path[len("/books/v3/") :].rstrip("/")
        params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if name == "organizations":
            return _json(
                {
                    "code": 0,
                    "message": "success",
                    "organizations": [
                        {
                            "organization_id": org.organization_id,
                            "name": org.name,
                            "contact_name": "Owner Name",
                            "email": "owner@example.com",
                            "is_default_org": org.default,
                            "currency_code": org.currency,
                            "time_zone": "Asia/Calcutta",
                        }
                        for org in self._data().values()
                    ],
                }
            )
        org = self._data().get(params.get("organization_id", ""))
        if org is None:
            return _books_error(400, 6041, "This user is not associated with the organization.")
        if name == "reports/profitandloss":
            if self.refuse_reports:
                return _books_error(401, 57, "You are not authorized to perform this operation")
            return self._pnl(org, params)
        key = LIST_KEYS.get(name)
        if key is None:
            return _books_error(404, 5, "Invalid URL Passed")
        return self._list(org, key, params)

    def _list(self, org: OrgData, key: str, params: dict[str, str]) -> httpx.Response:
        rows = org.tables.get(key, [])
        start = params.get("date_start")
        if start:
            rows = [r for r in rows if str(r.get("date", "9999")) >= start]
        modified = params.get("last_modified_time")
        if modified:
            since = _parse_time(modified)
            if since is None:
                return _books_error(400, 2, "Invalid value passed for last_modified_time")
            rows = [
                r
                for r in rows
                if (t := _parse_time(str(r.get("last_modified_time", "")))) is not None
                and t >= since
            ]
        try:
            page = max(1, int(params.get("page", "1")))
            per_page = min(200, max(1, int(params.get("per_page", "200"))))
        except ValueError:
            return _books_error(400, 2, "Invalid value passed for page")
        chunk = rows[(page - 1) * per_page : page * per_page]
        return _json(
            {
                "code": 0,
                "message": "success",
                key: chunk,
                "page_context": {
                    "page": page,
                    "per_page": per_page,
                    "has_more_page": page * per_page < len(rows),
                },
            }
        )

    @staticmethod
    def _pnl(org: OrgData, params: dict[str, str]) -> httpx.Response:
        try:
            first = date.fromisoformat(params["from_date"])
            last = date.fromisoformat(params["to_date"])
        except (KeyError, ValueError):
            return _books_error(400, 2, "Invalid value passed for from_date")
        totals: dict[str, dict[str, float]] = {}
        for month, sections in org.pnl.items():
            if first <= month <= last:
                for section, lines in sections.items():
                    for account, amount in lines.items():
                        bucket = totals.setdefault(section, {})
                        bucket[account] = bucket.get(account, 0.0) + amount

        def group(section: str) -> dict[str, Any]:
            lines = totals.get(section, {})
            return {
                "name": section,
                "total": round(sum(lines.values()), 2),
                "account_transactions": [
                    {"account_id": f"acc-{account}", "name": account, "total": round(amount, 2)}
                    for account, amount in sorted(lines.items())
                ],
            }

        income, cost, expense = (
            group("Operating Income"),
            group("Cost of Goods Sold"),
            group("Operating Expense"),
        )
        gross = income["total"] - cost["total"]
        operating = gross - expense["total"]
        report = [
            {
                "name": "Gross Profit",
                "total": round(gross, 2),
                "account_transactions": [income, cost],
            },
            {
                "name": "Operating Profit",
                "total": round(operating, 2),
                "account_transactions": [expense],
            },
            {"name": "Net Profit/Loss", "total": round(operating, 2), "account_transactions": []},
        ]
        return _json({"code": 0, "message": "success", "profit_and_loss": report})
