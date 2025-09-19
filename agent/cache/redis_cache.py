"""Redis-based distributed cache backend."""

import asyncio
import pickle
from datetime import datetime
from typing import Any, Optional, Dict
import json

from .base import CacheBackend, CacheStats

try:
    import aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class RedisCacheBackend(CacheBackend):
    """Redis-based cache backend for distributed caching."""

    def __init__(self, redis_url: str = "redis://localhost:6379",
                 key_prefix: str = "dogcatcher:", name: str = "redis"):
        super().__init__(name)

        if not REDIS_AVAILABLE:
            raise ImportError("aioredis is required for Redis cache backend")

        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.redis = None
        self._connected = False

    async def _ensure_connected(self) -> bool:
        """Ensure Redis connection is established."""
        if self._connected and self.redis:
            try:
                await self.redis.ping()
                return True
            except Exception:
                self._connected = False

        try:
            self.redis = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=False  # We handle binary data
            )
            await self.redis.ping()
            self._connected = True
            return True

        except Exception:
            self._connected = False
            self._record_error()
            return False

    def _make_key(self, key: str) -> str:
        """Create Redis key with prefix."""
        return f"{self.key_prefix}{key}"

    def _make_metadata_key(self, key: str) -> str:
        """Create metadata key for cache entry."""
        return f"{self.key_prefix}meta:{key}"

    async def get(self, key: str) -> Optional[Any]:
        """Get value from Redis cache."""
        try:
            if not await self._ensure_connected():
                self._record_miss()
                return None

            redis_key = self._make_key(key)
            metadata_key = self._make_metadata_key(key)

            # Check if key exists and is not expired
            if not await self.redis.exists(redis_key):
                self._record_miss()
                return None

            # Get data and metadata
            data_bytes = await self.redis.get(redis_key)
            metadata_bytes = await self.redis.get(metadata_key)

            if not data_bytes:
                self._record_miss()
                return None

            # Deserialize data
            data = pickle.loads(data_bytes)

            # Update access count in metadata
            if metadata_bytes:
                try:
                    metadata = json.loads(metadata_bytes.decode())
                    metadata["access_count"] = metadata.get("access_count", 0) + 1

                    # Update metadata with new access count
                    await self.redis.set(
                        metadata_key,
                        json.dumps(metadata),
                        ex=metadata.get("ttl_seconds", 3600)
                    )
                except Exception:
                    pass  # Don't fail on metadata update issues

            self._record_hit()
            return data

        except Exception:
            self._record_error()
            return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in Redis cache."""
        try:
            if not await self._ensure_connected():
                return False

            redis_key = self._make_key(key)
            metadata_key = self._make_metadata_key(key)

            # Serialize data
            data_bytes = pickle.dumps(value)

            # Create metadata
            metadata = {
                "timestamp": datetime.now().isoformat(),
                "ttl_seconds": ttl,
                "access_count": 0,
                "size_bytes": len(data_bytes)
            }
            metadata_bytes = json.dumps(metadata)

            # Set data and metadata with TTL
            if ttl > 0:
                await asyncio.gather(
                    self.redis.set(redis_key, data_bytes, ex=ttl),
                    self.redis.set(metadata_key, metadata_bytes, ex=ttl)
                )
            else:
                await asyncio.gather(
                    self.redis.set(redis_key, data_bytes),
                    self.redis.set(metadata_key, metadata_bytes)
                )

            return True

        except Exception:
            self._record_error()
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from Redis cache."""
        try:
            if not await self._ensure_connected():
                return False

            redis_key = self._make_key(key)
            metadata_key = self._make_metadata_key(key)

            # Delete both data and metadata
            result = await self.redis.delete(redis_key, metadata_key)
            return result > 0

        except Exception:
            self._record_error()
            return False

    async def clear(self) -> bool:
        """Clear all cache entries with our prefix."""
        try:
            if not await self._ensure_connected():
                return False

            # Find all keys with our prefix
            pattern = f"{self.key_prefix}*"
            cursor = 0
            keys_deleted = 0

            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)

                if keys:
                    await self.redis.delete(*keys)
                    keys_deleted += len(keys)

                if cursor == 0:
                    break

            return True

        except Exception:
            self._record_error()
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in Redis cache."""
        try:
            if not await self._ensure_connected():
                return False

            redis_key = self._make_key(key)
            return await self.redis.exists(redis_key) > 0

        except Exception:
            self._record_error()
            return False

    async def cleanup_expired(self) -> int:
        """Redis automatically handles TTL expiration."""
        # Redis handles expiration automatically, but we can return 0
        # In a more sophisticated implementation, we could scan for
        # expired keys and return an estimate
        return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get Redis cache statistics."""
        try:
            stats = CacheStats()
            stats.hits = self.hits
            stats.misses = self.misses
            stats.errors = self.errors

            # For size and memory usage, we'd need to scan all keys
            # which can be expensive for large caches
            # For now, we'll return basic stats

            return {
                **stats.to_dict(),
                "backend": self.name,
                "redis_url": self.redis_url,
                "connected": self._connected,
                "key_prefix": self.key_prefix,
                "note": "Size and memory stats require scanning all keys"
            }

        except Exception:
            return {
                "backend": self.name,
                "error": "Failed to collect stats",
                "hits": self.hits,
                "misses": self.misses,
                "errors": self.errors,
                "connected": self._connected
            }

    async def close(self) -> None:
        """Close Redis connection."""
        if self.redis:
            try:
                await self.redis.close()
            except Exception:
                pass
            finally:
                self.redis = None
                self._connected = False

    # Additional Redis-specific methods

    async def get_redis_info(self) -> Optional[Dict[str, Any]]:
        """Get Redis server information."""
        try:
            if not await self._ensure_connected():
                return None

            info = await self.redis.info()
            return {
                "redis_version": info.get("redis_version"),
                "used_memory": info.get("used_memory"),
                "used_memory_human": info.get("used_memory_human"),
                "connected_clients": info.get("connected_clients"),
                "total_commands_processed": info.get("total_commands_processed"),
                "keyspace_hits": info.get("keyspace_hits"),
                "keyspace_misses": info.get("keyspace_misses")
            }

        except Exception:
            return None

    async def set_with_pipeline(self, items: Dict[str, Any], ttl: int = 3600) -> int:
        """Set multiple items using Redis pipeline for better performance."""
        try:
            if not await self._ensure_connected():
                return 0

            pipe = self.redis.pipeline()

            for key, value in items.items():
                redis_key = self._make_key(key)
                metadata_key = self._make_metadata_key(key)

                data_bytes = pickle.dumps(value)
                metadata = {
                    "timestamp": datetime.now().isoformat(),
                    "ttl_seconds": ttl,
                    "access_count": 0,
                    "size_bytes": len(data_bytes)
                }

                if ttl > 0:
                    pipe.set(redis_key, data_bytes, ex=ttl)
                    pipe.set(metadata_key, json.dumps(metadata), ex=ttl)
                else:
                    pipe.set(redis_key, data_bytes)
                    pipe.set(metadata_key, json.dumps(metadata))

            await pipe.execute()
            return len(items)

        except Exception:
            self._record_error()
            return 0

    async def get_keys_by_pattern(self, pattern: str) -> list:
        """Get all keys matching pattern."""
        try:
            if not await self._ensure_connected():
                return []

            full_pattern = f"{self.key_prefix}{pattern}"
            cursor = 0
            all_keys = []

            while True:
                cursor, keys = await self.redis.scan(cursor, match=full_pattern, count=100)
                all_keys.extend(keys)

                if cursor == 0:
                    break

            # Remove prefix from keys
            return [key.decode().replace(self.key_prefix, "") for key in all_keys]

        except Exception:
            self._record_error()
            return []