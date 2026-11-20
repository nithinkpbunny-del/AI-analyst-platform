"""Tally bridge security (M16; BUILD_SPEC 6.3, 8.8 "never sends import/alter requests (tested)").

* The bridge's guard refuses every request that is not a plain export, and a refused request never reaches Tally.
* The platform has no way to send anything to Tally: no code in ``app/`` builds a Tally request, and the only
  answer the bridge reads from the platform is "read again" (+ settings it clamps).
* Rows the bridge sends land only in its own connection's workspace (RLS), and another workspace never sees them.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker
from tally_bridge.dev.stand_in import StandInTally, StandInTallyTransport, build_company
from tally_bridge.platform_client import Settings
from tally_bridge.tally_client import TallyClient
from tally_bridge.tally_xml import SPECS, ReadOnlyViolation, build_export, check_export_only

from app.config import Settings as AppSettings
from app.db.tenant import scope_to_org
from app.models import TallyDevice, TallyRecord, TallySync
from app.schemas.tally import PollOut
from tests.conftest import FakeClock
from tests.tallybridge import pair
from tests.tenancy import build_world

APP = Path(__file__).resolve().parents[2] / "app"

IMPORT_VOUCHER = b"""<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>
<REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA><TALLYMESSAGE>
<VOUCHER VCHTYPE="Payment" ACTION="Create"><DATE>20260701</DATE></VOUCHER></TALLYMESSAGE></REQUESTDATA>
</IMPORTDATA></BODY></ENVELOPE>"""
ALTER_LEDGER = b"""<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST><TYPE>Data</TYPE>
<ID>All Masters</ID></HEADER><BODY><DESC></DESC><DATA><TALLYMESSAGE><LEDGER NAME="Cash" ACTION="Alter">
<PARENT>Bank Accounts</PARENT></LEDGER></TALLYMESSAGE></DATA></BODY></ENVELOPE>"""


REFUSED = {
    "import data": IMPORT_VOUCHER,
    "alter a ledger": ALTER_LEDGER,
    "export with a TALLYMESSAGE": build_export(SPECS["ledgers"]).replace(
        b"</TDLMESSAGE>",
        b'</TDLMESSAGE><TALLYMESSAGE><LEDGER NAME="X" ACTION="Delete"/></TALLYMESSAGE>',
    ),
    "a collection that modifies": build_export(SPECS["ledgers"]).replace(
        b'ISMODIFY="No"', b'ISMODIFY="Yes"'
    ),
    "a TDL function": build_export(SPECS["ledgers"]).replace(
        b"</TDLMESSAGE>",
        b'<FUNCTION NAME="X"><ACTION>New Object: Ledger</ACTION></FUNCTION></TDLMESSAGE>',
    ),
    "an ACTION attribute": build_export(SPECS["ledgers"]).replace(
        b"<COLLECTION ", b'<COLLECTION ACTION="Create" '
    ),
    "a formula calling a function": build_export(SPECS["vouchers"], alter_id_above=5).replace(
        b"$AlterID &gt; 5", b"$$CallSomething"
    ),
    "a compute that is an expression": build_export(SPECS["ledgers"]).replace(
        b"$_PrimaryGroup", b"$$SetValue:$Name"
    ),
    "a report export": build_export(SPECS["ledgers"]).replace(
        b"<TYPE>Collection</TYPE>", b"<TYPE>Data</TYPE>"
    ),
    "another export format": build_export(SPECS["ledgers"]).replace(
        b"$$SysName:XML", b"$$SysName:ASCII"
    ),
    "an entity": b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "Import">]><ENVELOPE><HEADER>'
    b"<TALLYREQUEST>&a;</TALLYREQUEST></HEADER></ENVELOPE>",
    "two collections": build_export(SPECS["ledgers"]).replace(
        b"</TDLMESSAGE>",
        b'<COLLECTION NAME="B" ISMODIFY="No"><TYPE>Ledger</TYPE></COLLECTION></TDLMESSAGE>',
    ),
    "too large": build_export(SPECS["ledgers"]).replace(
        b"<VERSION>1", b"<VERSION>" + b"1" * 70_000
    ),
    "not XML": b"Import",
}


@pytest.mark.parametrize("body", REFUSED.values(), ids=list(REFUSED))
def test_the_guard_refuses_anything_that_is_not_an_export(body: bytes) -> None:
    with pytest.raises(ReadOnlyViolation):
        check_export_only(body)


@pytest.mark.parametrize("name", list(SPECS))
def test_every_request_the_bridge_builds_passes(name: str) -> None:
    from datetime import date

    check_export_only(build_export(SPECS[name]))
    check_export_only(
        build_export(
            SPECS[name],
            company='A & B "Traders" <Pune>',
            from_date=date(2026, 4, 1),
            to_date=date(2026, 11, 20),
            alter_id_above=123,
        )
    )


def test_a_refused_request_never_reaches_tally(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.read())
        return httpx.Response(200, content=b"<ENVELOPE/>")

    client = TallyClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("tally_bridge.tally_client.build_export", lambda *a, **k: IMPORT_VOUCHER)
    with pytest.raises(ReadOnlyViolation):
        client.export(SPECS["vouchers"])
    assert sent == []


def test_a_whole_sync_sends_tally_exports_only(tmp_path: Path) -> None:
    tally = StandInTally(build_company(None))
    client = TallyClient(transport=StandInTallyTransport(tally))
    for name, spec in SPECS.items():
        assert client.export(spec) is not None, name
    assert tally.refused == []
    assert all(b"<TALLYREQUEST>Export</TALLYREQUEST>" in r for r in tally.requests)


def test_the_stand_in_would_notice_a_change_request() -> None:
    tally = StandInTally(build_company(None))
    tally.handle(IMPORT_VOUCHER)
    assert len(tally.refused) == 1


def test_no_platform_code_builds_a_tally_request() -> None:
    """The platform never talks to Tally: no module of the app contains Tally's request vocabulary."""
    pattern = re.compile(r"TALLYREQUEST|TALLYMESSAGE|IMPORTDATA|SVCURRENTCOMPANY|TDLMESSAGE")
    offenders = [
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_platform_can_only_ask_to_read_again() -> None:
    assert set(PollOut.model_fields) == {"refresh", "full", "settings"}
    # Settings from the platform are clamped by the bridge: nothing it sends can make it misbehave.
    clamped = Settings.from_json(
        {
            "interval_minutes": 0,
            "max_batch_rows": 10**9,
            "history_months": -5,
            "poll_wait_seconds": "x",
        }
    )
    assert clamped.interval_minutes == 5
    assert clamped.max_batch_rows == 10_000
    assert clamped.history_months == 1
    assert clamped.poll_wait_seconds == 25


def test_rows_stay_in_the_devices_workspace(
    session_factory: sessionmaker[DbSession],
    client: TestClient,
    db: DbSession,
    settings: AppSettings,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    world = build_world(db, settings, clock.now)
    paired = pair(client, world, tmp_path / "a")
    paired.engine().sync()
    with session_factory() as other:
        scope_to_org(other, world.org_b.id)
        for model in (TallyRecord, TallyDevice, TallySync):
            assert other.scalars(select(model)).all() == [], model.__tablename__
    with session_factory() as mine:
        scope_to_org(mine, world.org_a.id)
        assert mine.scalars(select(TallyRecord)).first() is not None
