"""Turn rows of cells into a typed table (BUILD_SPEC 8.2): used for uploaded files and Google Sheets tabs.

What it does, in order:

1. **Header row**: the first of the top 20 rows that looks like column names (mostly text, at least half as
   wide as the table). Title rows above it ("UrbanCart sales report") are skipped. No such row: the columns are
   named "Column 1", "Column 2", ...
2. **Clean up**: fully empty rows and unnamed empty columns are dropped; a final "Total"/"Grand total" row is
   left out (it would double every sum) and reported.
3. **Types** per column, when at least 95% of the non-empty values fit: yes/no, whole number, number, date,
   date and time, otherwise text. Numbers understand ``₹``/``Rs``/``INR``, Indian (``12,34,567``) and Western
   (``1,234,567``) digit grouping, ``(1,200)`` for negatives, ``%`` and ``L``/``lakh``/``Cr``/``crore``.
   Dates are read day-first (``05/01/2026`` is 5 January), as Indian businesses write them, unless the values
   prove month-first. Times with a UTC offset and Unix timestamps are converted to the workspace's time zone.
   Values that do not fit the column's type are left empty and counted, so quality checks can say so.
4. **Names**: every column gets a stable ``snake_case`` name for queries and keeps its original header as label.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any, Literal

import numpy as np
import pandas as pd

ColumnType = Literal["boolean", "integer", "number", "date", "datetime", "text"]

HEADER_SCAN_ROWS = 20
TYPE_THRESHOLD = 0.95
_SAMPLE_FOR_FORMATS = 400
_MAX_NAME_LENGTH = 63

NULL_TOKENS = frozenset(
    {
        "",
        "-",
        "--",
        "—",
        "\u2013",  # en dash
        "na",
        "n/a",
        "#n/a",
        "null",
        "none",
        "nil",
        "nan",
        "#value!",
        "#ref!",
        "#div/0!",
    }
)
_TRUE = frozenset({"true", "yes", "y"})
_FALSE = frozenset({"false", "no", "n"})

# Currency markers in values. ``$`` and friends are only detected so that mixed currencies can be reported.
_CURRENCY = re.compile(r"(₹|\bRs\.?|\bINR\b|\$|\bUSD\b|€|\bEUR\b|£|\bGBP\b)", re.IGNORECASE)
_RUPEE_MARKERS = frozenset({"₹", "rs", "rs.", "inr"})
_MULTIPLIER = re.compile(r"\s*(lakhs?|lacs?|l|crores?|cr)\.?$", re.IGNORECASE)
_MULTIPLIERS = {
    "l": 1e5,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "cr": 1e7,
    "crore": 1e7,
    "crores": 1e7,
}
_PLAIN_NUMBER = re.compile(r"^-?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$", re.IGNORECASE)
_WESTERN_GROUPED = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_INDIAN_GROUPED = re.compile(r"^-?\d{1,2}(,\d{2})*,\d{3}(\.\d+)?$")
_LEADING_ZERO = re.compile(r"^0\d+$")
_TOTAL_LABEL = re.compile(r"^\s*(grand\s+)?(sub\s*)?totals?\s*:?\s*$", re.IGNORECASE)

# Columns whose digits are identifiers, not quantities (keep them as text: leading zeros matter).
_IDENTIFIER_HINT = re.compile(
    r"(phone|mobile|contact_no|pincode|pin_code|^pin$|zip|postal|gstin|^gst$|^pan$|aadhaar|ifsc|account_no|account_number)"
)
_TIME_HINT = re.compile(r"(_at$|^at$|date|time|timestamp|created|updated)")
_UNIX_SECONDS = (946_684_800, 4_102_444_800)  # 2000-01-01 .. 2100-01-01

# Formats tried for text dates, day-first (India) before month-first. Each: (strptime format, label for people).
_DAY_FIRST = [
    ("%d/%m/%Y", "dd/mm/yyyy"),
    ("%d-%m-%Y", "dd-mm-yyyy"),
    ("%d.%m.%Y", "dd.mm.yyyy"),
    ("%d/%m/%y", "dd/mm/yy"),
    ("%d-%m-%y", "dd-mm-yy"),
    ("%d/%m/%Y %H:%M", "dd/mm/yyyy hh:mm"),
    ("%d/%m/%Y %H:%M:%S", "dd/mm/yyyy hh:mm:ss"),
    ("%d-%m-%Y %H:%M", "dd-mm-yyyy hh:mm"),
    ("%d-%m-%Y %H:%M:%S", "dd-mm-yyyy hh:mm:ss"),
]
_MONTH_FIRST = [
    ("%m/%d/%Y", "mm/dd/yyyy"),
    ("%m-%d-%Y", "mm-dd-yyyy"),
    ("%m/%d/%y", "mm/dd/yy"),
    ("%m/%d/%Y %H:%M", "mm/dd/yyyy hh:mm"),
    ("%m/%d/%Y %H:%M:%S", "mm/dd/yyyy hh:mm:ss"),
]
_UNAMBIGUOUS = [
    ("%Y-%m-%d", "yyyy-mm-dd"),
    ("%Y/%m/%d", "yyyy/mm/dd"),
    ("%Y-%m-%d %H:%M", "yyyy-mm-dd hh:mm"),
    ("%Y-%m-%d %H:%M:%S", "yyyy-mm-dd hh:mm:ss"),
    ("%d %b %Y", "d Mon yyyy"),
    ("%d %B %Y", "d Month yyyy"),
    ("%d-%b-%Y", "d-Mon-yyyy"),
    ("%d-%b-%y", "d-Mon-yy"),
    ("%d %b %y", "d Mon yy"),
    ("%b %d, %Y", "Mon d, yyyy"),
    ("%B %d, %Y", "Month d, yyyy"),
    ("%b %Y", "Mon yyyy"),
    ("%B %Y", "Month yyyy"),
    ("%b-%y", "Mon-yy"),
    ("%b-%Y", "Mon-yyyy"),
]
_ISO_WITH_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    label: str
    type: ColumnType
    unit: str | None = None  # "INR" or "percent"
    date_format: str | None = None  # how text dates were read, for people ("dd/mm/yyyy")
    null_count: int = 0
    # Non-empty values that did not fit the column's type and were left empty.
    unparsed_count: int = 0
    unparsed_examples: tuple[str, ...] = ()
    currencies: tuple[str, ...] = ()
    # How an API source stores a money amount ("paise" for Razorpay), when the source says so itself.
    stored_in: str | None = None


@dataclass
class ParsedTable:
    frame: pd.DataFrame
    columns: list[ColumnInfo]
    header_row: int | None  # 1-based row number in the source, None when there was no header
    total_row_dropped: bool = False
    truncated: bool = False

    @property
    def row_count(self) -> int:
        return len(self.frame)

    def schema_json(self) -> dict[str, Any]:
        return {
            "columns": [
                {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(c).items()}
                for c in self.columns
            ],
            "header_row": self.header_row,
            "row_count": self.row_count,
            "total_row_dropped": self.total_row_dropped,
            "truncated": self.truncated,
        }


# --------------------------------------------------------------------------------------------- helpers
def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and np.isnan(value):
        return True
    return isinstance(value, str) and value.strip().lower() in NULL_TOKENS


def _cell_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _looks_like_value(value: Any) -> bool:
    """A number or date (typed, or as text): header cells are mostly not values."""
    if isinstance(value, bool | int | float | datetime | date | time):
        return True
    text = str(value).strip()
    if not text:
        return False
    cleaned = _CURRENCY.sub("", text).replace(",", "").replace("%", "").strip()
    if _PLAIN_NUMBER.match(cleaned):
        return True
    return bool(re.match(r"^\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}", text))


def snake_name(label: str) -> str:
    text = unicodedata.normalize("NFKC", label).replace("₹", " inr ").replace("%", " pct ")
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()
    if text and text[0].isdigit():
        text = f"c_{text}"
    return text[:_MAX_NAME_LENGTH].rstrip("_")


def unique_names(labels: Sequence[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for index, label in enumerate(labels, start=1):
        base = snake_name(label) or f"column_{index}"
        name, n = base, 2
        while name in seen:
            name = f"{base}_{n}"[:_MAX_NAME_LENGTH]
            n += 1
        seen.add(name)
        names.append(name)
    return names


# ---------------------------------------------------------------------------------------- header row
def find_header_row(rows: Sequence[Sequence[Any]]) -> int | None:
    """0-based index of the header row among ``rows``, or None if the data has no header."""
    top = rows[:HEADER_SCAN_ROWS]
    widths = [sum(not _is_blank(v) for v in row) for row in top]
    if not widths or max(widths) == 0:
        return None
    widest = max(widths)
    for index, row in enumerate(top):
        filled = [v for v in row if not _is_blank(v)]
        # Title rows ("UrbanCart sales report") are narrower than half the table.
        if not filled or (widest > 1 and len(filled) * 2 <= widest):
            continue
        texts = [v for v in filled if isinstance(v, str) and not _looks_like_value(v)]
        distinct = len({str(v).strip().lower() for v in filled}) == len(filled)
        if len(texts) / len(filled) >= 0.7 and distinct:
            return index
        # The first wide row is data, not names: there is no header.
        return None
    return None


# ------------------------------------------------------------------------------------------ inference
@dataclass
class _Parsed:
    values: pd.Series
    type: ColumnType
    unit: str | None = None
    date_format: str | None = None
    unparsed: pd.Series | None = None  # mask of non-empty values that did not fit
    currencies: tuple[str, ...] = ()


def _as_text(series: pd.Series) -> pd.Series:
    return series.map(lambda v: None if v is None else _cell_text(v)).astype("string")


def _parse_booleans(raw: pd.Series) -> _Parsed | None:
    def convert(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        return pd.NA

    converted = raw.map(convert)
    if converted.isna().any():
        return None
    return _Parsed(values=converted.astype("boolean"), type="boolean")


def _clean_number_text(text: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Strip currency, %, lakh/crore words and grouping. Returns (clean text, multiplier, had percent)."""
    s = text.str.strip()
    negative_parens = s.str.match(r"^\(.*\)$", na=False)
    s = s.where(~negative_parens, "-" + s.str.slice(1, -1))
    s = s.str.replace(_CURRENCY, "", regex=True).str.strip()
    s = s.str.replace(r"^-\s+", "-", regex=True)
    percent = s.str.endswith("%", na=False)
    s = s.str.rstrip("%").str.strip()
    words = s.str.extract(_MULTIPLIER, expand=False).str.lower()
    multiplier = words.map(lambda w: _MULTIPLIERS.get(str(w), 1.0) if isinstance(w, str) else 1.0)
    s = s.str.replace(_MULTIPLIER, "", regex=True).str.replace(" ", "", regex=False)
    grouped = s.str.match(_WESTERN_GROUPED, na=False) | s.str.match(_INDIAN_GROUPED, na=False)
    s = s.where(~grouped, s.str.replace(",", "", regex=False))
    return s, multiplier.astype(float), percent


def _parse_numbers(raw: pd.Series, name: str) -> _Parsed | None:
    typed = raw.map(lambda v: isinstance(v, int | float) and not isinstance(v, bool))
    text = _as_text(raw.where(~typed, None))
    if _IDENTIFIER_HINT.search(name):
        return None
    if text.str.match(_LEADING_ZERO, na=False).any():
        return None  # pincodes, codes: the leading zero is part of the value
    if text.str.match(r"^-?\d{16,}$", na=False).any():
        return None  # too long to be a quantity (card or account numbers)
    currencies = tuple(
        sorted({m.lower() for m in text.str.extract(_CURRENCY, expand=False).dropna().tolist()})
    )
    cleaned, multiplier, percent = _clean_number_text(text)
    valid = cleaned.str.match(_PLAIN_NUMBER, na=False)
    numbers = pd.to_numeric(cleaned.where(valid), errors="coerce") * multiplier
    typed_values = pd.to_numeric(raw.where(typed), errors="coerce")
    values = numbers.where(~typed, typed_values).astype(float)
    ok = values.notna().sum()
    if ok == 0 or ok / len(raw) < TYPE_THRESHOLD:
        return None
    unit = None
    if currencies and set(currencies) <= _RUPEE_MARKERS:
        unit = "INR"
    elif percent.any():
        unit = "percent"
    finite = values.dropna()
    is_integral = (
        bool((finite == finite.round()).all())
        and not cleaned.str.contains(r"\.\d", regex=True, na=False).any()
        and bool((finite.abs() < 2**53).all())
        and not (multiplier != 1.0).any()
    )
    out = values.round().astype("Int64") if is_integral else values
    return _Parsed(
        values=out,
        type="integer" if is_integral else "number",
        unit=unit,
        unparsed=values.isna(),
        currencies=currencies,
    )


def _to_local(parsed: pd.Series, timezone: str) -> pd.Series:
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert(timezone).dt.tz_localize(None)
    return parsed


def _best_text_format(sample: pd.Series) -> tuple[str, str] | None:
    """The format that reads most of the sample; day-first wins ties (Indian convention)."""
    best: tuple[int, tuple[str, str]] | None = None
    for fmt in (*_UNAMBIGUOUS, *_DAY_FIRST, *_MONTH_FIRST):
        count = int(pd.to_datetime(sample, format=fmt[0], errors="coerce").notna().sum())
        if count and (best is None or count > best[0]):
            best = (count, fmt)
    return best[1] if best else None


def _parse_dates(raw: pd.Series, name: str, timezone: str) -> _Parsed | None:
    typed = raw.map(lambda v: isinstance(v, datetime | date) and not isinstance(v, bool))
    if bool(typed.all()):
        values = pd.to_datetime(
            raw.map(lambda v: v if isinstance(v, datetime) else datetime.combine(v, time()))
        )
        return _Parsed(values=values, type=_date_or_datetime(values))

    # Unix timestamps in a column whose name says it is a time (e.g. Razorpay's created_at).
    numeric = pd.to_numeric(raw, errors="coerce")
    if _TIME_HINT.search(name) and numeric.notna().all():
        seconds = numeric.where(numeric < 1e11, numeric / 1000)  # milliseconds -> seconds
        low, high = _UNIX_SECONDS
        if bool(((seconds >= low) & (seconds < high)).all()):
            values = pd.to_datetime(seconds, unit="s", utc=True)
            local = _to_local(values, timezone)
            return _Parsed(values=local, type="datetime", date_format="unix time")

    text = _as_text(raw.where(~typed, None))
    nonblank = text.dropna()
    if nonblank.empty:
        return None
    if not nonblank.str.contains(r"\d", regex=True).all():
        return None
    sample = nonblank.head(_SAMPLE_FOR_FORMATS)
    label: str | None
    if sample.str.match(_ISO_WITH_TIME, na=False).mean() >= TYPE_THRESHOLD:
        parsed = pd.to_datetime(text, format="ISO8601", errors="coerce", utc=True)
        has_offset = sample.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).any()
        parsed = _to_local(parsed, timezone) if has_offset else parsed.dt.tz_localize(None)
        label = "ISO 8601"
    else:
        fmt = _best_text_format(sample)
        if fmt is None:
            return None
        parsed = pd.to_datetime(text, format=fmt[0], errors="coerce")
        label = fmt[1]
    if typed.any():
        parsed = parsed.where(~typed, pd.to_datetime(raw.where(typed), errors="coerce"))
    ok = parsed.notna().sum()
    if ok == 0 or ok / len(raw) < TYPE_THRESHOLD:
        return None
    return _Parsed(
        values=parsed,
        type=_date_or_datetime(parsed),
        date_format=label,
        unparsed=parsed.isna(),
    )


def _date_or_datetime(values: pd.Series) -> ColumnType:
    present = values.dropna()
    if present.empty:
        return "date"
    midnight = (present.dt.hour == 0) & (present.dt.minute == 0) & (present.dt.second == 0)
    return "date" if bool(midnight.all()) else "datetime"


def _header_unit(label: str) -> str | None:
    lowered = label.lower()
    if "₹" in label or re.search(r"\b(inr|rs\.?|rupees?)\b", lowered):
        return "INR"
    if "%" in label or "percent" in lowered:
        return "percent"
    return None


def infer_column(
    raw: pd.Series, *, name: str, label: str, timezone: str
) -> tuple[pd.Series, ColumnInfo]:
    blank = raw.map(_is_blank)
    present = raw[~blank]
    parsed: _Parsed | None = None
    if not present.empty:
        # A time-like name (created_at, order_date) tries dates first, so Unix timestamps become times.
        if _TIME_HINT.search(name):
            parsed = (
                _parse_booleans(present)
                or _parse_dates(present, name, timezone)
                or _parse_numbers(present, name)
            )
        else:
            parsed = (
                _parse_booleans(present)
                or _parse_numbers(present, name)
                or _parse_dates(present, name, timezone)
            )
    if parsed is None:
        values = _as_text(present)
        parsed = _Parsed(values=values, type="text")
    full = parsed.values.reindex(raw.index)
    if parsed.type == "text":
        full = full.astype("string")
    unparsed_mask = (
        parsed.unparsed if parsed.unparsed is not None else pd.Series(False, index=present.index)
    )
    examples = tuple(_cell_text(v)[:40] for v in present[unparsed_mask].head(3).tolist())
    unit = parsed.unit or (_header_unit(label) if parsed.type in ("integer", "number") else None)
    info = ColumnInfo(
        name=name,
        label=label,
        type=parsed.type,
        unit=unit,
        date_format=parsed.date_format,
        null_count=int(full.isna().sum()),
        unparsed_count=int(unparsed_mask.sum()),
        unparsed_examples=examples,
        currencies=parsed.currencies,
    )
    return full, info


# ---------------------------------------------------------------------------------------------- table
def parse_rows(
    rows: Iterable[Sequence[Any]], *, timezone: str = "Asia/Kolkata", max_rows: int = 1_000_000
) -> ParsedTable:
    """Rows of raw cells (text, numbers, dates, None) -> a typed table. Reads at most ``max_rows`` data rows."""
    iterator = iter(rows)
    head = [list(r) for r in itertools.islice(iterator, HEADER_SCAN_ROWS)]
    header_index = find_header_row(head)
    header: list[Any] = [] if header_index is None else head[header_index]
    first_rows = head if header_index is None else head[header_index + 1 :]

    body: list[list[Any]] = []
    truncated = False
    for row in itertools.chain(first_rows, iterator):
        if all(_is_blank(v) for v in row):
            continue
        if len(body) >= max_rows:
            truncated = True
            break
        body.append(list(row))

    total_dropped = False
    if body and not truncated:
        first = next((v for v in body[-1] if not _is_blank(v)), None)
        if isinstance(first, str) and _TOTAL_LABEL.match(first):
            body, total_dropped = body[:-1], True

    width = max(len(header), max((len(r) for r in body), default=0))
    labels = [
        _cell_text(header[i]) if i < len(header) and not _is_blank(header[i]) else ""
        for i in range(width)
    ]
    # Unnamed columns without a single value are layout, not data.
    keep = [
        i
        for i in range(width)
        if labels[i] or any(i < len(r) and not _is_blank(r[i]) for r in body)
    ]
    labels = [labels[i] or f"Column {n}" for n, i in enumerate(keep, start=1)]
    names = unique_names(labels)
    raw = pd.DataFrame(
        [[r[i] if i < len(r) else None for i in keep] for r in body], columns=names, dtype=object
    )

    columns: list[ColumnInfo] = []
    out: dict[str, pd.Series] = {}
    for name, label in zip(names, labels, strict=True):
        series, info = infer_column(raw[name], name=name, label=label, timezone=timezone)
        out[name] = series
        columns.append(info)
    frame = pd.DataFrame(out, index=raw.index).reset_index(drop=True)
    return ParsedTable(
        frame=frame,
        columns=columns,
        header_row=None if header_index is None else header_index + 1,
        total_row_dropped=total_dropped,
        truncated=truncated,
    )
