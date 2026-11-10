"""Object storage wrapper over S3 (checked with botocore's Stubber: no network)."""

from __future__ import annotations

import io
import uuid

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.config import Settings
from app.data.objects import (
    MemoryObjectStore,
    ObjectNotFound,
    S3ObjectStore,
    file_key,
    snapshot_key,
)


def test_keys_are_per_workspace_and_never_use_file_names() -> None:
    org, conn, version = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert file_key(org, conn, version) == f"orgs/{org}/files/{conn}/{version}"
    assert snapshot_key(org, conn, version).endswith(f"{version}.parquet")


def test_s3_store(settings: Settings) -> None:
    store = S3ObjectStore(settings)
    client = store._s3()
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {"Bucket": settings.s3_bucket, "Key": "k", "Body": b"data", "ContentType": "text/csv"},
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"data"), 4)},
            {"Bucket": settings.s3_bucket, "Key": "k"},
        )
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        stub.add_response("head_object", {}, {"Bucket": settings.s3_bucket, "Key": "k"})
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stub.add_response("delete_object", {}, {"Bucket": settings.s3_bucket, "Key": "k"})
        store.put("k", b"data", content_type="text/csv")
        assert store.get("k") == b"data"
        with pytest.raises(ObjectNotFound):
            store.get("missing")
        assert store.exists("k") is True
        assert store.exists("missing") is False
        store.delete("k")


def test_memory_store() -> None:
    store = MemoryObjectStore()
    store.put("orgs/a/x", b"1")
    assert store.exists("orgs/a/x") and store.stored_keys("orgs/a/") == ["orgs/a/x"]
    store.delete("orgs/a/x")
    with pytest.raises(ObjectNotFound):
        store.get("orgs/a/x")
