"""Test helpers for connections: small CSV and Excel files, uploads through the API, and connections made
directly in the database (fast set-up for tests about something else)."""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Sequence
from typing import Any

import openpyxl
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from app.connectors import service
from app.connectors.registry import SECRET_PURPOSES, ConnectorRuntime
from app.data.objects import file_key
from app.models import Connection, FileVersion
from tests.recorded_google import ACCOUNT, REFRESH_TOKEN, SHEET_ID
from tests.recorded_razorpay import KEY_ID, KEY_SECRET

SALES_CSV = (
    "UrbanCart outlet sales\n"
    "\n"
    "Date,Outlet,City,Bills,Sales (₹),Pincode\n"
    '05/01/2026,Indiranagar,Bengaluru,120,"₹1,20,500",560038\n'
    '13/01/2026,Andheri,Mumbai,98,"₹98,250.50",400053\n'
    "14/01/2026,Salt Lake,Kolkata,77,N/A,700091\n"
    'Total,,,295,"2,18,750.50",\n'
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def csv_bytes(text: str = SALES_CSV) -> bytes:
    return text.encode("utf-8")


def xlsx_bytes(sheets: dict[str, Sequence[Sequence[Any]]] | None = None) -> bytes:
    sheets = sheets or {
        "Targets": [["Month", "Region", "Revenue target (INR)"], ["Jul 2026", "South", 720000]],
        "Budget": [["Department", "Budget (₹)"], ["Marketing", "7,00,000"], ["Operations", 900000]],
    }
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)  # type: ignore[arg-type]
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def upload(
    client: TestClient,
    headers: dict[str, str],
    filename: str = "outlet_sales.csv",
    data: bytes | None = None,
    *,
    name: str | None = None,
    connection_id: str | None = None,
) -> Any:
    path = f"/connections/{connection_id}/files" if connection_id else "/connections/files"
    form = {"name": name} if name else {}
    return client.post(
        path,
        headers=headers,
        files={"file": (filename, data if data is not None else csv_bytes(), "text/csv")},
        data=form,
    )


def make_file_connection(
    db: DbSession,
    runtime: ConnectorRuntime,
    org_id: uuid.UUID,
    *,
    filename: str = "outlet_sales.csv",
    data: bytes | None = None,
    name: str | None = None,
    sync: bool = True,
) -> Connection:
    """A file connection with version 1 stored, read by the sync job (unless ``sync=False``)."""
    data = data if data is not None else csv_bytes()
    connection = Connection(
        org_id=org_id,
        type="file",
        name=name or filename.rsplit(".", 1)[0],
        status="active",
        config={},
        sync_state="idle",
    )
    db.add(connection)
    db.flush()
    version = FileVersion(
        org_id=org_id,
        connection_id=connection.id,
        storage_key="",
        original_name=filename,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        version=1,
        status="processing",
    )
    db.add(version)
    db.flush()
    version.storage_key = file_key(org_id, connection.id, version.id)
    runtime.objects.put(version.storage_key, data)
    db.commit()
    if sync:
        service.sync_connection(
            runtime, org_id=org_id, connection_id=connection.id, file_version_id=version.id
        )
        db.expire_all()
    return connection


def make_sheets_connection(
    db: DbSession,
    runtime: ConnectorRuntime,
    org_id: uuid.UUID,
    *,
    spreadsheet_ids: Sequence[str] = (SHEET_ID,),
    refresh_token: str = REFRESH_TOKEN,
    spreadsheet_name: str = "UrbanCart targets 2026",
    sync: bool = True,
) -> Connection:
    secret = runtime.secrets.put(
        db, org_id=org_id, purpose=SECRET_PURPOSES["google_sheets"], value=refresh_token
    )
    connection = Connection(
        org_id=org_id,
        type="google_sheets",
        name=f"Google Sheets ({ACCOUNT})",
        status="active",
        config={
            "account_email": ACCOUNT,
            "spreadsheets": [{"id": s, "name": spreadsheet_name} for s in spreadsheet_ids],
        },
        secret_ref=secret.id,
        sync_state="idle",
    )
    db.add(connection)
    db.commit()
    if sync:
        service.sync_connection(runtime, org_id=org_id, connection_id=connection.id)
        db.expire_all()
    return connection


def make_razorpay_connection(
    db: DbSession,
    runtime: ConnectorRuntime,
    org_id: uuid.UUID,
    *,
    key_id: str = KEY_ID,
    key_secret: str = KEY_SECRET,
    name: str = "Razorpay",
    sync: bool = True,
) -> Connection:
    secret = runtime.secrets.put(
        db, org_id=org_id, purpose=SECRET_PURPOSES["razorpay"], value=key_secret
    )
    connection = Connection(
        org_id=org_id,
        type="razorpay",
        name=name,
        status="active",
        config={"key_id": key_id, "mode": "live" if key_id.startswith("rzp_live_") else "test"},
        secret_ref=secret.id,
        sync_state="idle",
    )
    db.add(connection)
    db.commit()
    if sync:
        service.sync_connection(runtime, org_id=org_id, connection_id=connection.id)
        db.expire_all()
    return connection
