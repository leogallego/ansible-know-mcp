"""Tests for ansible_know.cache."""

import threading
import time
from unittest.mock import patch

from ansible_know.cache import BoundedCache


class TestBoundedCache:
    def test_get_put_basic(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10)
        cache.put("a", 1)
        assert cache.get("a") == 1

    def test_get_missing_returns_none(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10)
        assert cache.get("missing") is None

    def test_lru_eviction(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.put("d", 4)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("d") == 4

    def test_lru_access_refreshes(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.get("a")
        cache.put("d", 4)
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_ttl_expiry(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10, ttl=0.5)
        cache.put("a", 1)
        assert cache.get("a") == 1
        with patch("ansible_know.cache.time.monotonic", return_value=time.monotonic() + 1.0):
            assert cache.get("a") is None

    def test_ttl_not_expired(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10, ttl=10.0)
        cache.put("a", 1)
        assert cache.get("a") == 1

    def test_no_ttl_never_expires(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10)
        cache.put("a", 1)
        with patch("ansible_know.cache.time.monotonic", return_value=time.monotonic() + 999999):
            assert cache.get("a") == 1

    def test_overwrite_value(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10)
        cache.put("a", 1)
        cache.put("a", 2)
        assert cache.get("a") == 2

    def test_clear(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert len(cache) == 0

    def test_len(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10)
        assert len(cache) == 0
        cache.put("a", 1)
        assert len(cache) == 1
        cache.put("b", 2)
        assert len(cache) == 2

    def test_contains(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10)
        cache.put("a", 1)
        assert "a" in cache
        assert "b" not in cache

    def test_contains_does_not_refresh_lru(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        assert "a" in cache
        cache.put("d", 4)
        assert "a" not in cache

    def test_contains_expired_does_not_delete(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10, ttl=0.5)
        cache.put("a", 1)
        with patch("ansible_know.cache.time.monotonic", return_value=time.monotonic() + 1.0):
            assert "a" not in cache
        assert len(cache) == 1

    def test_max_size_property(self):
        cache: BoundedCache[str, int] = BoundedCache(max_size=42)
        assert cache.max_size == 42

    def test_tuple_keys(self):
        cache: BoundedCache[tuple[str, str], str] = BoundedCache(max_size=10)
        cache.put(("ns", "name"), "1.0.0")
        assert cache.get(("ns", "name")) == "1.0.0"
        assert cache.get(("ns", "other")) is None

    def test_thread_safety(self):
        cache: BoundedCache[int, int] = BoundedCache(max_size=1000)
        errors: list[Exception] = []

        def writer(start: int) -> None:
            try:
                for i in range(start, start + 100):
                    cache.put(i, i * 2)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i * 100,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(cache) <= 1000
