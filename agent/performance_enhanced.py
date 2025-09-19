"""Enhanced performance optimization with persistent caching.

This module provides performance optimizations including:
- Persistent caching with multiple backends (Redis, File, Memory)
- Performance metrics tracking
- Cache optimization recommendations
- Backward compatibility with existing similarity cache API
"""

import time
import asyncio
from typing import Dict, Any, Optional, Tuple
from functools import lru_cache
from datetime import datetime

from agent.config import get_config
from agent.utils.logger import log_info, log_debug, log_warning, log_error
from agent.cache import CacheManager


class PerformanceMetrics:
    """Track performance metrics for the agent."""

    def __init__(self):
        self.metrics: Dict[str, Any] = {}
        self.start_times: Dict[str, float] = {}

    def start_timer(self, operation: str) -> None:
        """Start timing an operation."""
        self.start_times[operation] = time.time()

    def end_timer(self, operation: str) -> float:
        """End timing an operation and return duration."""
        if operation not in self.start_times:
            return 0.0

        duration = time.time() - self.start_times[operation]
        del self.start_times[operation]

        # Store the metric
        if operation not in self.metrics:
            self.metrics[operation] = []
        self.metrics[operation].append(duration)

        # Keep only last 100 measurements
        if len(self.metrics[operation]) > 100:
            self.metrics[operation] = self.metrics[operation][-100:]

        return duration

    def get_operation_stats(self, operation: str) -> Dict[str, float]:
        """Get statistics for a specific operation."""
        if operation not in self.metrics or not self.metrics[operation]:
            return {}

        durations = self.metrics[operation]
        return {
            "count": len(durations),
            "avg_ms": round(sum(durations) * 1000 / len(durations), 2),
            "min_ms": round(min(durations) * 1000, 2),
            "max_ms": round(max(durations) * 1000, 2),
            "total_ms": round(sum(durations) * 1000, 2)
        }

    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all operations."""
        return {op: self.get_operation_stats(op) for op in self.metrics.keys()}

    def log_performance_summary(self) -> None:
        """Log a performance summary."""
        stats = self.get_all_stats()
        if not stats:
            return

        log_info("Performance metrics summary")
        for operation, op_stats in stats.items():
            if op_stats:
                log_info(f"  {operation}: {op_stats['count']} calls, "
                        f"avg {op_stats['avg_ms']}ms, "
                        f"min {op_stats['min_ms']}ms, "
                        f"max {op_stats['max_ms']}ms")


class EnhancedSimilarityCache:
    """Enhanced similarity cache with persistent backends."""

    def __init__(self):
        self.cache_manager: Optional[CacheManager] = None
        self._initialized = False
        self.hits = 0
        self.misses = 0
        self.errors = 0

    async def initialize(self) -> bool:
        """Initialize the cache manager."""
        if self._initialized:
            return True

        try:
            config = get_config()

            # Get cache configuration
            cache_config = {
                "backend": getattr(config, "cache_backend", "memory"),
                "redis_url": getattr(config, "cache_redis_url", "redis://localhost:6379"),
                "file_cache_dir": getattr(config, "cache_file_dir", ".agent_cache/persistent"),
                "ttl_seconds": getattr(config, "cache_ttl_seconds", 3600),
                "max_memory_size": getattr(config, "cache_max_memory_size", 1000),
                "similarity_ttl_seconds": getattr(config, "cache_similarity_ttl_seconds", 3600)
            }

            self.cache_manager = CacheManager(cache_config)
            self._initialized = await self.cache_manager.initialize()

            if self._initialized:
                log_info("Enhanced similarity cache initialized successfully",
                        backend=self.cache_manager.active_backend.name if self.cache_manager.active_backend else "none")
            else:
                log_error("Failed to initialize enhanced similarity cache")

            return self._initialized

        except Exception as e:
            log_error("Error initializing enhanced similarity cache", error=str(e))
            return False

    async def get(self, summary: str, state: Optional[dict] = None) -> Optional[Tuple[str, float, str]]:
        """Get cached similarity result."""
        if not self._initialized or not self.cache_manager:
            self.misses += 1
            return None

        try:
            result = await self.cache_manager.get_similarity(summary, state)
            if result:
                self.hits += 1
                return result
            else:
                self.misses += 1
                return None

        except Exception as e:
            log_error("Error getting similarity from cache", error=str(e))
            self.errors += 1
            return None

    async def set(self, summary: str, state: Optional[dict], result: Tuple[str, float, str]) -> None:
        """Cache similarity result."""
        if not self._initialized or not self.cache_manager:
            return

        try:
            success = await self.cache_manager.set_similarity(summary, result, state)
            if not success:
                log_warning("Failed to cache similarity result")

        except Exception as e:
            log_error("Error setting similarity in cache", error=str(e))
            self.errors += 1

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        if not self._initialized or not self.cache_manager:
            return {
                "status": "not_initialized",
                "hits": self.hits,
                "misses": self.misses,
                "errors": self.errors
            }

        try:
            cache_stats = self.cache_manager.get_stats()
            cache_stats.update({
                "wrapper_hits": self.hits,
                "wrapper_misses": self.misses,
                "wrapper_errors": self.errors,
                "wrapper_hit_rate_percent": self._calculate_hit_rate()
            })
            return cache_stats

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "hits": self.hits,
                "misses": self.misses,
                "errors": self.errors
            }

    def _calculate_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0

    async def clear(self) -> None:
        """Clear all cache entries."""
        if not self._initialized or not self.cache_manager:
            return

        try:
            await self.cache_manager.clear()
            self.hits = 0
            self.misses = 0
            self.errors = 0
            log_info("Enhanced similarity cache cleared")

        except Exception as e:
            log_error("Error clearing similarity cache", error=str(e))

    async def close(self) -> None:
        """Close cache manager."""
        if self.cache_manager:
            await self.cache_manager.close()
            self._initialized = False

    async def optimize(self) -> Dict[str, Any]:
        """Optimize cache performance."""
        if not self._initialized or not self.cache_manager:
            return {"status": "not_initialized"}

        try:
            return await self.cache_manager.optimize()

        except Exception as e:
            log_error("Error optimizing cache", error=str(e))
            return {"status": "error", "error": str(e)}

    async def health_check(self) -> Dict[str, Any]:
        """Perform cache health check."""
        if not self._initialized or not self.cache_manager:
            return {"status": "not_initialized", "healthy": False}

        try:
            health = await self.cache_manager.health_check()
            health["wrapper_stats"] = {
                "hits": self.hits,
                "misses": self.misses,
                "errors": self.errors,
                "hit_rate_percent": self._calculate_hit_rate()
            }
            return health

        except Exception as e:
            log_error("Error checking cache health", error=str(e))
            return {"status": "error", "error": str(e), "healthy": False}


# Global instances
_enhanced_similarity_cache: Optional[EnhancedSimilarityCache] = None
performance_metrics = PerformanceMetrics()


async def get_similarity_cache() -> EnhancedSimilarityCache:
    """Get the global enhanced similarity cache instance."""
    global _enhanced_similarity_cache

    if _enhanced_similarity_cache is None:
        _enhanced_similarity_cache = EnhancedSimilarityCache()
        await _enhanced_similarity_cache.initialize()

    return _enhanced_similarity_cache


def get_performance_metrics() -> PerformanceMetrics:
    """Get the global performance metrics instance."""
    return performance_metrics


async def log_performance_summary() -> None:
    """Log performance summary including cache stats and metrics."""
    try:
        # Enhanced cache statistics
        cache = await get_similarity_cache()
        cache_stats = cache.get_stats()
        log_info("Enhanced similarity cache statistics", **cache_stats)

        # Performance metrics
        performance_metrics.log_performance_summary()

    except Exception as e:
        log_error("Error logging performance summary", error=str(e))


def optimize_jira_search_params() -> Dict[str, Any]:
    """Get optimized Jira search parameters based on configuration."""
    config = get_config()

    # Optimize search window based on project activity
    # For high-volume projects, reduce window to improve performance
    if config.jira_search_window_days > 180:
        optimized_window = 180  # 6 months max
        log_info("Optimized Jira search window",
                original=config.jira_search_window_days,
                optimized=optimized_window,
                reason="High-volume project optimization")
    else:
        optimized_window = config.jira_search_window_days

    # Optimize max results based on similarity threshold
    # Higher thresholds need fewer results since we're looking for exact matches
    if config.jira_similarity_threshold >= 0.9:
        optimized_max_results = min(50, config.jira_search_max_results)
        log_info("Optimized Jira search max results",
                original=config.jira_search_max_results,
                optimized=optimized_max_results,
                reason="High similarity threshold optimization")
    elif config.jira_similarity_threshold >= 0.8:
        optimized_max_results = min(100, config.jira_search_max_results)
        log_info("Optimized Jira search max results",
                original=config.jira_search_max_results,
                optimized=optimized_max_results,
                reason="Medium similarity threshold optimization")
    else:
        optimized_max_results = config.jira_search_max_results

    return {
        "search_window_days": optimized_window,
        "search_max_results": optimized_max_results,
        "similarity_threshold": config.jira_similarity_threshold,
        "direct_log_threshold": config.jira_direct_log_threshold,
        "partial_log_threshold": config.jira_partial_log_threshold
    }


def log_configuration_performance() -> None:
    """Log performance-related configuration values."""
    config = get_config()

    log_info("Performance configuration",
             jira_search_window_days=config.jira_search_window_days,
             jira_search_max_results=config.jira_search_max_results,
             jira_similarity_threshold=config.jira_similarity_threshold,
             jira_direct_log_threshold=config.jira_direct_log_threshold,
             jira_partial_log_threshold=config.jira_partial_log_threshold,
             datadog_limit=config.datadog_limit,
             datadog_max_pages=config.datadog_max_pages,
             datadog_timeout=config.datadog_timeout,
             max_tickets_per_run=config.max_tickets_per_run,
             cache_backend=getattr(config, "cache_backend", "memory"),
             cache_ttl_seconds=getattr(config, "cache_ttl_seconds", 3600))


@lru_cache(maxsize=128)
def cached_normalize_text(text: str) -> str:
    """Cached version of text normalization for frequently used strings."""
    from agent.jira.utils import normalize_text
    return normalize_text(text)


@lru_cache(maxsize=128)
def cached_normalize_log_message(message: str) -> str:
    """Cached version of log message normalization for frequently used strings."""
    from agent.jira.utils import normalize_log_message
    return normalize_log_message(message)


async def clear_performance_caches() -> None:
    """Clear all performance-related caches."""
    try:
        # Clear enhanced similarity cache
        cache = await get_similarity_cache()
        await cache.clear()

        # Clear LRU caches
        cached_normalize_text.cache_clear()
        cached_normalize_log_message.cache_clear()

        log_info("All performance caches cleared")

    except Exception as e:
        log_error("Error clearing performance caches", error=str(e))


def get_performance_recommendations() -> list[str]:
    """Get performance optimization recommendations based on current configuration."""
    config = get_config()
    recommendations = []

    # Search window recommendations
    if config.jira_search_window_days > 180:
        recommendations.append(
            f"Consider reducing JIRA_SEARCH_WINDOW_DAYS from {config.jira_search_window_days} to 180 "
            "for better performance in high-volume projects"
        )

    # Max results recommendations
    if config.jira_search_max_results > 200:
        recommendations.append(
            f"Consider reducing JIRA_SEARCH_MAX_RESULTS from {config.jira_search_max_results} to 200 "
            "for faster duplicate detection"
        )

    # Similarity threshold recommendations
    if config.jira_similarity_threshold < 0.7:
        recommendations.append(
            f"Consider increasing JIRA_SIMILARITY_THRESHOLD from {config.jira_similarity_threshold} to 0.8+ "
            "to reduce false positives and improve performance"
        )

    # Datadog limit recommendations
    if config.datadog_limit < 20:
        recommendations.append(
            f"Consider increasing DATADOG_LIMIT from {config.datadog_limit} to 50+ "
            "to reduce API calls and improve efficiency"
        )

    # Timeout recommendations
    if config.datadog_timeout < 15:
        recommendations.append(
            f"Consider increasing DATADOG_TIMEOUT from {config.datadog_timeout} to 20+ "
            "to avoid timeout issues"
        )

    # Cache recommendations
    cache_backend = getattr(config, "cache_backend", "memory")
    if cache_backend == "memory":
        recommendations.append(
            "Consider using Redis cache backend (CACHE_BACKEND=redis) for better performance "
            "and persistence across restarts"
        )

    return recommendations


# Backward compatibility wrapper for old SimilarityCache API
class LegacySimilarityCache:
    """Backward compatibility wrapper for the old SimilarityCache API."""

    def __init__(self):
        self._enhanced_cache: Optional[EnhancedSimilarityCache] = None
        self._loop = None

    def _get_loop(self):
        """Get or create event loop."""
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            return self._loop

    async def _get_cache(self) -> EnhancedSimilarityCache:
        """Get enhanced cache instance."""
        if self._enhanced_cache is None:
            self._enhanced_cache = await get_similarity_cache()
        return self._enhanced_cache

    def get(self, summary: str, state: Optional[dict] = None) -> Optional[Tuple[str, float, str]]:
        """Get cached similarity result (sync wrapper)."""
        loop = self._get_loop()
        cache = loop.run_until_complete(self._get_cache())
        return loop.run_until_complete(cache.get(summary, state))

    def set(self, summary: str, state: Optional[dict], result: Tuple[str, float, str]) -> None:
        """Cache similarity result (sync wrapper)."""
        loop = self._get_loop()
        cache = loop.run_until_complete(self._get_cache())
        loop.run_until_complete(cache.set(summary, state, result))

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics (sync wrapper)."""
        loop = self._get_loop()
        cache = loop.run_until_complete(self._get_cache())
        return cache.get_stats()

    def clear(self) -> None:
        """Clear cache (sync wrapper)."""
        loop = self._get_loop()
        cache = loop.run_until_complete(self._get_cache())
        loop.run_until_complete(cache.clear())


# Create legacy instance for backward compatibility
similarity_cache = LegacySimilarityCache()


# Migration utilities

async def migrate_from_legacy_cache() -> Dict[str, Any]:
    """Migrate data from legacy memory cache to enhanced cache."""
    try:
        # This would be implemented if we had data to migrate
        # For now, it's a placeholder for future use

        log_info("Legacy cache migration completed (no data to migrate)")
        return {
            "status": "completed",
            "migrated_entries": 0,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        log_error("Error during cache migration", error=str(e))
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


async def performance_health_check() -> Dict[str, Any]:
    """Comprehensive performance health check."""
    try:
        health = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "components": {}
        }

        # Check cache health
        cache = await get_similarity_cache()
        cache_health = await cache.health_check()
        health["components"]["cache"] = cache_health

        # Check performance metrics
        metrics_stats = performance_metrics.get_all_stats()
        health["components"]["metrics"] = {
            "active_operations": len(metrics_stats),
            "total_measurements": sum(len(stats.get("count", 0)) for stats in metrics_stats.values() if stats)
        }

        # Overall health determination
        cache_healthy = cache_health.get("healthy", False)
        health["status"] = "healthy" if cache_healthy else "degraded"

        return health

    except Exception as e:
        log_error("Error during performance health check", error=str(e))
        return {
            "timestamp": datetime.now().isoformat(),
            "status": "error",
            "error": str(e)
        }