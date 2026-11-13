"""Charts for PDFs as plain SVG (WeasyPrint draws SVG; the web app's ECharts cannot run here).

The same chart spec the Ask screen draws (``make_chart``): ``type`` line / bar / horizontal_bar / stacked_bar,
``x.values``, ``series[{name, data}]``, ``unit``. Colours follow the fixed chart order of the design tokens
(BUILD_SPEC 11a.2); every chart also gets a one-line text summary, so colour is never the only carrier.
"""

from __future__ import annotations

from html import escape
from typing import Any

# design/tokens.json chart series, light theme (PDFs are printed on white).
SERIES = ("#3A36A8", "#11795A", "#D08A00", "#8C4FBF", "#2A8FB5", "#C0392B")
INK, MUTED, LINE = "#1E1C3A", "#5A5874", "#E2E4EE"
WIDTH, HEIGHT = 640, 260
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 64, 16, 16, 48
MAX_POINTS = 60


def format_inr(value: float) -> str:
    """₹8,450 / ₹12.4 L / ₹1.24 Cr (Indian style, BUILD_SPEC 11)."""
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1e7:
        return f"{sign}₹{amount / 1e7:.2f} Cr"
    if amount >= 1e5:
        return f"{sign}₹{amount / 1e5:.1f} L"
    return f"{sign}₹{indian_grouping(round(amount))}"


def indian_grouping(number: int) -> str:
    text = str(abs(int(number)))
    if len(text) <= 3:
        return text
    head, tail = text[:-3], text[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail])


def format_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return "–"  # noqa: RUF001
    if unit == "INR":
        return format_inr(value)
    if unit in ("pct", "percent", "%"):
        return f"{value:.1f}%"
    if abs(value) >= 1000:
        return indian_grouping(round(value))
    return f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))


def _numbers(values: list[Any]) -> list[float | None]:
    out: list[float | None] = []
    for v in values:
        try:
            out.append(None if v is None else float(v))
        except (TypeError, ValueError):
            out.append(None)
    return out


def summary(spec: dict[str, Any]) -> str:
    """A text version of the chart (accessibility, BUILD_SPEC 11a.9)."""
    xs = [str(x) for x in (spec.get("x") or {}).get("values") or []]
    unit = spec.get("unit")
    parts = []
    for series in (spec.get("series") or [])[:6]:
        data = _numbers(list(series.get("data") or []))
        known = [(x, v) for x, v in zip(xs, data, strict=False) if v is not None]
        if not known:
            continue
        first, last = known[0], known[-1]
        parts.append(
            f"{series.get('name')}: {format_value(first[1], unit)} ({first[0]}) to "
            f"{format_value(last[1], unit)} ({last[0]})"
        )
    return "; ".join(parts)


def svg(spec: dict[str, Any]) -> str:
    kind = spec.get("type") or "bar"
    xs = [str(x) for x in (spec.get("x") or {}).get("values") or []][:MAX_POINTS]
    series = [
        (str(s.get("name") or ""), _numbers(list(s.get("data") or []))[: len(xs)])
        for s in (spec.get("series") or [])[: len(SERIES)]
    ]
    if not xs or not series:
        return ""
    if kind == "horizontal_bar":
        return _horizontal(xs, series, spec.get("unit"))
    stacked = kind == "stacked_bar"
    tops = (
        [sum(max(0.0, s[1][i] or 0.0) for s in series) for i in range(len(xs))]
        if stacked
        else [v or 0.0 for _, data in series for v in data]
    )
    lows = [0.0] + [v or 0.0 for _, data in series for v in data]
    high, low = max([*tops, 0.0]), min(lows)
    if high == low:
        high = low + 1
    plot_w, plot_h = WIDTH - PAD_LEFT - PAD_RIGHT, HEIGHT - PAD_TOP - PAD_BOTTOM

    def y(v: float) -> float:
        return float(PAD_TOP + plot_h - (v - low) / (high - low) * plot_h)

    step = plot_w / len(xs)
    out = [_open()]
    for i in range(5):
        value = low + (high - low) * i / 4
        out.append(
            f'<line x1="{PAD_LEFT}" x2="{WIDTH - PAD_RIGHT}" y1="{y(value):.1f}" y2="{y(value):.1f}" '
            f'stroke="{LINE}" stroke-width="1"/>'
            f'<text x="{PAD_LEFT - 6}" y="{y(value) + 4:.1f}" text-anchor="end" font-size="10" '
            f'fill="{MUTED}">{escape(format_value(value, spec.get("unit")))}</text>'
        )
    label_every = max(1, len(xs) // 8)
    for i, label in enumerate(xs):
        if i % label_every == 0:
            out.append(
                f'<text x="{PAD_LEFT + step * (i + 0.5):.1f}" y="{HEIGHT - PAD_BOTTOM + 16}" '
                f'text-anchor="middle" font-size="10" fill="{MUTED}">{escape(label[:12])}</text>'
            )
    if kind == "line":
        for n, (_, data) in enumerate(series):
            points = " ".join(
                f"{PAD_LEFT + step * (i + 0.5):.1f},{y(v):.1f}"
                for i, v in enumerate(data)
                if v is not None
            )
            out.append(
                f'<polyline points="{points}" fill="none" stroke="{SERIES[n]}" stroke-width="2"/>'
            )
    else:
        width = step * 0.7 / (1 if stacked else len(series))
        bases = [0.0] * len(xs)
        for n, (_, data) in enumerate(series):
            for i, v in enumerate(data):
                if v is None:
                    continue
                if stacked:
                    x0 = PAD_LEFT + step * i + step * 0.15
                    top, bottom = y(bases[i] + max(v, 0.0)), y(bases[i])
                    bases[i] += max(v, 0.0)
                else:
                    x0 = PAD_LEFT + step * i + step * 0.15 + width * n
                    top, bottom = min(y(v), y(0.0)), max(y(v), y(0.0))
                out.append(
                    f'<rect x="{x0:.1f}" y="{top:.1f}" width="{width:.1f}" '
                    f'height="{max(0.5, bottom - top):.1f}" fill="{SERIES[n]}"/>'
                )
    out.append(_legend(series))
    out.append("</svg>")
    return "".join(out)


def _open(height: int = HEIGHT) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="100%" '
        f'role="img" font-family="Noto Sans, Arial, sans-serif">'
    )


def _legend(series: list[tuple[str, list[float | None]]], height: int = HEIGHT) -> str:
    if len(series) < 2:
        return ""
    parts = []
    x = PAD_LEFT
    for n, (name, _) in enumerate(series):
        parts.append(
            f'<rect x="{x}" y="{height - 16}" width="10" height="10" fill="{SERIES[n]}"/>'
            f'<text x="{x + 14}" y="{height - 7}" font-size="10" fill="{INK}">{escape(name[:24])}</text>'
        )
        x += 24 + 7 * min(24, len(name))
    return "".join(parts)


def _horizontal(
    xs: list[str], series: list[tuple[str, list[float | None]]], unit: str | None
) -> str:
    rows = xs[:20]
    height = PAD_TOP + 24 * len(rows) + 32
    values = [v or 0.0 for v in series[0][1][: len(rows)]]
    high = max([abs(v) for v in values] + [1.0])
    left, plot_w = 150, WIDTH - 150 - 90
    out = [_open(height)]
    for i, (label, value) in enumerate(zip(rows, values, strict=False)):
        top = PAD_TOP + 24 * i
        out.append(
            f'<text x="{left - 8}" y="{top + 14}" text-anchor="end" font-size="11" fill="{INK}">'
            f"{escape(label[:22])}</text>"
            f'<rect x="{left}" y="{top + 3}" width="{max(0.5, abs(value) / high * plot_w):.1f}" height="16" '
            f'fill="{SERIES[0]}"/>'
            f'<text x="{left + abs(value) / high * plot_w + 6:.1f}" y="{top + 15}" font-size="10" '
            f'fill="{MUTED}">{escape(format_value(value, unit))}</text>'
        )
    out.append("</svg>")
    return "".join(out)
