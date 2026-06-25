"""Shared bounded cache with optional TTL and disk persistence.

Provides a single thread-safe cache implementation to replace the ad-hoc
caching patterns spread across galaxy.py, docs.py, collections.py,
and server.py.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

logger = logging.getLogger("ansible_know")


def _serialize_key(key: Any) -> str:
    """Serialize a cache key to a JSON-safe string."""
    if isinstance(key, tuple):
        return json.dumps(list(key))
    return json.dumps(key)


def _deserialize_key(raw: str) -> Any:
    """Deserialize a JSON string back to a cache key."""
    val = json.loads(raw)
    if isinstance(val, list):
        return tuple(val)
    return val


class BoundedCache(Generic[K, V]):
    """Thread-safe LRU cache with optional TTL expiry and disk persistence.

    Args:
        max_size: Maximum number of entries before LRU eviction.
        ttl: Time-to-live in seconds. None means entries never expire.
        path: Path to a JSON file for disk persistence. When provided,
            entries are loaded from disk on construction and written
            through on put(). Set ANSIBLE_KNOW_NO_DISK_CACHE=1 to
            disable persistence even when a path is given.
    """

    def __init__(
        self,
        max_size: int,
        ttl: float | None = None,
        path: Path | str | None = None,
    ) -> None:
        self._max_size = max_size
        self._ttl = ttl
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._lock = threading.Lock()

        no_disk = os.environ.get("ANSIBLE_KNOW_NO_DISK_CACHE", "").strip()
        if path is not None and no_disk != "1":
            self._path: Path | None = Path(path)
        else:
            self._path = None

        if self._path is not None:
            self._load_from_disk()

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

    def _purge_expired(self) -> None:
        if self._ttl is None:
            return
        expired = [k for k, (_, ts) in self._data.items() if self._is_expired(ts)]
        for k in expired:
            del self._data[k]

    def put(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic())
            self._data.move_to_end(key)
            if len(self._data) > self._max_size:
                self._purge_expired()
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)
            if self._path is not None:
                self._write_to_disk()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    pass

    @property
    def max_size(self) -> int:
        return self._max_size

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: K) -> bool:
        # Non-mutating: expired entries are left for get()/put() to clean up.
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            return not self._is_expired(entry[1])

    def _write_to_disk(self) -> None:
        """Persist current entries to disk using epoch timestamps."""
        assert self._path is not None
        now_mono = time.monotonic()
        now_epoch = time.time()
        entries = []
        for key, (value, mono_ts) in self._data.items():
            age = now_mono - mono_ts
            epoch_ts = now_epoch - age
            entries.append({
                "key": _serialize_key(key),
                "value": value,
                "epoch_ts": epoch_ts,
            })
        data = {"version": 1, "entries": entries}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("Failed to write cache to %s: %s", self._path.name, exc)

    def _load_from_disk(self) -> None:
        """Load entries from disk, discarding expired ones."""
        assert self._path is not None
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            if not raw.strip():
                return
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to load cache from %s, starting empty: %s",
                self._path.name, exc,
            )
            return

        if not isinstance(data, dict) or "entries" not in data:
            logger.warning(
                "Failed to load cache from %s, starting empty: unexpected structure",
                self._path.name,
            )
            return

        now_epoch = time.time()
        now_mono = time.monotonic()
        loaded = 0
        for entry in data["entries"]:
            try:
                key = _deserialize_key(entry["key"])
                value = entry["value"]
                epoch_ts = entry["epoch_ts"]
            except (KeyError, json.JSONDecodeError, TypeError):
                continue

            age = now_epoch - epoch_ts
            if self._ttl is not None and age > self._ttl:
                continue

            mono_ts = now_mono - age
            self._data[key] = (value, mono_ts)
            loaded += 1
            if loaded >= self._max_size:
                break
