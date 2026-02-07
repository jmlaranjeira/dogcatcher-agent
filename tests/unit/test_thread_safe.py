"""Unit tests for thread-safe utilities."""

import pytest
import asyncio
import time
from agent.utils.thread_safe import (
    ThreadSafeSet,
    ThreadSafeCounter,
    ThreadSafeDeduplicator,
    ProcessingStats,
    RateLimiter,
)


class TestThreadSafeSet:
    """Test thread-safe set operations."""

    @pytest.mark.asyncio
    async def test_add_unique_items(self):
        """Test adding unique items."""
        ts_set = ThreadSafeSet()

        result1 = await ts_set.add("item1")
        result2 = await ts_set.add("item2")
        result3 = await ts_set.add("item1")  # Duplicate

        assert result1 is True
        assert result2 is True
        assert result3 is False  # Already existed

    @pytest.mark.asyncio
    async def test_contains(self):
        """Test contains operation."""
        ts_set = ThreadSafeSet()

        await ts_set.add("item1")
        assert await ts_set.contains("item1") is True
        assert await ts_set.contains("item2") is False

    @pytest.mark.asyncio
    async def test_size(self):
        """Test size tracking."""
        ts_set = ThreadSafeSet()

        assert await ts_set.size() == 0

        await ts_set.add("item1")
        await ts_set.add("item2")
        assert await ts_set.size() == 2

        await ts_set.add("item1")  # Duplicate shouldn't increase size
        assert await ts_set.size() == 2

    @pytest.mark.asyncio
    async def test_concurrent_adds(self):
        """Test concurrent additions are thread-safe."""
        ts_set = ThreadSafeSet()

        # Add same item concurrently
        tasks = [ts_set.add("same_item") for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # Only one should return True
        assert sum(results) == 1
        assert await ts_set.size() == 1


class TestThreadSafeCounter:
    """Test thread-safe counter."""

    @pytest.mark.asyncio
    async def test_increment(self):
        """Test incrementing counter."""
        counter = ThreadSafeCounter()

        value1 = await counter.increment()
        value2 = await counter.increment()
        value3 = await counter.increment(5)

        assert value1 == 1
        assert value2 == 2
        assert value3 == 7

    @pytest.mark.asyncio
    async def test_get(self):
        """Test getting current value."""
        counter = ThreadSafeCounter()

        assert await counter.get() == 0

        await counter.increment()
        assert await counter.get() == 1

    @pytest.mark.asyncio
    async def test_reset(self):
        """Test resetting counter."""
        counter = ThreadSafeCounter()

        await counter.increment(10)
        assert await counter.get() == 10

        await counter.reset()
        assert await counter.get() == 0

    @pytest.mark.asyncio
    async def test_concurrent_increments(self):
        """Test concurrent increments are atomic."""
        counter = ThreadSafeCounter()

        # Increment concurrently 100 times
        tasks = [counter.increment() for _ in range(100)]
        await asyncio.gather(*tasks)

        # Final value should be exactly 100
        assert await counter.get() == 100


class TestThreadSafeDeduplicator:
    """Test thread-safe log deduplication."""

    @pytest.mark.asyncio
    async def test_is_duplicate_first_log(self):
        """Test first occurrence is not a duplicate."""
        dedup = ThreadSafeDeduplicator()

        is_dup = await dedup.is_duplicate("log_key_1")

        assert is_dup is False

    @pytest.mark.asyncio
    async def test_is_duplicate_second_occurrence(self):
        """Test second occurrence is detected as duplicate."""
        dedup = ThreadSafeDeduplicator()

        await dedup.is_duplicate("log_key_1")
        is_dup = await dedup.is_duplicate("log_key_1")

        assert is_dup is True

    @pytest.mark.asyncio
    async def test_different_logs_not_duplicates(self):
        """Test different logs are not duplicates."""
        dedup = ThreadSafeDeduplicator()

        await dedup.is_duplicate("log_key_1")
        is_dup = await dedup.is_duplicate("log_key_2")

        assert is_dup is False

    @pytest.mark.asyncio
    async def test_fingerprint_tracking(self):
        """Test fingerprint creation tracking."""
        dedup = ThreadSafeDeduplicator()

        # First time marking fingerprint
        result1 = await dedup.mark_fingerprint_created("fingerprint_1")
        assert result1 is True

        # Second time marking same fingerprint
        result2 = await dedup.mark_fingerprint_created("fingerprint_1")
        assert result2 is False

        # Check has_fingerprint
        assert await dedup.has_fingerprint("fingerprint_1") is True
        assert await dedup.has_fingerprint("fingerprint_2") is False

    @pytest.mark.asyncio
    async def test_statistics_tracking(self):
        """Test statistics are tracked correctly."""
        dedup = ThreadSafeDeduplicator()

        await dedup.is_duplicate("log1")  # Unique
        await dedup.is_duplicate("log2")  # Unique
        await dedup.is_duplicate("log1")  # Duplicate

        stats = await dedup.get_stats()

        assert stats["total_checked"] == 3
        assert stats["unique_logs"] == 2
        assert stats["duplicates_found"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_deduplication(self):
        """Test concurrent deduplication is thread-safe."""
        dedup = ThreadSafeDeduplicator()

        # Check same log concurrently
        tasks = [dedup.is_duplicate("same_log") for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # Only first should be False (not duplicate), rest should be True
        assert results.count(False) == 1
        assert results.count(True) == 9


class TestProcessingStats:
    """Test processing statistics tracking."""

    @pytest.mark.asyncio
    async def test_record_times(self):
        """Test recording start and end times."""
        stats = ProcessingStats()

        await stats.record_start()
        await asyncio.sleep(0.1)
        await stats.record_end()

        summary = await stats.get_summary()

        assert summary["start_time"] is not None
        assert summary["end_time"] is not None
        assert summary["duration_seconds"] >= 0.1

    @pytest.mark.asyncio
    async def test_record_log_processed(self):
        """Test recording log processing."""
        stats = ProcessingStats()

        await stats.record_log_processed(0.5)
        await stats.record_log_processed(1.0)

        summary = await stats.get_summary()

        assert summary["processed"] == 2
        assert summary["avg_processing_time"] == 0.75
        assert summary["min_processing_time"] == 0.5
        assert summary["max_processing_time"] == 1.0

    @pytest.mark.asyncio
    async def test_record_tickets(self):
        """Test recording ticket creation."""
        stats = ProcessingStats()

        await stats.record_ticket_created()
        await stats.record_ticket_created()
        await stats.record_ticket_simulated()

        summary = await stats.get_summary()

        assert summary["tickets_created"] == 2
        assert summary["tickets_simulated"] == 1

    @pytest.mark.asyncio
    async def test_record_duplicates_and_errors(self):
        """Test recording duplicates and errors."""
        stats = ProcessingStats()

        await stats.record_duplicate()
        await stats.record_duplicate()
        await stats.record_error()

        summary = await stats.get_summary()

        assert summary["duplicates"] == 2
        assert summary["errors"] == 1

    @pytest.mark.asyncio
    async def test_logs_per_second_calculation(self):
        """Test logs per second calculation."""
        stats = ProcessingStats()

        await stats.set_total_logs(10)
        await stats.record_start()

        for _ in range(10):
            await stats.record_log_processed(0.01)

        await asyncio.sleep(0.1)
        await stats.record_end()

        summary = await stats.get_summary()

        assert summary["processed"] == 10
        assert summary["logs_per_second"] > 0


class TestRateLimiter:
    """Test rate limiter functionality."""

    @pytest.mark.asyncio
    async def test_rate_limit_allows_calls_under_limit(self):
        """Test calls are allowed under the limit."""
        limiter = RateLimiter(max_calls=5, time_window=1.0)

        start = time.time()

        # Should allow 5 calls quickly
        for _ in range(5):
            await limiter.acquire()

        elapsed = time.time() - start

        # Should be fast (< 0.1s)
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_rate_limit_delays_over_limit(self):
        """Test rate limiter delays calls over the limit."""
        limiter = RateLimiter(max_calls=3, time_window=0.5)

        start = time.time()

        # First 3 should be fast
        for _ in range(3):
            await limiter.acquire()

        # 4th should wait
        await limiter.acquire()

        elapsed = time.time() - start

        # Should have waited at least 0.5s
        assert elapsed >= 0.4  # Allow small margin

    @pytest.mark.asyncio
    async def test_reset(self):
        """Test resetting rate limiter."""
        limiter = RateLimiter(max_calls=2, time_window=1.0)

        # Hit limit
        await limiter.acquire()
        await limiter.acquire()

        # Reset
        await limiter.reset()

        # Should allow calls immediately
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed < 0.1  # Should be fast after reset


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
