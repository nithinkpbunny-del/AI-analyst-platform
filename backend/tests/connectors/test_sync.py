"""Reading a whole connection in the background (``app.connectors.service``) and removing one."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.connectors import service
from app.connectors.registry import ConnectorRuntime
from app.data.objects import MemoryObjectStore, file_key
from app.models import Connection, Dataset, FileVersion, Organization, Secret, Snapshot
from app.tasks.queue import InlineJobQueue, RecordingJobQueue
from tests.datasources import make_file_connection, make_sheets_connection, xlsx_bytes
from tests.recorded_google import RecordedGoogle
from tests.tenancy import make_org


@pytest.fixture
def org(db: DbSession) -> Organization:
    return make_org(db, "UrbanCart", "asha@urbancart.in")


def add_version(
    db: DbSession, runtime: ConnectorRuntime, connection: Connection, data: bytes, name: str
) -> FileVersion:
    number = (
        len(db.scalars(select(FileVersion).where(FileVersion.connection_id == connection.id)).all())
        + 1
    )
    version = FileVersion(
        org_id=connection.org_id,
        connection_id=connection.id,
        storage_key="",
        original_name=name,
        sha256="0" * 64,
        size_bytes=len(data),
        version=number,
        status="processing",
    )
    db.add(version)
    db.flush()
    version.storage_key = file_key(connection.org_id, connection.id, version.id)
    runtime.objects.put(version.storage_key, data)
    db.commit()
    return version


def names(db: DbSession, connection: Connection) -> list[str]:
    db.expire_all()
    return [
        d.name
        for d in db.scalars(
            select(Dataset).where(Dataset.connection_id == connection.id).order_by(Dataset.position)
        )
    ]


def test_a_new_sheet_with_the_same_structure_is_used_at_once(
    db: DbSession, runtime: ConnectorRuntime, org: Organization
) -> None:
    connection = make_file_connection(db, runtime, org.id, filename="plan.xlsx", data=xlsx_bytes())
    assert names(db, connection) == ["Targets", "Budget"]
    kept = db.scalars(select(Dataset).where(Dataset.name == "Targets")).one().id
    # Same sheets and columns, new rows, plus a new sheet: adding is safe.
    newer = xlsx_bytes(
        {
            "Targets": [["Month", "Region", "Revenue target (INR)"], ["Aug 2026", "South", 750000]],
            "Budget": [["Department", "Budget (₹)"], ["Marketing", "7,50,000"]],
            "Stock": [["Item", "Qty"], ["Tea", 4]],
        }
    )
    version = add_version(db, runtime, connection, newer, "plan v2.xlsx")
    outcome = service.sync_connection(
        runtime, org_id=org.id, connection_id=connection.id, file_version_id=version.id
    )
    assert (outcome.status, outcome.datasets) == ("active", 3)
    assert names(db, connection) == ["Targets", "Budget", "Stock"]
    assert db.scalars(select(Dataset).where(Dataset.name == "Targets")).one().id == kept
    db.refresh(version)
    db.refresh(connection)
    assert version.status == "ready"
    assert connection.config["file_version_id"] == str(version.id)


def test_a_removed_sheet_waits_for_confirmation_then_goes(
    db: DbSession, runtime: ConnectorRuntime, org: Organization
) -> None:
    connection = make_file_connection(db, runtime, org.id, filename="plan.xlsx", data=xlsx_bytes())
    first = db.scalars(select(FileVersion)).one()
    newer = xlsx_bytes(
        {"Targets": [["Month", "Region", "Revenue target (INR)"], ["Aug 2026", "South", 750000]]}
    )
    version = add_version(db, runtime, connection, newer, "plan v2.xlsx")
    outcome = service.sync_connection(
        runtime, org_id=org.id, connection_id=connection.id, file_version_id=version.id
    )
    assert outcome.status == "needs_confirmation"
    db.refresh(version)
    db.refresh(connection)
    assert version.status == "needs_confirmation"
    assert version.review_json == [{"kind": "sheet_missing", "sheet": "Budget"}]
    assert connection.config["file_version_id"] == str(first.id)  # still in use
    assert names(db, connection) == ["Targets", "Budget"]
    # The Owner chooses it.
    outcome = service.sync_connection(
        runtime,
        org_id=org.id,
        connection_id=connection.id,
        file_version_id=version.id,
        accept_changes=True,
    )
    assert outcome.status == "active"
    assert names(db, connection) == ["Targets"]
    assert len(db.scalars(select(Snapshot)).all()) == 1  # Budget's snapshot went with it
    db.refresh(version)
    assert (version.status, version.review_json) == ("ready", [])


def test_a_first_file_that_cannot_be_read_marks_the_connection(
    db: DbSession, runtime: ConnectorRuntime, org: Organization
) -> None:
    connection = make_file_connection(
        db, runtime, org.id, filename="x.xlsx", data=b"PK\x03\x04broken"
    )
    db.refresh(connection)
    assert connection.status == "error"
    assert connection.last_error == "This workbook is damaged and cannot be opened."
    assert connection.sync_state == "idle"
    version = db.scalars(select(FileVersion)).one()
    assert (version.status, version.error) == ("failed", connection.last_error)


def test_a_bad_new_version_keeps_the_good_one(
    db: DbSession, runtime: ConnectorRuntime, org: Organization
) -> None:
    connection = make_file_connection(db, runtime, org.id)
    bad = add_version(db, runtime, connection, b"\x00\x01binary", "v2.csv")
    outcome = service.sync_connection(
        runtime, org_id=org.id, connection_id=connection.id, file_version_id=bad.id
    )
    db.refresh(connection)
    db.refresh(bad)
    assert connection.status == "active" and outcome.status == "active"
    assert bad.status == "failed"
    assert names(db, connection) == ["outlet_sales"]


def test_a_paused_connection_is_not_read(
    db: DbSession, runtime: ConnectorRuntime, org: Organization
) -> None:
    connection = make_file_connection(db, runtime, org.id, sync=False)
    connection.status = "disabled"
    db.commit()
    assert (
        service.sync_connection(runtime, org_id=org.id, connection_id=connection.id).status
        == "disabled"
    )


def test_another_workspaces_connection_is_left_alone(
    db: DbSession, runtime: ConnectorRuntime, org: Organization
) -> None:
    connection = make_file_connection(db, runtime, org.id, sync=False)
    other = make_org(db, "Chaiwala", "kabir@chaiwala.in")
    assert (
        service.sync_connection(runtime, org_id=other.id, connection_id=connection.id).status
        == "missing"
    )


def test_google_sheets_sync(db: DbSession, runtime: ConnectorRuntime, org: Organization) -> None:
    connection = make_sheets_connection(db, runtime, org.id)
    db.refresh(connection)
    assert connection.status == "active"
    assert connection.last_success_at is not None
    assert names(db, connection) == [
        "UrbanCart targets 2026 · Targets",
        "UrbanCart targets 2026 · Notes",
    ]


def test_a_crash_never_leaves_the_connection_reading(
    db: DbSession, runtime: ConnectorRuntime, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = make_file_connection(db, runtime, org.id, sync=False)

    def boom(*_: object, **__: object) -> object:
        raise RuntimeError("bug")

    version = db.scalars(select(FileVersion)).one()
    monkeypatch.setattr(service, "_set_datasets", boom)
    with pytest.raises(RuntimeError):
        InlineJobQueue(runtime).sync_connection(
            org_id=org.id, connection_id=connection.id, file_version_id=version.id
        )
    db.refresh(connection)
    assert (connection.sync_state, connection.status) == ("idle", "error")
    assert connection.last_error == service.UNEXPECTED_FAILURE
    db.refresh(version)
    assert version.status == "failed"


def test_the_recording_queue_sends_ids_as_text() -> None:
    import uuid

    queue = RecordingJobQueue()
    org_id, connection_id = uuid.uuid4(), uuid.uuid4()
    queue.sync_connection(org_id=org_id, connection_id=connection_id, force=True)
    assert queue.jobs == [
        (
            "app.tasks.jobs.sync_connection",
            {
                "org_id": str(org_id),
                "connection_id": str(connection_id),
                "force": True,
                "file_version_id": None,
                "accept_changes": False,
            },
        )
    ]


def test_removing_a_sheets_connection_revokes_and_deletes_everything(
    db: DbSession,
    runtime: ConnectorRuntime,
    org: Organization,
    object_store: MemoryObjectStore,
    google_transport: RecordedGoogle,
) -> None:
    connection = make_sheets_connection(db, runtime, org.id)
    assert object_store.stored_keys()
    service.remove_connection(db, runtime, connection)
    db.commit()
    assert object_store.stored_keys() == []
    assert db.scalars(select(Secret)).all() == []
    assert db.scalars(select(Dataset)).all() == []
    revoked = [c for c in google_transport.calls if c.url.endswith("/revoke")]
    assert [c.form for c in revoked] == [{"token": "1//test-refresh-token"}]
