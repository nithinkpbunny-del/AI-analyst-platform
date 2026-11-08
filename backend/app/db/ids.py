"""UUIDv7 generation (BUILD_SPEC section 5: all ids are UUIDv7).

Python 3.12 has no ``uuid.uuid7``; this follows RFC 9562: 48-bit unix-millisecond timestamp, version 7,
variant 10, and 74 random bits. Ids sort by creation time, which keeps B-tree indexes compact.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    timestamp_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits, we use 74 of them
    rand_a = (rand >> 68) & 0xFFF  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits

    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= rand_a << 64
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return uuid.UUID(int=value)
