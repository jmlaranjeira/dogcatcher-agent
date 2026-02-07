"""Abstract base classes for cache backends."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any, Optional, Dict
import hashlib


@dataclass
class CacheEntry:
    """A cache entry with metadata."""

    key: str
    data: Any
    timestamp: datetime
    ttl_seconds: int = 3600  # 1 hour default TTL
    access_count: int = 0

    def is_expired(self) -> bool:
        """Check if the cache entry has expired."""
        if self.ttl_seconds <= 0:  # Never expires if TTL is 0 or negative
            return False
        return datetime.now() - self.timestamp > timedelta(seconds=self.ttl_seconds)

    def touch(self) -> None:
        """Update access count and timestamp on cache hit."""
        self.access_count += 1
        # Note: We don't update timestamp on access to preserve original TTL


class CacheBackend(ABC):
    """Abstract base class for cache backends."""

    def __init__(self, name: str):
        self.name = name
        self.hits = 0
        self.misses = 0
        self.errors = 0

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache by key."""
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in cache with TTL."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        pass

    @abstractmethod
    async def clear(self) -> bool:
        """Clear all cache entries."""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        pass

    @abstractmethod
    async def cleanup_expired(self) -> int:
        """Remove expired entries and return count removed."""
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close cache backend and cleanup resources."""
        pass

    # Utility methods

    def _make_cache_key(self, prefix: str, *parts: str) -> str:
        """Create a consistent cache key from parts."""
        key_parts = [prefix] + list(parts)
        key_string = "|".join(str(part) for part in key_parts)

        # Hash long keys to avoid key length limitations
        if len(key_string) > 200:
            key_hash = hashlib.md5(
                key_string.encode(), usedforsecurity=False
            ).hexdigest()
            return f"{prefix}:{key_hash}"

        return key_string.replace(" ", "_")

    def _record_hit(self) -> None:
        """Record cache hit."""
        self.hits += 1

    def _record_miss(self) -> None:
        """Record cache miss."""
        self.misses += 1

    def _record_error(self) -> None:
        """Record cache error."""
        self.errors += 1

    def get_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0


class CacheStats:
    """Cache statistics data structure."""

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.errors = 0
        self.size = 0
        self.memory_usage = 0
        self.oldest_entry = None
        self.newest_entry = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "hit_rate_percent": self.hit_rate,
            "size": self.size,
            "memory_usage_bytes": self.memory_usage,
            "oldest_entry": (
                self.oldest_entry.isoformat() if self.oldest_entry else None
            ),
            "newest_entry": (
                self.newest_entry.isoformat() if self.newest_entry else None
            ),
        }

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate percentage."""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0
