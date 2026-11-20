"""Talking to TallyPrime's XML server on this PC (default ``http://127.0.0.1:9000``).

The client has one public way to send anything: ``export``. It builds the request from a fixed template
(``tally_xml.build_export``) and every request passes ``check_export_only`` right before it is sent, so nothing the
bridge does (or is asked to do) can change data in Tally. Tally's XML server needs ``POST`` even for exports;
that is the only method used.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import httpx

from tally_bridge.tally_xml import (
    COMPANIES,
    CollectionSpec,
    Record,
    TallyError,
    build_export,
    check_export_only,
    parse_objects,
    to_record,
)

DEFAULT_URL = "http://127.0.0.1:9000"
# Exports of a busy month can take Tally a while; the bridge reads month by month to keep each one small.
TIMEOUT_SECONDS = 180.0
MAX_ANSWER_BYTES = 256 * 1024 * 1024


class TallyUnavailable(Exception):
    """Tally is not open on this PC, or its XML server (Connectivity > Client/Server) is off."""


@dataclass(frozen=True)
class Company:
    name: str
    guid: str
    books_from: str
    starting_from: str


def check_tally_url(url: str) -> str:
    """Tally runs on this PC or another PC on the same office network: plain http to a local address only."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        raise ValueError("The Tally address must look like http://127.0.0.1:9000")
    host = parts.hostname
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise ValueError("Use the Tally PC's IP address, for example 192.168.1.20") from error
        if not (address.is_loopback or address.is_private) or address.is_unspecified:
            raise ValueError("Tally must be on this PC or on the office network")
    return f"http://{parts.netloc}"


class TallyClient:
    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = check_tally_url(url)
        self._http = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
        )

    @property
    def url(self) -> str:
        return self._url

    def close(self) -> None:
        self._http.close()

    def _post(self, body: bytes) -> bytes:
        check_export_only(
            body
        )  # the last thing before the request leaves (P2, "never write to Tally")
        try:
            with self._http.stream(
                "POST",
                self._url,
                content=body,
                headers={"Content-Type": "text/xml; charset=utf-8"},
            ) as response:
                if response.status_code != 200:
                    raise TallyError(
                        "http_error", f"Tally answered with HTTP {response.status_code}."
                    )
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_ANSWER_BYTES:
                        raise TallyError("too_large", "Tally's answer was too large to read.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TransportError as error:
            raise TallyUnavailable(type(error).__name__) from error

    def export(
        self,
        spec: CollectionSpec,
        *,
        company: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        alter_id_above: int | None = None,
    ) -> list[Record]:
        body = build_export(
            spec,
            company=company,
            from_date=from_date,
            to_date=to_date,
            alter_id_above=alter_id_above,
        )
        items = parse_objects(self._post(body), spec)
        return [record for item in items if (record := to_record(item)) is not None]

    def companies(self) -> list[Company]:
        """The companies open in Tally (the first one is the one Tally shows)."""
        items = parse_objects(self._post(build_export(COMPANIES)), COMPANIES)
        return [
            Company(
                name=str(item.get("NAME", "")),
                guid=str(item.get("GUID", "")),
                books_from=str(item.get("BOOKSFROM", "")),
                starting_from=str(item.get("STARTINGFROM", "")),
            )
            for item in items
            if item.get("NAME")
        ]
