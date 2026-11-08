from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.config import Settings
from app.storage import ensure_bucket, make_s3_client, wait_and_ensure_bucket


def _client_error(code: str, operation: str = "HeadBucket") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, operation)


def test_existing_bucket_is_left_alone() -> None:
    client = MagicMock()
    assert ensure_bucket(client, "analyst-dev") is False
    client.create_bucket.assert_not_called()


@pytest.mark.parametrize("code", ["404", "NoSuchBucket", "NotFound"])
def test_missing_bucket_is_created(code: str) -> None:
    client = MagicMock()
    client.head_bucket.side_effect = _client_error(code)
    assert ensure_bucket(client, "analyst-dev") is True
    client.create_bucket.assert_called_once_with(Bucket="analyst-dev")


def test_authentication_errors_are_not_mistaken_for_a_missing_bucket() -> None:
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("403")
    with pytest.raises(ClientError):
        ensure_bucket(client, "analyst-dev")
    client.create_bucket.assert_not_called()


def test_client_uses_path_style_and_the_configured_endpoint(settings: Settings) -> None:
    client: Any = make_s3_client(settings.model_copy(update={"s3_endpoint": "http://storage:9000"}))
    assert client.meta.endpoint_url == "http://storage:9000"
    assert client.meta.config.s3["addressing_style"] == "path"
    assert client.meta.region_name == settings.s3_region


def test_wait_retries_until_the_service_is_reachable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    unreachable = EndpointConnectionError(endpoint_url="http://storage:9000")
    client.head_bucket.side_effect = [unreachable, unreachable, None]
    monkeypatch.setattr("app.storage.make_s3_client", lambda _settings: client)
    monkeypatch.setattr("app.storage.time.sleep", lambda _seconds: None)
    assert wait_and_ensure_bucket(settings, attempts=5) is False
    assert client.head_bucket.call_count == 3


def test_wait_gives_up_after_the_last_attempt(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    client.head_bucket.side_effect = EndpointConnectionError(endpoint_url="http://storage:9000")
    monkeypatch.setattr("app.storage.make_s3_client", lambda _settings: client)
    monkeypatch.setattr("app.storage.time.sleep", lambda _seconds: None)
    with pytest.raises(EndpointConnectionError):
        wait_and_ensure_bucket(settings, attempts=3)
    assert client.head_bucket.call_count == 3


def test_wait_fails_fast_on_wrong_credentials(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("403")
    monkeypatch.setattr("app.storage.make_s3_client", lambda _settings: client)
    with pytest.raises(ClientError):
        wait_and_ensure_bucket(settings, attempts=5)
    assert client.head_bucket.call_count == 1
