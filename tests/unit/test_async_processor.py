"""Unit tests for async log processor.

Tests concurrent processing, error isolation, statistics tracking,
and integration with async Jira client.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from agent.async_processor import AsyncLogProcessor, process_logs_parallel


@pytest.fixture
def sample_logs():
    """Generate sample log data for testing."""
    return [
        {
            "message": "Error connecting to database",
            "logger": "database.connection",
            "status": "error",
            "timestamp": "2025-01-01T10:00:00Z"
        },
        {
            "message": "API timeout after 30 seconds",
            "logger": "api.client",
            "status": "error",
            "timestamp": "2025-01-01T10:01:00Z"
        },
        {
            "message": "Null pointer exception in handler",
            "logger": "request.handler",
            "status": "error",
            "timestamp": "2025-01-01T10:02:00Z"
        }
    ]


@pytest.fixture
def sample_analysis():
    """Sample LLM analysis result."""
    return {
        "error_type": "database-connection",
        "summary": "Database Connection Error",
        "description": "Error connecting to database",
        "severity": "high",
        "create_ticket": True,
        "fingerprint": "test-fingerprint-123"
    }


class TestAsyncLogProcessorInit:
    """Test AsyncLogProcessor initialization."""

    @pytest.mark.asyncio
    async def test_initialization_default(self):
        """Test processor initializes with default values."""
        processor = AsyncLogProcessor()

        assert processor.max_workers == 5
        assert processor.semaphore._value == 5
        assert processor.deduplicator is not None
        assert processor.stats is not None
        assert processor.rate_limiter is not None

    @pytest.mark.asyncio
    async def test_initialization_custom_workers(self):
        """Test processor initializes with custom worker count."""
        processor = AsyncLogProcessor(max_workers=3)

        assert processor.max_workers == 3
        assert processor.semaphore._value == 3

    @pytest.mark.asyncio
    async def test_initialization_no_rate_limiting(self):
        """Test processor initializes without rate limiting."""
        processor = AsyncLogProcessor(enable_rate_limiting=False)

        assert processor.rate_limiter is None


class TestAsyncLogProcessorBasic:
    """Test basic async processor operations."""

    @pytest.mark.asyncio
    async def test_process_empty_logs(self):
        """Test processing empty log list."""
        processor = AsyncLogProcessor()
        result = await processor.process_logs([])

        assert result["processed"] == 0
        assert result["results"] == []
        # Empty logs return early, so other fields are not present

    @pytest.mark.asyncio
    async def test_process_single_log_duplicate(self, sample_logs):
        """Test processing single log detected as duplicate."""
        processor = AsyncLogProcessor()

        # Mock the deduplicator to return duplicate
        with patch.object(processor.deduplicator, 'is_duplicate', return_value=True):
            result = await processor.process_logs([sample_logs[0]])

        assert result["processed"] == 1
        assert result["successful"] == 1
        assert result["errors"] == 0
        # Check duplicate was recorded
        stats = await processor.stats.get_summary()
        assert stats["duplicates"] >= 1

    @pytest.mark.asyncio
    async def test_log_key_generation(self, sample_logs):
        """Test log key generation for deduplication."""
        processor = AsyncLogProcessor()

        log = sample_logs[0]
        key = processor._generate_log_key(log)

        assert "database.connection" in key
        assert len(key) > 0

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_detection(self):
        """Test that concurrent logs with same key are deduplicated."""
        processor = AsyncLogProcessor(max_workers=3)

        # Create 5 identical logs
        identical_logs = [
            {
                "message": "Same error message",
                "logger": "same.logger",
                "status": "error"
            }
        ] * 5

        with patch.object(processor, '_analyze_log_async', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = {"create_ticket": False}

            result = await processor.process_logs(identical_logs)

        # Only first should be analyzed, rest should be duplicates
        assert mock_analyze.call_count == 1
        stats = await processor.stats.get_summary()
        assert stats["duplicates"] == 4


class TestAsyncLogProcessorConcurrency:
    """Test concurrent processing behavior."""

    @pytest.mark.asyncio
    async def test_concurrent_processing_with_semaphore(self, sample_logs):
        """Test that semaphore limits concurrent processing."""
        processor = AsyncLogProcessor(max_workers=2)

        # Track concurrent executions
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def mock_analyze(log):
            nonlocal concurrent_count, max_concurrent

            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)

            await asyncio.sleep(0.1)  # Simulate work

            async with lock:
                concurrent_count -= 1

            return {"create_ticket": False}

        with patch.object(processor, '_analyze_log_async', side_effect=mock_analyze):
            with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                await processor.process_logs(sample_logs)

        # Should never exceed max_workers
        assert max_concurrent <= processor.max_workers

    @pytest.mark.asyncio
    async def test_parallel_processing_faster_than_sequential(self, sample_logs):
        """Test that parallel processing is faster than sequential."""
        processor = AsyncLogProcessor(max_workers=3)

        async def slow_analyze(log):
            await asyncio.sleep(0.1)
            return {"create_ticket": False}

        with patch.object(processor, '_analyze_log_async', side_effect=slow_analyze):
            with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                import time
                start = time.time()
                await processor.process_logs(sample_logs)
                duration = time.time() - start

        # With 3 logs and 3 workers, should take ~0.1s (parallel)
        # Sequential would take ~0.3s
        assert duration < 0.25  # Allow some overhead


class TestAsyncLogProcessorErrorHandling:
    """Test error handling and isolation."""

    @pytest.mark.asyncio
    async def test_error_isolation(self, sample_logs):
        """Test that one log failure doesn't stop others."""
        processor = AsyncLogProcessor(max_workers=3)

        call_count = 0

        async def mock_analyze(log):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("Simulated error")
            return {"create_ticket": False}

        with patch.object(processor, '_analyze_log_async', side_effect=mock_analyze):
            with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                result = await processor.process_logs(sample_logs)

        # Should process all 3, with 1 error
        assert result["processed"] == 3
        assert result["successful"] == 2
        assert result["errors"] == 1
        assert len(result["error_details"]) == 1

    @pytest.mark.asyncio
    async def test_exception_types_preserved(self, sample_logs):
        """Test that exception details are captured."""
        processor = AsyncLogProcessor()

        async def mock_analyze(log):
            raise RuntimeError("Custom error message")

        with patch.object(processor, '_analyze_log_async', side_effect=mock_analyze):
            with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                result = await processor.process_logs([sample_logs[0]])

        assert result["errors"] == 1
        assert "Custom error message" in result["error_details"][0]["error"]


class TestAsyncLogProcessorStatistics:
    """Test statistics tracking."""

    @pytest.mark.asyncio
    async def test_statistics_tracking(self, sample_logs):
        """Test that statistics are tracked correctly."""
        processor = AsyncLogProcessor(max_workers=2)

        with patch.object(processor, '_analyze_log_async', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = {"create_ticket": False}
            with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                await processor.process_logs(sample_logs)

        summary = await processor.get_summary()

        assert summary["processed"] == len(sample_logs)
        assert summary["start_time"] is not None
        assert summary["end_time"] is not None
        assert summary["duration_seconds"] > 0

    @pytest.mark.asyncio
    async def test_duplicate_statistics(self):
        """Test duplicate statistics tracking."""
        processor = AsyncLogProcessor()

        # First log: not duplicate
        # Second log: duplicate
        dup_results = [False, True]
        dup_index = 0

        async def mock_is_dup(key):
            nonlocal dup_index
            result = dup_results[dup_index]
            dup_index += 1
            return result

        logs = [
            {"message": "Error 1", "logger": "test"},
            {"message": "Error 2", "logger": "test"}
        ]

        with patch.object(processor.deduplicator, 'is_duplicate', side_effect=mock_is_dup):
            with patch.object(processor, '_analyze_log_async', new_callable=AsyncMock) as mock_analyze:
                mock_analyze.return_value = {"create_ticket": False}
                await processor.process_logs(logs)

        stats = await processor.stats.get_summary()
        assert stats["duplicates"] == 1


class TestAsyncLogProcessorRateLimiting:
    """Test rate limiting functionality."""

    @pytest.mark.asyncio
    async def test_rate_limiting_enabled(self, sample_logs):
        """Test that rate limiter is called when enabled."""
        processor = AsyncLogProcessor(enable_rate_limiting=True)

        with patch.object(processor.rate_limiter, 'acquire', new_callable=AsyncMock) as mock_acquire:
            with patch.object(processor, '_analyze_log_async', new_callable=AsyncMock) as mock_analyze:
                mock_analyze.return_value = {"create_ticket": False}
                with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                    await processor.process_logs(sample_logs)

        # Rate limiter should be called for each log
        assert mock_acquire.call_count == len(sample_logs)

    @pytest.mark.asyncio
    async def test_rate_limiting_disabled(self, sample_logs):
        """Test that rate limiter is not called when disabled."""
        processor = AsyncLogProcessor(enable_rate_limiting=False)

        with patch.object(processor, '_analyze_log_async', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = {"create_ticket": False}
            with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                await processor.process_logs(sample_logs)

        # No rate limiter, so no errors
        assert processor.rate_limiter is None


class TestAsyncLogProcessorIntegration:
    """Test integration with Jira async client."""

    @pytest.mark.asyncio
    async def test_ticket_creation_integration(self, sample_logs, sample_analysis):
        """Test integration with async Jira client for ticket creation."""
        processor = AsyncLogProcessor()

        # Mock analysis to return create_ticket=True
        with patch.object(processor, '_analyze_log_async', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = sample_analysis

            # Mock the ticket creation handler
            with patch.object(processor, '_handle_ticket_creation', new_callable=AsyncMock) as mock_ticket:
                mock_ticket.return_value = {
                    "action": "simulated",
                    "ticket_key": None,
                    "decision": "dry_run",
                    "reason": "Dry-run mode"
                }

                with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                    result = await processor.process_logs([sample_logs[0]])

        # Should have called ticket creation
        assert mock_ticket.call_count == 1
        assert result["successful"] == 1

    @pytest.mark.asyncio
    async def test_duplicate_ticket_handling(self, sample_logs, sample_analysis):
        """Test handling of duplicate ticket detection."""
        processor = AsyncLogProcessor()

        with patch.object(processor, '_analyze_log_async', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = sample_analysis

            with patch.object(processor, '_handle_ticket_creation', new_callable=AsyncMock) as mock_ticket:
                mock_ticket.return_value = {
                    "action": "duplicate",
                    "ticket_key": "PROJ-123",
                    "decision": "similar_found",
                    "reason": "Similar ticket found"
                }

                with patch.object(processor.deduplicator, 'is_duplicate', return_value=False):
                    result = await processor.process_logs([sample_logs[0]])

        assert result["successful"] == 1
        assert result["results"][0]["action"] == "duplicate"


class TestProcessLogsParallelConvenience:
    """Test the convenience function."""

    @pytest.mark.asyncio
    async def test_convenience_function(self, sample_logs):
        """Test process_logs_parallel convenience function."""
        with patch('agent.async_processor.AsyncLogProcessor') as MockProcessor:
            mock_processor = AsyncMock()
            mock_processor.process_logs.return_value = {
                "processed": 3,
                "successful": 3,
                "errors": 0
            }
            MockProcessor.return_value = mock_processor

            result = await process_logs_parallel(sample_logs, max_workers=5)

        assert result["processed"] == 3
        MockProcessor.assert_called_once_with(max_workers=5, enable_rate_limiting=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
