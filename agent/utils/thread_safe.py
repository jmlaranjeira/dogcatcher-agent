"""Thread-safe utilities for async parallel processing.

This module provides thread-safe data structures and utilities for
concurrent log processing without race conditions.
"""

import asyncio
from typing import Set, Dict, Any, Optional
from datetime import datetime
from agent.utils.logger import log_debug, log_info


class ThreadSafeSet:
    """Thread-safe set implementation for async operations."""

    def __init__(self):
        self._set: Set[str] = set()
        self._lock = asyncio.Lock()

    async def add(self, item: str) -> bool:
        """Add item to set. Returns True if item was added, False if already existed."""
        async with self._lock:
            if item in self._set:
                return False
            self._set.add(item)
            return True

    async def contains(self, item: str) -> bool:
        """Check if item exists in set."""
        async with self._lock:
            return item in self._set

    async def size(self) -> int:
        """Get size of set."""
        async with self._lock:
            return len(self._set)

    async def to_list(self) -> list:
        """Get list of all items."""
        async with self._lock:
            return list(self._set)


class ThreadSafeCounter:
    """Thread-safe counter for tracking statistics."""

    def __init__(self):
        self._count = 0
        self._lock = asyncio.Lock()

    async def increment(self, amount: int = 1) -> int:
        """Increment counter and return new value."""
        async with self._lock:
            self._count += amount
            return self._count

    async def get(self) -> int:
        """Get current count."""
        async with self._lock:
            return self._count

    async def reset(self):
        """Reset counter to zero."""
        async with self._lock:
            self._count = 0


class ThreadSafeDeduplicator:
    """Thread-safe log deduplication for parallel processing."""

    def __init__(self):
        self._seen_logs: Set[str] = set()
        self._created_fingerprints: Set[str] = set()
        self._lock = asyncio.Lock()
        self._stats = {"total_checked": 0, "duplicates_found": 0, "unique_logs": 0}

    async def is_duplicate(self, log_key: str) -> bool:
        """Check if log has been seen. Registers new logs automatically.

        Args:
            log_key: Unique identifier for the log (logger|message)

        Returns:
            True if log is duplicate, False if unique
        """
        async with self._lock:
            self._stats["total_checked"] += 1

            if log_key in self._seen_logs:
                self._stats["duplicates_found"] += 1
                log_debug("Duplicate log detected", log_key=log_key)
                return True

            self._seen_logs.add(log_key)
            self._stats["unique_logs"] += 1
            return False

    async def mark_fingerprint_created(self, fingerprint: str) -> bool:
        """Mark a fingerprint as having a ticket created.

        Args:
            fingerprint: Log fingerprint (sha1 hash)

        Returns:
            True if fingerprint was newly added, False if already existed
        """
        async with self._lock:
            if fingerprint in self._created_fingerprints:
                return False
            self._created_fingerprints.add(fingerprint)
            return True

    async def has_fingerprint(self, fingerprint: str) -> bool:
        """Check if fingerprint has ticket created."""
        async with self._lock:
            return fingerprint in self._created_fingerprints

    async def get_stats(self) -> Dict[str, int]:
        """Get deduplication statistics."""
        async with self._lock:
            return dict(self._stats)

    async def size(self) -> tuple:
        """Get sizes of internal sets (seen_logs, created_fingerprints)."""
        async with self._lock:
            return len(self._seen_logs), len(self._created_fingerprints)


class ProcessingStats:
    """Thread-safe statistics tracking for async processing."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._stats = {
            "total_logs": 0,
            "processed": 0,
            "tickets_created": 0,
            "tickets_simulated": 0,
            "duplicates": 0,
            "errors": 0,
            "start_time": None,
            "end_time": None,
            "processing_times": [],
        }

    async def record_start(self):
        """Record processing start time."""
        async with self._lock:
            self._stats["start_time"] = datetime.now()
            log_info(
                "Async processing started",
                timestamp=self._stats["start_time"].isoformat(),
            )

    async def record_end(self):
        """Record processing end time."""
        async with self._lock:
            self._stats["end_time"] = datetime.now()
            log_info(
                "Async processing completed",
                timestamp=self._stats["end_time"].isoformat(),
            )

    async def record_log_processed(self, processing_time: float):
        """Record a log being processed."""
        async with self._lock:
            self._stats["processed"] += 1
            self._stats["processing_times"].append(processing_time)

    async def record_ticket_created(self):
        """Record a ticket being created."""
        async with self._lock:
            self._stats["tickets_created"] += 1

    async def record_ticket_simulated(self):
        """Record a simulated ticket."""
        async with self._lock:
            self._stats["tickets_simulated"] += 1

    async def record_duplicate(self):
        """Record a duplicate log."""
        async with self._lock:
            self._stats["duplicates"] += 1

    async def record_error(self):
        """Record an error."""
        async with self._lock:
            self._stats["errors"] += 1

    async def set_total_logs(self, count: int):
        """Set total number of logs to process."""
        async with self._lock:
            self._stats["total_logs"] = count

    async def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive statistics summary."""
        async with self._lock:
            stats = dict(self._stats)

            # Calculate derived metrics
            if stats["start_time"] and stats["end_time"]:
                duration = (stats["end_time"] - stats["start_time"]).total_seconds()
                stats["duration_seconds"] = duration
                stats["logs_per_second"] = (
                    stats["processed"] / duration if duration > 0 else 0
                )

            if stats["processing_times"]:
                stats["avg_processing_time"] = sum(stats["processing_times"]) / len(
                    stats["processing_times"]
                )
                stats["min_processing_time"] = min(stats["processing_times"])
                stats["max_processing_time"] = max(stats["processing_times"])
            else:
                stats["avg_processing_time"] = 0
                stats["min_processing_time"] = 0
                stats["max_processing_time"] = 0

            # Remove internal data
            stats.pop("processing_times", None)

            # Format timestamps
            if stats["start_time"]:
                stats["start_time"] = stats["start_time"].isoformat()
            if stats["end_time"]:
                stats["end_time"] = stats["end_time"].isoformat()

            return stats

    async def log_progress(self):
        """Log current progress."""
        async with self._lock:
            processed = self._stats["processed"]
            total = self._stats["total_logs"]
            percent = (processed / total * 100) if total > 0 else 0

            log_info(
                "Async processing progress",
                processed=processed,
                total=total,
                percent=f"{percent:.1f}%",
                tickets_created=self._stats["tickets_created"],
                duplicates=self._stats["duplicates"],
                errors=self._stats["errors"],
            )


class RateLimiter:
    """Thread-safe rate limiter for API calls."""

    def __init__(self, max_calls: int, time_window: float):
        """Initialize rate limiter.

        Args:
            max_calls: Maximum calls allowed in time window
            time_window: Time window in seconds
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self._calls = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until rate limit allows another call."""
        async with self._lock:
            now = datetime.now().timestamp()

            # Remove old calls outside time window
            self._calls = [t for t in self._calls if now - t < self.time_window]

            # If at limit, wait
            if len(self._calls) >= self.max_calls:
                oldest_call = min(self._calls)
                sleep_time = self.time_window - (now - oldest_call)
                if sleep_time > 0:
                    log_debug("Rate limit reached, waiting", sleep_seconds=sleep_time)
                    await asyncio.sleep(sleep_time)

            # Record this call
            self._calls.append(now)

    async def reset(self):
        """Reset rate limiter."""
        async with self._lock:
            self._calls = []
