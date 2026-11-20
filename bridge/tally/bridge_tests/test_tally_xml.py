"""Building export requests and reading Tally's answers (``tally_bridge.tally_xml``)."""

from __future__ import annotations

from datetime import date

import pytest
from tally_bridge.tally_xml import (
    LEDGERS,
    SPECS,
    VOUCHERS,
    TallyError,
    build_export,
    check_export_only,
    decode,
    parse_objects,
    to_record,
)


def test_requests_escape_the_company_and_format_dates() -> None:
    body = build_export(
        LEDGERS,
        company="Shah & Sons <Pune>",
        from_date=date(2026, 4, 1),
        to_date=date(2026, 11, 20),
    ).decode()
    assert "<SVCURRENTCOMPANY>Shah &amp; Sons &lt;Pune&gt;</SVCURRENTCOMPANY>" in body
    assert "<SVFROMDATE>20260401</SVFROMDATE>" in body
    assert 'ISMODIFY="No"' in body
    check_export_only(body.encode())


def test_the_alter_filter_is_a_number_only() -> None:
    body = build_export(VOUCHERS, alter_id_above=42).decode()
    assert "$AlterID &gt; 42" in body
    check_export_only(body.encode())


def test_utf16_bom_and_forbidden_characters_are_handled() -> None:
    xml = (
        '<ENVELOPE><BODY><DATA><COLLECTION><LEDGER NAME="Cash&#4;"><GUID>g1</GUID></LEDGER>'
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )
    for raw in (xml.encode("utf-16"), xml.encode("utf-16-le"), ("﻿" + xml).encode("utf-8")):
        assert decode(raw).startswith("<ENVELOPE>")
        items = parse_objects(raw, LEDGERS)
        assert items == [{"NAME": "Cash", "GUID": "g1"}]


def test_only_asked_fields_are_kept_and_name_lists_are_read() -> None:
    raw = (
        b"<ENVELOPE><BODY><DATA><COLLECTION><LEDGER><GUID>g</GUID><LEDGERPHONE>98450</LEDGERPHONE>"
        b'<NAME.LIST TYPE="String"><NAME>Cash</NAME></NAME.LIST><EMAIL>a@b.in</EMAIL></LEDGER>'
        b"</COLLECTION></DATA></BODY></ENVELOPE>"
    )
    assert parse_objects(raw, LEDGERS) == [{"GUID": "g", "NAME": "Cash"}]


def test_vouchers_carry_their_lines_and_allocations() -> None:
    raw = (
        b'<ENVELOPE><BODY><DATA><COLLECTION><VOUCHER ACTION="Create"><GUID>v</GUID><DATE>20260701</DATE>'
        b"<NARRATION>private</NARRATION>"
        b"<LEDGERENTRIES.LIST><LEDGERNAME>Asha</LEDGERNAME><AMOUNT>-10.00</AMOUNT></LEDGERENTRIES.LIST>"
        b"<INVENTORYENTRIES.LIST><STOCKITEMNAME>Kurta</STOCKITEMNAME><AMOUNT>10.00</AMOUNT>"
        b"<ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>Sales</LEDGERNAME><AMOUNT>10.00</AMOUNT>"
        b"</ACCOUNTINGALLOCATIONS.LIST></INVENTORYENTRIES.LIST></VOUCHER></COLLECTION></DATA></BODY></ENVELOPE>"
    )
    record = to_record(parse_objects(raw, VOUCHERS)[0])
    assert record is not None
    assert record.key == "v"
    assert "NARRATION" not in record.fields
    assert [e["LEDGERNAME"] for e in record.ledger_entries] == ["Asha", "Sales"]
    assert record.inventory_entries == [{"STOCKITEMNAME": "Kurta", "AMOUNT": "10.00"}]


def test_objects_without_guid_or_name_are_skipped() -> None:
    assert to_record({"PARENT": "x"}) is None
    named = to_record({"NAME": "Cash"})
    assert named is not None and named.key == "Cash"


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (b"<RESPONSE>Unknown Request, cannot be processed</RESPONSE>", "unknown_request"),
        (b"<ENVELOPE><LINEERROR>Could not find Company</LINEERROR></ENVELOPE>", "tally_error"),
        (b"", "empty_answer"),
        (b"<ENVELOPE><unclosed>", "unreadable_answer"),
    ],
)
def test_errors(answer: bytes, code: str) -> None:
    with pytest.raises(TallyError) as caught:
        parse_objects(answer, SPECS["ledgers"])
    assert caught.value.code == code
