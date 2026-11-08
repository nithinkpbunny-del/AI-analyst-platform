from __future__ import annotations

import time
import uuid

from app.db.ids import uuid7


def test_uuid7_has_version_and_variant_bits() -> None:
    value = uuid7()
    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_embeds_the_current_time_in_milliseconds() -> None:
    before = int(time.time() * 1000)
    value = uuid7()
    after = int(time.time() * 1000)
    assert before <= (value.int >> 80) <= after


def test_uuid7_sorts_by_creation_time() -> None:
    first = uuid7()
    time.sleep(0.003)
    second = uuid7()
    assert first < second


def test_uuid7_is_unique() -> None:
    assert len({uuid7() for _ in range(20_000)}) == 20_000
