"""PDF export (BUILD_SPEC X3, 3: WeasyPrint, HTML to PDF).

The page follows the design system in print: indigo ink on white, ledger rules, one haldi mark behind the key
figure, numbered source marks after figures and a Sources list with tables, connections, fetch times and queries.
WeasyPrint is given no base URL and a fetcher that refuses everything, so a document can never load a file or a
web address (answers contain text the model wrote).
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from app.exports import charts
from app.exports.document import Document

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _no_fetch(url: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise ValueError(f"exports never load resources ({url[:40]})")


def cited_html(document: Document, text: str, key_figure: str | None = None) -> Markup:
    """Escaped text with ``<sup>`` source marks and, once, the haldi mark behind the key figure."""
    out = []
    marked = False
    for piece, numbers in document.cite(text):
        html = escape(piece)
        if key_figure and not marked and escape(key_figure) in html:
            html = html.replace(
                escape(key_figure), f'<span class="mark">{escape(key_figure)}</span>', 1
            )
            marked = True
        out.append(html)
        if numbers:
            out.append(f"<sup>{','.join(str(n) for n in numbers)}</sup>")
    return Markup("".join(out))  # noqa: S704 - every piece above is escaped


def html(document: Document) -> str:
    template = _env.get_template("report.html")
    return template.render(
        doc=document,
        cited=lambda text, key=None: cited_html(document, text, key),
        charts=[
            {
                "title": c.get("title") or "",
                "svg": Markup(charts.svg(c)),  # noqa: S704 - built with escape() for every text
                "summary": charts.summary(c),
            }
            for c in document.charts
        ],
    )


def render(document: Document) -> bytes:
    from weasyprint import HTML

    pdf: bytes = HTML(string=html(document), url_fetcher=_no_fetch).write_pdf()
    return pdf
