"""S3-compatible object storage helpers (local RustFS container, AWS S3 ``ap-south-1`` in production).

``python -m app.storage`` waits for the storage service and creates the bucket if it is missing; docker compose
runs it once at start-up. File upload/versioning and snapshots build on ``make_s3_client`` in later milestones.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings, get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

_NOT_FOUND_CODES = {"404", "NoSuchBucket", "NotFound"}


def make_s3_client(settings: Settings, *, timeout_seconds: float = 5.0) -> Any:
    """A boto3 S3 client. Path-style addressing is required by local S3-compatible servers. With no access key
    configured, boto3's own credential chain is used (the ECS task role on AWS, docs/DEPLOY_AWS.md; M19)."""
    secret = settings.s3_secret_key.get_secret_value()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=secret or None,
        region_name=settings.s3_region,
        config=BotoConfig(
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={"max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )


def ensure_bucket(client: Any, bucket: str) -> bool:
    """Create ``bucket`` if it does not exist. Returns True when it was created, False if it already existed.

    Any other error (for example 403 from wrong credentials) is raised: it must not be mistaken for "missing".
    """
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code not in _NOT_FOUND_CODES:
            raise
    else:
        return False
    client.create_bucket(Bucket=bucket)
    return True


def wait_and_ensure_bucket(
    settings: Settings, *, attempts: int = 60, delay_seconds: float = 1.0
) -> bool:
    """Retry while the storage service is still starting; fail fast on authentication errors."""
    client = make_s3_client(settings)
    for attempt in range(1, attempts + 1):
        try:
            return ensure_bucket(client, settings.s3_bucket)
        except ClientError:
            raise
        except BotoCoreError:
            if attempt == attempts:
                raise
            logger.info("storage not ready yet (attempt %d/%d)", attempt, attempts)
            time.sleep(delay_seconds)
    raise AssertionError("unreachable")  # pragma: no cover


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    try:
        created = wait_and_ensure_bucket(settings)
    except Exception:
        logger.exception("could not prepare bucket %s", settings.s3_bucket)
        return 1
    logger.info("bucket %s %s", settings.s3_bucket, "created" if created else "already exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
