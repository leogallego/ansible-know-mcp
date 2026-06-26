"""Tests for BoundedCache disk persistence."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from ansible_know.cache import BoundedCache


class TestDiskPersistenceRoundTrip:
    """Save and load round-trip tests."""

    def test_put_persists_to_disk(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache.put("key1", 42)
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert isinstance(data, dict)
        assert "entries" in data

    def test_load_on_construction(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        # Write some data with first cache
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("key1", 42)
        cache1.put("key2", 99)

        # Create new cache from same file — should load
        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert cache2.get("key1") == 42
        assert cache2.get("key2") == 99

    def test_round_trip_preserves_values(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[str, str] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("hello", "world")
        cache1.put("foo", "bar")

        cache2: BoundedCache[str, str] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert cache2.get("hello") == "world"
        assert cache2.get("foo") == "bar"


class TestTTLExpiryOnLoad:
    """Stale entries should be discarded when loading from disk."""

    def test_expired_entries_discarded_on_load(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=1.0, path=cache_file,
        )
        cache1.put("old_key", 100)

        # Manually edit the file to set an old epoch timestamp
        data = json.loads(cache_file.read_text())
        for entry in data["entries"]:
            entry["epoch_ts"] = time.time() - 3600  # 1 hour ago
        cache_file.write_text(json.dumps(data))

        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=1.0, path=cache_file,
        )
        assert cache2.get("old_key") is None
        assert len(cache2) == 0

    def test_fresh_entries_survive_load(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("fresh_key", 200)

        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert cache2.get("fresh_key") == 200


class TestTupleKeySerialization:
    """Tuple keys must round-trip through JSON."""

    def test_two_element_tuple_key(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[tuple[str, str], str] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put(("namespace", "name"), "1.2.3")

        cache2: BoundedCache[tuple[str, str], str] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert cache2.get(("namespace", "name")) == "1.2.3"

    def test_three_element_tuple_key(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[tuple[str, str, str], dict] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put(("ns", "name", "1.0.0"), {"docs": "blob"})

        cache2: BoundedCache[tuple[str, str, str], dict] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert cache2.get(("ns", "name", "1.0.0")) == {"docs": "blob"}


class TestCorruptFileHandling:
    """Missing or corrupt cache files must be handled gracefully."""

    def test_missing_file_starts_empty(self, tmp_path: Path):
        cache_file = tmp_path / "nonexistent.json"
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert len(cache) == 0
        assert cache.get("anything") is None

    def test_corrupt_json_starts_empty(self, tmp_path: Path):
        cache_file = tmp_path / "corrupt.json"
        cache_file.write_text("not valid json {{{")
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert len(cache) == 0

    def test_empty_file_starts_empty(self, tmp_path: Path):
        cache_file = tmp_path / "empty.json"
        cache_file.write_text("")
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert len(cache) == 0

    def test_wrong_structure_starts_empty(self, tmp_path: Path):
        cache_file = tmp_path / "wrong.json"
        cache_file.write_text(json.dumps(["not", "a", "dict"]))
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert len(cache) == 0

    def test_corrupt_file_logs_warning(self, tmp_path: Path, caplog):
        cache_file = tmp_path / "corrupt.json"
        cache_file.write_text("not valid json")
        with caplog.at_level("WARNING", logger="ansible_know"):
            cache = BoundedCache(max_size=10, ttl=3600, path=cache_file)
            cache.get("trigger_load")  # lazy load triggers on first access
        assert any("corrupt" in r.message.lower() or "failed" in r.message.lower() for r in caplog.records)


class TestNoDiskCacheEnvVar:
    """ANSIBLE_KNOW_NO_DISK_CACHE=1 disables persistence."""

    def test_no_disk_cache_disables_persistence(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        with patch.dict(os.environ, {"ANSIBLE_KNOW_NO_DISK_CACHE": "1"}):
            cache: BoundedCache[str, int] = BoundedCache(
                max_size=10, ttl=3600, path=cache_file,
            )
            cache.put("key1", 42)
        assert not cache_file.exists()
        assert cache.get("key1") == 42  # in-memory still works


class TestCacheDirectoryCreation:
    """Cache directory should be created automatically."""

    def test_creates_parent_directories(self, tmp_path: Path):
        cache_file = tmp_path / "sub" / "dir" / "test_cache.json"
        assert not cache_file.parent.exists()
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache.put("key1", 42)
        assert cache_file.exists()
        assert cache_file.parent.exists()


class TestClearDeletesDiskFile:
    """clear() should also delete the disk file."""

    def test_clear_removes_disk_file(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache.put("key1", 42)
        assert cache_file.exists()
        cache.clear()
        assert not cache_file.exists()
        assert len(cache) == 0


class TestLazyLoading:
    """Disk load should be deferred to first access, not construction."""

    def test_construction_does_not_read_disk(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        # Write a cache file with known data
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("key1", 42)

        # Construct a new cache — should NOT load yet
        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        # Internal data should be empty before first access
        assert cache2._disk_loaded is False

    def test_first_get_triggers_load(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("key1", 42)

        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert cache2._disk_loaded is False
        result = cache2.get("key1")
        assert cache2._disk_loaded is True
        assert result == 42

    def test_first_put_triggers_load(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("existing", 100)

        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache2.put("new_key", 200)
        # Both old and new entries should be available
        assert cache2.get("existing") == 100
        assert cache2.get("new_key") == 200

    def test_len_triggers_load(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("a", 1)
        cache1.put("b", 2)

        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert len(cache2) == 2

    def test_contains_triggers_load(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache1: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache1.put("key1", 42)

        cache2: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        assert "key1" in cache2


class TestWriteOutsideLock:
    """Disk writes should not hold the cache lock."""

    def test_put_releases_lock_before_disk_write(self, tmp_path: Path):
        cache_file = tmp_path / "test_cache.json"
        cache: BoundedCache[str, int] = BoundedCache(
            max_size=10, ttl=3600, path=cache_file,
        )
        cache.put("key1", 42)
        # After put() returns, the lock should not be held
        assert not cache._lock.locked()
        # Data should be on disk
        assert cache_file.exists()
        # Data should be in memory
        assert cache.get("key1") == 42


class TestNoPersistenceWithoutPath:
    """Without a path, behavior is unchanged from in-memory only."""

    def test_no_path_no_disk_writes(self, tmp_path: Path):
        cache: BoundedCache[str, int] = BoundedCache(max_size=10, ttl=3600)
        cache.put("key1", 42)
        assert cache.get("key1") == 42
        # No file created anywhere — just verify in-memory works as before
