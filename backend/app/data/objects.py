"""Object storage for uploaded files and Parquet snapshots, behind a small interface.

* ``S3ObjectStore``: the S3-compatible bucket (local RustFS container, AWS S3 ``ap-south-1`` in production).
* ``MemoryObjectStore``: a dictionary, for tests.

Keys always start with ``orgs/<org id>/`` so one workspace's objects can be listed and deleted together, and
never contain a user-supplied file name (an uploaded file is stored under its version id).
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Protocol

from botocore.exceptions import ClientError

from app.config import Settings
from app.storage import make_s3_client


class ObjectNotFound(Exception):
    pass


class ObjectStore(Protocol):
    def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


def org_prefix(org_id: uuid.UUID) -> str:
    return f"orgs/{org_id}/"


def file_key(org_id: uuid.UUID, connection_id: uuid.UUID, version_id: uuid.UUID) -> str:
    return f"{org_prefix(org_id)}files/{connection_id}/{version_id}"


def snapshot_key(org_id: uuid.UUID, dataset_id: uuid.UUID, snapshot_id: uuid.UUID) -> str:
    return f"{org_prefix(org_id)}snapshots/{dataset_id}/{snapshot_id}.parquet"


class S3ObjectStore:
    """The client is created on first use, so building the app never needs the storage service."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.s3_bucket
        self._client: Any = None
        self._lock = threading.Lock()

    def _s3(self) -> Any:
        with self._lock:
            if self._client is None:
                # Uploads of up to UPLOAD_MAX_MB need more than the health check's 5 s.
                self._client = make_s3_client(self._settings, timeout_seconds=60)
            return self._client

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        self._s3().put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            response = self._s3().get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code", "")) in {"NoSuchKey", "404"}:
                raise ObjectNotFound(key) from error
            raise
        body: bytes = response["Body"].read()
        return body

    def delete(self, key: str) -> None:
        self._s3().delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._s3().head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code", "")) in {
                "NoSuchKey",
                "404",
                "NotFound",
            }:
                return False
            raise
        return True


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        with self._lock:
            self.objects[key] = bytes(data)

    def get(self, key: str) -> bytes:
        with self._lock:
            if key not in self.objects:
                raise ObjectNotFound(key)
            return self.objects[key]

    def delete(self, key: str) -> None:
        with self._lock:
            self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self.objects

    def stored_keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            return sorted(k for k in self.objects if k.startswith(prefix))
