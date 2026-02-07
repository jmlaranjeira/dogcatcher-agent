"""In-memory cache backend with LRU eviction."""

import asyncio
from datetime import datetime
from typing import Any, Optional, Dict
from collections import OrderedDict

from .base import CacheBackend, CacheEntry, CacheStats


class MemoryCacheBackend(CacheBackend):
    """In-memory cache with LRU eviction policy."""

    def __init__(self, max_size: int = 1000, name: str = "memory"):
        super().__init__(name)
        self.max_size = max_size
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from memory cache."""
        async with self._lock:
            try:
                if key in self.cache:
                    entry = self.cache[key]

                    if entry.is_expired():
                        # Remove expired entry
                        del self.cache[key]
                        self._record_miss()
                        return None

                    # Move to end (most recently used)
                    self.cache.move_to_end(key)
                    entry.touch()
                    self._record_hit()
                    return entry.data

                self._record_miss()
                return None

            except Exception:
                self._record_error()
                return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in memory cache."""
        async with self._lock:
            try:
                # Remove existing entry if present
                if key in self.cache:
                    del self.cache[key]

                # Create new entry
                entry = CacheEntry(
                    key=key, data=value, timestamp=datetime.now(), ttl_seconds=ttl
                )

                # Add to cache
                self.cache[key] = entry

                # Enforce size limit with LRU eviction
                while len(self.cache) > self.max_size:
                    self.cache.popitem(last=False)  # Remove least recently used

                return True

            except Exception:
                self._record_error()
                return False

    async def delete(self, key: str) -> bool:
        """Delete key from memory cache."""
        async with self._lock:
            try:
                if key in self.cache:
                    del self.cache[key]
                    return True
                return False

            except Exception:
                self._record_error()
                return False

    async def clear(self) -> bool:
        """Clear all cache entries."""
        async with self._lock:
            try:
                self.cache.clear()
                return True

            except Exception:
                self._record_error()
                return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        async with self._lock:
            try:
                if key in self.cache:
                    entry = self.cache[key]
                    if entry.is_expired():
                        del self.cache[key]
                        return False
                    return True
                return False

            except Exception:
                self._record_error()
                return False

    async def cleanup_expired(self) -> int:
        """Remove expired entries."""
        async with self._lock:
            try:
                expired_keys = []
                now = datetime.now()

                for key, entry in self.cache.items():
                    if entry.is_expired():
                        expired_keys.append(key)

                for key in expired_keys:
                    del self.cache[key]

                return len(expired_keys)

            except Exception:
                self._record_error()
                return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get memory cache statistics."""
        try:
            stats = CacheStats()
            stats.hits = self.hits
            stats.misses = self.misses
            stats.errors = self.errors
            stats.size = len(self.cache)

            if self.cache:
                # Calculate memory usage (rough estimate)
                import sys

                stats.memory_usage = sum(
                    sys.getsizeof(entry.data) + sys.getsizeof(entry)
                    for entry in self.cache.values()
                )

                # Find oldest and newest entries
                entries = list(self.cache.values())
                stats.oldest_entry = min(entry.timestamp for entry in entries)
                stats.newest_entry = max(entry.timestamp for entry in entries)

            return {
                **stats.to_dict(),
                "backend": self.name,
                "max_size": self.max_size,
                "eviction_policy": "LRU",
            }

        except Exception:
            return {
                "backend": self.name,
                "error": "Failed to collect stats",
                "hits": self.hits,
                "misses": self.misses,
                "errors": self.errors,
            }

    async def close(self) -> None:
        """Close memory cache (cleanup)."""
        async with self._lock:
            self.cache.clear()

    # Additional memory-specific methods

    async def cleanup_lru(self, percentage: float = 0.3) -> int:
        """Remove least recently used entries by percentage."""
        async with self._lock:
            try:
                if not self.cache:
                    return 0

                target_size = int(len(self.cache) * (1 - percentage))
                removed_count = 0

                while len(self.cache) > target_size:
                    self.cache.popitem(last=False)  # Remove LRU
                    removed_count += 1

                return removed_count

            except Exception:
                self._record_error()
                return 0

    def get_memory_usage(self) -> int:
        """Get approximate memory usage in bytes."""
        try:
            import sys

            return sum(
                sys.getsizeof(entry.data) + sys.getsizeof(entry)
                for entry in self.cache.values()
            )
        except Exception:
            return 0
