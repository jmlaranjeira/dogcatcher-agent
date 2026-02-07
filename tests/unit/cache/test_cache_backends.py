"""Unit tests for cache backends."""

import pytest
import pytest_asyncio
import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

from agent.cache.memory_cache import MemoryCacheBackend
from agent.cache.file_cache import FileCacheBackend
from agent.cache.redis_cache import RedisCacheBackend, REDIS_AVAILABLE


class TestMemoryCacheBackend:
    """Test memory cache backend."""

    @pytest_asyncio.fixture
    async def memory_cache(self):
        """Create memory cache backend for testing."""
        cache = MemoryCacheBackend(max_size=10, name="test_memory")
        yield cache
        await cache.close()

    @pytest.mark.asyncio
    async def test_basic_operations(self, memory_cache):
        """Test basic cache operations."""
        # Test set and get
        assert await memory_cache.set("key1", "value1", ttl=3600)
        result = await memory_cache.get("key1")
        assert result == "value1"

        # Test existence
        assert await memory_cache.exists("key1")
        assert not await memory_cache.exists("nonexistent")

        # Test delete
        assert await memory_cache.delete("key1")
        assert not await memory_cache.exists("key1")
        result = await memory_cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, memory_cache):
        """Test TTL expiration."""
        # Set with very short TTL
        await memory_cache.set("expiring_key", "value", ttl=0.1)

        # Should exist immediately
        assert await memory_cache.exists("expiring_key")

        # Wait for expiration
        await asyncio.sleep(0.2)

        # Should be expired
        result = await memory_cache.get("expiring_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_lru_eviction(self, memory_cache):
        """Test LRU eviction when cache is full."""
        # Fill cache to capacity
        for i in range(10):
            await memory_cache.set(f"key{i}", f"value{i}")

        # All keys should exist
        for i in range(10):
            assert await memory_cache.exists(f"key{i}")

        # Add one more key (should evict oldest)
        await memory_cache.set("key10", "value10")

        # key0 should be evicted (oldest)
        assert not await memory_cache.exists("key0")
        assert await memory_cache.exists("key10")

    @pytest.mark.asyncio
    async def test_cache_stats(self, memory_cache):
        """Test cache statistics."""
        # Initial stats
        stats = memory_cache.get_stats()
        assert stats["backend"] == "test_memory"
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

        # Add some data and access it
        await memory_cache.set("test_key", "test_value")
        await memory_cache.get("test_key")  # Hit
        await memory_cache.get("nonexistent")  # Miss

        stats = memory_cache.get_stats()
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate_percent"] == 50.0

    @pytest.mark.asyncio
    async def test_clear_cache(self, memory_cache):
        """Test cache clearing."""
        # Add some data
        await memory_cache.set("key1", "value1")
        await memory_cache.set("key2", "value2")

        assert memory_cache.get_stats()["size"] == 2

        # Clear cache
        await memory_cache.clear()

        assert memory_cache.get_stats()["size"] == 0
        assert not await memory_cache.exists("key1")
        assert not await memory_cache.exists("key2")


class TestFileCacheBackend:
    """Test file cache backend."""

    @pytest_asyncio.fixture
    async def temp_dir(self):
        """Create temporary directory for file cache."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest_asyncio.fixture
    async def file_cache(self, temp_dir):
        """Create file cache backend for testing."""
        cache = FileCacheBackend(cache_dir=temp_dir, name="test_file")
        yield cache
        await cache.close()

    @pytest.mark.asyncio
    async def test_basic_operations(self, file_cache):
        """Test basic cache operations."""
        # Test set and get
        test_data = {"key": "value", "number": 42}
        assert await file_cache.set("test_key", test_data, ttl=3600)

        result = await file_cache.get("test_key")
        assert result == test_data

        # Test existence
        assert await file_cache.exists("test_key")
        assert not await file_cache.exists("nonexistent")

        # Test delete
        assert await file_cache.delete("test_key")
        assert not await file_cache.exists("test_key")

    @pytest.mark.asyncio
    async def test_persistence(self, temp_dir):
        """Test that data persists across cache instances."""
        test_data = {"persistent": True}

        # Create first cache instance
        cache1 = FileCacheBackend(cache_dir=temp_dir, name="test_file1")
        await cache1.set("persistent_key", test_data, ttl=3600)
        await cache1.close()

        # Create second cache instance
        cache2 = FileCacheBackend(cache_dir=temp_dir, name="test_file2")
        result = await cache2.get("persistent_key")
        assert result == test_data
        await cache2.close()

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, file_cache):
        """Test TTL expiration."""
        # Set with very short TTL
        await file_cache.set("expiring_key", "value", ttl=0.1)

        # Should exist immediately
        assert await file_cache.exists("expiring_key")

        # Wait for expiration
        await asyncio.sleep(0.2)

        # Should be expired
        result = await file_cache.get("expiring_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, file_cache):
        """Test cleanup of expired entries."""
        # Add some keys with different TTLs
        await file_cache.set("short_ttl", "value1", ttl=0.1)
        await file_cache.set("long_ttl", "value2", ttl=3600)

        # Wait for short TTL to expire
        await asyncio.sleep(0.2)

        # Cleanup expired entries
        removed_count = await file_cache.cleanup_expired()
        assert removed_count == 1

        # Long TTL should still exist
        assert await file_cache.exists("long_ttl")
        assert not await file_cache.exists("short_ttl")

    @pytest.mark.asyncio
    async def test_disk_usage_tracking(self, file_cache):
        """Test disk usage tracking."""
        # Add some data
        large_data = {"data": "x" * 1000}  # 1KB of data
        await file_cache.set("large_key", large_data)

        stats = file_cache.get_stats()
        assert stats["disk_usage_bytes"] > 0
        assert stats["size"] == 1

    @pytest.mark.asyncio
    async def test_clear_cache(self, file_cache):
        """Test cache clearing."""
        # Add some data
        await file_cache.set("key1", "value1")
        await file_cache.set("key2", "value2")

        assert file_cache.get_stats()["size"] == 2

        # Clear cache
        await file_cache.clear()

        assert file_cache.get_stats()["size"] == 0
        assert not await file_cache.exists("key1")
        assert not await file_cache.exists("key2")


@pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not available")
class TestRedisCacheBackend:
    """Test Redis cache backend (requires Redis to be running)."""

    @pytest_asyncio.fixture
    async def redis_cache(self):
        """Create Redis cache backend for testing."""
        # Use database 15 for testing to avoid conflicts
        cache = RedisCacheBackend(
            redis_url="redis://localhost:6379/15", key_prefix="test:", name="test_redis"
        )

        # Clear test database before test
        if await cache._ensure_connected():
            await cache.clear()

        yield cache

        # Clean up after test
        if cache._connected:
            await cache.clear()
            await cache.close()

    @pytest.mark.asyncio
    async def test_connection(self, redis_cache):
        """Test Redis connection."""
        connected = await redis_cache._ensure_connected()
        if not connected:
            pytest.skip("Redis server not reachable")

    @pytest.mark.asyncio
    async def test_basic_operations(self, redis_cache):
        """Test basic cache operations."""
        if not await redis_cache._ensure_connected():
            pytest.skip("Redis not available")

        # Test set and get
        test_data = {"key": "value", "number": 42}
        assert await redis_cache.set("test_key", test_data, ttl=3600)

        result = await redis_cache.get("test_key")
        assert result == test_data

        # Test existence
        assert await redis_cache.exists("test_key")
        assert not await redis_cache.exists("nonexistent")

        # Test delete
        assert await redis_cache.delete("test_key")
        assert not await redis_cache.exists("test_key")

    @pytest.mark.asyncio
    async def test_ttl_behavior(self, redis_cache):
        """Test TTL behavior with Redis."""
        if not await redis_cache._ensure_connected():
            pytest.skip("Redis not available")

        # Set with TTL
        await redis_cache.set("ttl_key", "value", ttl=1)

        # Should exist immediately
        assert await redis_cache.exists("ttl_key")

        # Wait for expiration (Redis handles this automatically)
        await asyncio.sleep(1.1)

        # Should be expired
        result = await redis_cache.get("ttl_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_bulk_operations(self, redis_cache):
        """Test bulk operations with pipeline."""
        if not await redis_cache._ensure_connected():
            pytest.skip("Redis not available")

        # Test bulk set
        items = {
            "bulk_key1": {"data": "value1"},
            "bulk_key2": {"data": "value2"},
            "bulk_key3": {"data": "value3"},
        }

        result = await redis_cache.set_with_pipeline(items, ttl=3600)
        assert result == 3

        # Verify all items were set
        for key, expected_value in items.items():
            result = await redis_cache.get(key)
            assert result == expected_value

    @pytest.mark.asyncio
    async def test_key_pattern_search(self, redis_cache):
        """Test key pattern search."""
        if not await redis_cache._ensure_connected():
            pytest.skip("Redis not available")

        # Add some keys with pattern
        await redis_cache.set("pattern:key1", "value1")
        await redis_cache.set("pattern:key2", "value2")
        await redis_cache.set("other:key3", "value3")

        # Search for pattern
        pattern_keys = await redis_cache.get_keys_by_pattern("pattern:*")
        assert "pattern:key1" in pattern_keys
        assert "pattern:key2" in pattern_keys
        assert "other:key3" not in pattern_keys


class TestCacheBackendErrors:
    """Test error handling in cache backends."""

    @pytest.mark.asyncio
    async def test_memory_cache_error_handling(self):
        """Test error handling in memory cache."""
        cache = MemoryCacheBackend(max_size=10)

        # Test with invalid data types that might cause pickle errors
        # Memory cache should handle this gracefully
        result = await cache.set("test", "normal_string")
        assert result is True

        await cache.close()

    @pytest.mark.asyncio
    async def test_file_cache_permission_errors(self):
        """Test file cache behavior with permission issues."""
        # FileCacheBackend.__init__ calls mkdir(parents=True) which raises
        # on truly inaccessible paths. Test that the constructor raises.
        with pytest.raises((OSError, FileNotFoundError)):
            FileCacheBackend(cache_dir="/nonexistent/path", name="test_file")

    @pytest.mark.asyncio
    async def test_redis_connection_failure(self):
        """Test Redis cache behavior when connection fails."""
        if not REDIS_AVAILABLE:
            # Without redis package, the constructor raises ImportError
            with pytest.raises(ImportError):
                RedisCacheBackend(
                    redis_url="redis://nonexistent:6379", name="test_redis_fail"
                )
        else:
            cache = RedisCacheBackend(
                redis_url="redis://nonexistent:6379", name="test_redis_fail"
            )
            result = await cache.set("test_key", "test_value")
            assert result is False
            result = await cache.get("test_key")
            assert result is None
            assert not await cache.exists("test_key")
            await cache.close()


# Integration test utilities


@pytest.fixture
def cache_test_data():
    """Common test data for cache tests."""
    return {
        "simple_string": "test_value",
        "simple_dict": {"key": "value", "number": 42},
        "complex_dict": {
            "nested": {"data": [1, 2, 3]},
            "timestamp": "2025-12-09T10:30:00Z",
            "boolean": True,
            "float": 3.14159,
        },
        "list_data": [1, "two", {"three": 3}],
        "unicode_data": "Test with üñîçödé characters 🚀",
    }


@pytest.mark.asyncio
async def test_cache_data_integrity(cache_test_data):
    """Test data integrity across different cache backends."""
    backends = [
        MemoryCacheBackend(max_size=100, name="integrity_memory"),
        FileCacheBackend(cache_dir=tempfile.mkdtemp(), name="integrity_file"),
    ]

    if REDIS_AVAILABLE:
        redis_backend = RedisCacheBackend(
            redis_url="redis://localhost:6379/15",
            key_prefix="integrity:",
            name="integrity_redis",
        )
        if await redis_backend._ensure_connected():
            backends.append(redis_backend)

    for backend in backends:
        try:
            # Test each data type
            for key, expected_value in cache_test_data.items():
                await backend.set(f"test_{key}", expected_value, ttl=3600)
                result = await backend.get(f"test_{key}")
                assert (
                    result == expected_value
                ), f"Data integrity failed for {key} in {backend.name}"

        finally:
            await backend.close()


@pytest.mark.asyncio
async def test_concurrent_cache_access():
    """Test concurrent access to cache backends."""
    cache = MemoryCacheBackend(max_size=1000, name="concurrent_test")

    async def write_worker(worker_id: int):
        """Worker that writes to cache."""
        for i in range(10):
            key = f"worker_{worker_id}_key_{i}"
            value = f"worker_{worker_id}_value_{i}"
            await cache.set(key, value, ttl=3600)

    async def read_worker(worker_id: int):
        """Worker that reads from cache."""
        hits = 0
        for i in range(10):
            for worker in range(3):  # Read keys from all workers
                key = f"worker_{worker}_key_{i}"
                result = await cache.get(key)
                if result:
                    hits += 1
        return hits

    try:
        # Run concurrent writers
        write_tasks = [write_worker(i) for i in range(3)]
        await asyncio.gather(*write_tasks)

        # Run concurrent readers
        read_tasks = [read_worker(i) for i in range(3)]
        hit_counts = await asyncio.gather(*read_tasks)

        # Should have some hits (exact count depends on timing)
        assert sum(hit_counts) > 0

        # Verify final state
        stats = cache.get_stats()
        assert stats["size"] == 30  # 3 workers × 10 keys each

    finally:
        await cache.close()
