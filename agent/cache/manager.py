"""Cache manager with automatic backend selection and fallback."""

import asyncio
from typing import Any, Optional, Dict, List
from enum import Enum

from agent.utils.logger import log_info, log_warning, log_error, log_debug
from .base import CacheBackend
from .redis_cache import RedisCacheBackend, REDIS_AVAILABLE
from .file_cache import FileCacheBackend
from .memory_cache import MemoryCacheBackend


class CacheBackendType(Enum):
    """Cache backend types."""

    REDIS = "redis"
    FILE = "file"
    MEMORY = "memory"


class CacheManager:
    """Manages multiple cache backends with automatic fallback."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.primary_backend: Optional[CacheBackend] = None
        self.fallback_backends: List[CacheBackend] = []
        self.active_backend: Optional[CacheBackend] = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize cache manager with configured backends."""
        if self._initialized:
            return True

        try:
            backend_type = self.config.get("backend", "memory").lower()
            log_info("Initializing cache manager", backend_type=backend_type)

            # Create primary backend
            self.primary_backend = await self._create_backend(backend_type)

            # Create fallback backends
            await self._create_fallback_backends(backend_type)

            # Set active backend
            self.active_backend = await self._select_active_backend()

            if self.active_backend:
                log_info(
                    "Cache manager initialized successfully",
                    active_backend=self.active_backend.name,
                    fallback_count=len(self.fallback_backends),
                )
                self._initialized = True
                return True
            else:
                log_error("No cache backend available")
                return False

        except Exception as e:
            log_error("Failed to initialize cache manager", error=str(e))
            return False

    async def _create_backend(self, backend_type: str) -> Optional[CacheBackend]:
        """Create a cache backend of specified type."""
        try:
            if backend_type == CacheBackendType.REDIS.value:
                if not REDIS_AVAILABLE:
                    log_warning("Redis not available, redis package not installed")
                    return None

                redis_url = self.config.get("redis_url", "redis://localhost:6379")
                backend = RedisCacheBackend(redis_url=redis_url)

                # Test connection
                if await backend._ensure_connected():
                    log_info("Redis cache backend created successfully")
                    return backend
                else:
                    log_warning("Redis connection failed")
                    await backend.close()
                    return None

            elif backend_type == CacheBackendType.FILE.value:
                cache_dir = self.config.get("file_cache_dir", ".agent_cache/persistent")
                backend = FileCacheBackend(cache_dir=cache_dir)
                log_info("File cache backend created successfully", cache_dir=cache_dir)
                return backend

            elif backend_type == CacheBackendType.MEMORY.value:
                max_size = self.config.get("max_memory_size", 1000)
                backend = MemoryCacheBackend(max_size=max_size)
                log_info("Memory cache backend created successfully", max_size=max_size)
                return backend

            else:
                log_error("Unknown cache backend type", backend_type=backend_type)
                return None

        except Exception as e:
            log_error(
                "Failed to create cache backend",
                backend_type=backend_type,
                error=str(e),
            )
            return None

    async def _create_fallback_backends(self, primary_type: str) -> None:
        """Create fallback backends."""
        fallback_types = []

        # Define fallback chain
        if primary_type == CacheBackendType.REDIS.value:
            fallback_types = [
                CacheBackendType.FILE.value,
                CacheBackendType.MEMORY.value,
            ]
        elif primary_type == CacheBackendType.FILE.value:
            fallback_types = [CacheBackendType.MEMORY.value]
        # Memory doesn't need fallbacks (it's the ultimate fallback)

        for backend_type in fallback_types:
            backend = await self._create_backend(backend_type)
            if backend:
                self.fallback_backends.append(backend)

        log_info(
            "Fallback backends created",
            fallback_count=len(self.fallback_backends),
            fallbacks=[b.name for b in self.fallback_backends],
        )

    async def _select_active_backend(self) -> Optional[CacheBackend]:
        """Select the active backend from available options."""
        # Try primary backend first
        if self.primary_backend:
            if await self._test_backend(self.primary_backend):
                return self.primary_backend

        # Try fallback backends
        for backend in self.fallback_backends:
            if await self._test_backend(backend):
                log_warning(
                    "Using fallback cache backend",
                    backend=backend.name,
                    primary_failed=(
                        self.primary_backend.name if self.primary_backend else "none"
                    ),
                )
                return backend

        return None

    async def _test_backend(self, backend: CacheBackend) -> bool:
        """Test if a backend is working."""
        try:
            test_key = "cache_test"
            test_value = {"test": True, "timestamp": "now"}

            await backend.set(test_key, test_value, ttl=60)
            result = await backend.get(test_key)
            await backend.delete(test_key)

            return result is not None and result.get("test") is True

        except Exception:
            return False

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if not self._initialized or not self.active_backend:
            return None

        try:
            return await self.active_backend.get(key)

        except Exception as e:
            log_error("Cache get failed", key=key[:50], error=str(e))
            await self._handle_backend_failure()
            return None

    async def set(self, key: str, value: Any, ttl: int = None) -> bool:
        """Set value in cache."""
        if not self._initialized or not self.active_backend:
            return False

        try:
            ttl = ttl or self.config.get("ttl_seconds", 3600)
            return await self.active_backend.set(key, value, ttl)

        except Exception as e:
            log_error("Cache set failed", key=key[:50], error=str(e))
            await self._handle_backend_failure()
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self._initialized or not self.active_backend:
            return False

        try:
            return await self.active_backend.delete(key)

        except Exception as e:
            log_error("Cache delete failed", key=key[:50], error=str(e))
            await self._handle_backend_failure()
            return False

    async def clear(self) -> bool:
        """Clear all cache entries."""
        if not self._initialized or not self.active_backend:
            return False

        try:
            return await self.active_backend.clear()

        except Exception as e:
            log_error("Cache clear failed", error=str(e))
            await self._handle_backend_failure()
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        if not self._initialized or not self.active_backend:
            return False

        try:
            return await self.active_backend.exists(key)

        except Exception as e:
            log_error("Cache exists check failed", key=key[:50], error=str(e))
            await self._handle_backend_failure()
            return False

    async def cleanup_expired(self) -> int:
        """Remove expired entries."""
        if not self._initialized or not self.active_backend:
            return 0

        try:
            return await self.active_backend.cleanup_expired()

        except Exception as e:
            log_error("Cache cleanup failed", error=str(e))
            await self._handle_backend_failure()
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        if not self._initialized or not self.active_backend:
            return {"status": "not_initialized"}

        try:
            stats = self.active_backend.get_stats()
            stats.update(
                {
                    "manager_status": "active",
                    "primary_backend": (
                        self.primary_backend.name if self.primary_backend else None
                    ),
                    "active_backend": self.active_backend.name,
                    "fallback_backends": [b.name for b in self.fallback_backends],
                    "config": self.config,
                }
            )
            return stats

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "active_backend": (
                    self.active_backend.name if self.active_backend else None
                ),
            }

    async def close(self) -> None:
        """Close all cache backends."""
        try:
            if self.primary_backend:
                await self.primary_backend.close()

            for backend in self.fallback_backends:
                await backend.close()

            log_info("Cache manager closed")

        except Exception as e:
            log_error("Error closing cache manager", error=str(e))

        finally:
            self._initialized = False
            self.primary_backend = None
            self.fallback_backends = []
            self.active_backend = None

    async def _handle_backend_failure(self) -> None:
        """Handle backend failure by switching to fallback."""
        try:
            log_warning(
                "Cache backend failure detected, attempting fallback",
                current_backend=(
                    self.active_backend.name if self.active_backend else "none"
                ),
            )

            # Try to switch to a working fallback
            new_backend = await self._select_active_backend()

            if new_backend and new_backend != self.active_backend:
                old_backend = self.active_backend
                self.active_backend = new_backend
                log_info(
                    "Switched to fallback cache backend",
                    old_backend=old_backend.name if old_backend else "none",
                    new_backend=new_backend.name,
                )
            else:
                log_error("No working cache backend available")
                self.active_backend = None

        except Exception as e:
            log_error("Failed to handle cache backend failure", error=str(e))
            self.active_backend = None

    # Convenience methods for similarity caching (backward compatibility)

    def make_similarity_key(self, summary: str, state: Optional[Dict] = None) -> str:
        """Create cache key for similarity results."""
        if not self.active_backend:
            return ""

        error_type = ""
        logger = ""

        if state:
            error_type = state.get("error_type", "")
            log_data = state.get("log_data", {})
            logger = log_data.get("logger", "")

        # Create a consistent key
        return self.active_backend._make_cache_key(
            "similarity",
            summary.lower().strip()[:100],  # Limit length
            error_type,
            logger,
        )

    async def get_similarity(
        self, summary: str, state: Optional[Dict] = None
    ) -> Optional[tuple]:
        """Get cached similarity result."""
        key = self.make_similarity_key(summary, state)
        if not key:
            return None

        result = await self.get(key)
        if result and isinstance(result, (tuple, list)) and len(result) >= 3:
            log_debug("Similarity cache hit", key=key[:50])
            return tuple(result)

        return None

    async def set_similarity(
        self, summary: str, result: tuple, state: Optional[Dict] = None, ttl: int = None
    ) -> bool:
        """Cache similarity result."""
        key = self.make_similarity_key(summary, state)
        if not key:
            return False

        ttl = ttl or self.config.get("similarity_ttl_seconds", 3600)
        success = await self.set(key, result, ttl)

        if success:
            log_debug("Similarity cache set", key=key[:50])

        return success

    # Health check and maintenance

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on cache system."""
        health = {
            "timestamp": asyncio.get_event_loop().time(),
            "initialized": self._initialized,
            "active_backend": self.active_backend.name if self.active_backend else None,
            "backends": {},
        }

        # Test primary backend
        if self.primary_backend:
            health["backends"][self.primary_backend.name] = await self._test_backend(
                self.primary_backend
            )

        # Test fallback backends
        for backend in self.fallback_backends:
            health["backends"][backend.name] = await self._test_backend(backend)

        health["overall_health"] = any(health["backends"].values())

        return health

    async def optimize(self) -> Dict[str, Any]:
        """Optimize cache performance."""
        if not self._initialized or not self.active_backend:
            return {"status": "not_initialized"}

        results = {"backend": self.active_backend.name, "actions_taken": []}

        try:
            # Cleanup expired entries
            expired_count = await self.cleanup_expired()
            if expired_count > 0:
                results["actions_taken"].append(
                    f"Removed {expired_count} expired entries"
                )

            # Backend-specific optimizations
            if hasattr(self.active_backend, "cleanup_lru"):
                # Memory backend LRU cleanup
                stats = self.active_backend.get_stats()
                if stats.get("size", 0) > stats.get("max_size", 1000) * 0.9:
                    lru_removed = await self.active_backend.cleanup_lru(0.2)
                    results["actions_taken"].append(
                        f"LRU cleanup removed {lru_removed} entries"
                    )

            elif hasattr(self.active_backend, "cleanup_by_size"):
                # File backend size cleanup
                max_size_mb = self.config.get("max_file_cache_size_mb", 100)
                size_removed = await self.active_backend.cleanup_by_size(max_size_mb)
                if size_removed > 0:
                    results["actions_taken"].append(
                        f"Size cleanup removed {size_removed} entries"
                    )

            results["status"] = "completed"

        except Exception as e:
            results["status"] = "error"
            results["error"] = str(e)

        return results
