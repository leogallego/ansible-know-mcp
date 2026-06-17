"""Shared bounded cache with optional TTL.

Provides a single thread-safe cache implementation to replace the ad-hoc
caching patterns spread across galaxy.py, docs.py, collections.py,
and server.py.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class BoundedCache(Generic[K, V]):
    """Thread-safe LRU cache with optional TTL expiry.

    Args:
        max_size: Maximum number of entries before LRU eviction.
        ttl: Time-to-live in seconds. None means entries never expire.
    """

    def __init__(self, max_size: int, ttl: float | None = None) -> None:
        self._max_size = max_size
        self._ttl = ttl
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._lock = threading.Lock()

    def _is_expired(self, timestamp: float) -> bool:
        return self._ttl is not None and time.monotonic() - timestamp > self._ttl

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, timestamp = entry
            if self._is_expired(timestamp):
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic())
            self._data.move_to_end(key)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    @property
    def max_size(self) -> int:
        return self._max_size

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: K) -> bool:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            return not self._is_expired(entry[1])
