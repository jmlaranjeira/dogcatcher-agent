"""Performance optimization and caching for the dogcatcher-agent.

This module provides caching mechanisms and performance optimizations
to reduce API calls and improve response times.
"""

import time
import hashlib
from typing import Dict, Any, Optional, Tuple, Set
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime, timedelta

from agent.config import get_config
from agent.utils.logger import log_info, log_debug, log_warning


@dataclass
class CacheEntry:
    """A cache entry with timestamp and data."""

    data: Any
    timestamp: datetime
    ttl_seconds: int = 300  # 5 minutes default TTL

    def is_expired(self) -> bool:
        """Check if the cache entry has expired."""
        return datetime.now() - self.timestamp > timedelta(seconds=self.ttl_seconds)


class SimilarityCache:
    """In-memory cache for similarity calculations to avoid repeated API calls."""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        self.cache: Dict[str, CacheEntry] = {}
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.hits = 0
        self.misses = 0

    def _make_key(self, summary: str, state: Optional[dict] = None) -> str:
        """Create a cache key from summary and state."""
        # Include relevant state fields in the key
        state_key = ""
        if state:
            error_type = state.get("error_type", "")
            logger = state.get("log_data", {}).get("logger", "")
            state_key = f"|{error_type}|{logger}"

        # Create hash of the normalized summary + state
        key_data = f"{summary.lower().strip()}{state_key}"
        return hashlib.md5(key_data.encode(), usedforsecurity=False).hexdigest()

    def get(
        self, summary: str, state: Optional[dict] = None
    ) -> Optional[Tuple[str, float, str]]:
        """Get cached similarity result."""
        key = self._make_key(summary, state)

        if key in self.cache:
            entry = self.cache[key]
            if not entry.is_expired():
                self.hits += 1
                log_debug(
                    "Similarity cache hit", key=key[:8], summary_preview=summary[:50]
                )
                return entry.data
            else:
                # Remove expired entry
                del self.cache[key]

        self.misses += 1
        return None

    def set(
        self, summary: str, state: Optional[dict], result: Tuple[str, float, str]
    ) -> None:
        """Cache similarity result."""
        key = self._make_key(summary, state)

        # Remove oldest entries if cache is full
        if len(self.cache) >= self.max_size:
            self._evict_oldest()

        self.cache[key] = CacheEntry(
            data=result, timestamp=datetime.now(), ttl_seconds=self.ttl_seconds
        )

        log_debug("Similarity cache set", key=key[:8], summary_preview=summary[:50])

    def _evict_oldest(self) -> None:
        """Remove the oldest cache entry."""
        if not self.cache:
            return

        oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k].timestamp)
        del self.cache[oldest_key]
        log_debug("Similarity cache evicted oldest entry", key=oldest_key[:8])

    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()
        self.hits = 0
        self.misses = 0
        log_info("Similarity cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": round(hit_rate, 2),
            "ttl_seconds": self.ttl_seconds,
        }


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
            "total_ms": round(sum(durations) * 1000, 2),
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
                log_info(
                    f"  {operation}: {op_stats['count']} calls, "
                    f"avg {op_stats['avg_ms']}ms, "
                    f"min {op_stats['min_ms']}ms, "
                    f"max {op_stats['max_ms']}ms"
                )


# Global instances
similarity_cache = SimilarityCache()
performance_metrics = PerformanceMetrics()


def get_similarity_cache() -> SimilarityCache:
    """Get the global similarity cache instance."""
    return similarity_cache


def get_performance_metrics() -> PerformanceMetrics:
    """Get the global performance metrics instance."""
    return performance_metrics


def log_performance_summary() -> None:
    """Log performance summary including cache stats and metrics."""
    # Cache statistics
    cache_stats = similarity_cache.get_stats()
    log_info("Similarity cache statistics", **cache_stats)

    # Performance metrics
    performance_metrics.log_performance_summary()


def optimize_jira_search_params() -> Dict[str, Any]:
    """Get optimized Jira search parameters based on configuration."""
    config = get_config()

    # Optimize search window based on project activity
    # For high-volume projects, reduce window to improve performance
    if config.jira_search_window_days > 180:
        optimized_window = 180  # 6 months max
        log_info(
            "Optimized Jira search window",
            original=config.jira_search_window_days,
            optimized=optimized_window,
            reason="High-volume project optimization",
        )
    else:
        optimized_window = config.jira_search_window_days

    # Optimize max results based on similarity threshold
    # Higher thresholds need fewer results since we're looking for exact matches
    if config.jira_similarity_threshold >= 0.9:
        optimized_max_results = min(50, config.jira_search_max_results)
        log_info(
            "Optimized Jira search max results",
            original=config.jira_search_max_results,
            optimized=optimized_max_results,
            reason="High similarity threshold optimization",
        )
    elif config.jira_similarity_threshold >= 0.8:
        optimized_max_results = min(100, config.jira_search_max_results)
        log_info(
            "Optimized Jira search max results",
            original=config.jira_search_max_results,
            optimized=optimized_max_results,
            reason="Medium similarity threshold optimization",
        )
    else:
        optimized_max_results = config.jira_search_max_results

    return {
        "search_window_days": optimized_window,
        "search_max_results": optimized_max_results,
        "similarity_threshold": config.jira_similarity_threshold,
        "direct_log_threshold": config.jira_direct_log_threshold,
        "partial_log_threshold": config.jira_partial_log_threshold,
    }


def log_configuration_performance() -> None:
    """Log performance-related configuration values."""
    config = get_config()

    log_info(
        "Performance configuration",
        jira_search_window_days=config.jira_search_window_days,
        jira_search_max_results=config.jira_search_max_results,
        jira_similarity_threshold=config.jira_similarity_threshold,
        jira_direct_log_threshold=config.jira_direct_log_threshold,
        jira_partial_log_threshold=config.jira_partial_log_threshold,
        datadog_limit=config.datadog_limit,
        datadog_max_pages=config.datadog_max_pages,
        datadog_timeout=config.datadog_timeout,
        max_tickets_per_run=config.max_tickets_per_run,
    )


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


def clear_performance_caches() -> None:
    """Clear all performance-related caches."""
    similarity_cache.clear()
    cached_normalize_text.cache_clear()
    cached_normalize_log_message.cache_clear()
    log_info("All performance caches cleared")


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

    return recommendations
