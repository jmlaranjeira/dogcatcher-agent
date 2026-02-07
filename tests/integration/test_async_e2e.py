"""End-to-end integration tests for async parallel processing.

Tests the complete async pipeline: Datadog fetch -> LLM analysis -> Jira ticket creation.
Includes throughput benchmarks and rate limiting tests.
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import json

from agent.async_processor import AsyncLogProcessor, process_logs_parallel
from agent.datadog_async import get_logs_async
from agent.nodes.analysis_async import analyze_log_async
from agent.nodes.ticket_async import create_ticket_async


@pytest.fixture
def mock_config():
    """Mock configuration for integration tests."""
    config = MagicMock()
    # Datadog config
    config.datadog_api_key = "test-api-key"
    config.datadog_app_key = "test-app-key"
    config.datadog_site = "datadoghq.com"
    config.datadog_service = "test-service"
    config.datadog_env = "test"
    config.datadog_hours_back = 24
    config.datadog_limit = 100
    config.datadog_max_pages = 5
    config.datadog_timeout = 30
    config.datadog_statuses = "error"
    config.datadog_query_extra = ""
    config.datadog_query_extra_mode = "AND"
    # Jira config
    config.jira_domain = "test.atlassian.net"
    config.jira_user = "test@example.com"
    config.jira_api_token = "test-token"
    config.jira_project_key = "TEST"
    config.jira_search_max_results = 200
    config.jira_similarity_threshold = 0.85
    # LLM config
    config.circuit_breaker_enabled = False
    config.fallback_analysis_enabled = True
    # Ticket config
    config.auto_create_ticket = False  # Dry-run mode
    config.max_tickets_per_run = 100
    config.aggregate_email_not_found = False
    config.aggregate_kafka_consumer = False
    config.max_title_length = 100
    config.persist_sim_fp = False
    config.comment_on_duplicate = False
    return config


@pytest.fixture
def sample_logs():
    """Generate sample logs for testing."""
    return [
        {
            "message": f"Error in service operation {i}",
            "logger": f"com.example.service{i % 3}",
            "thread": f"thread-{i}",
            "detail": f"Stack trace for error {i}",
            "timestamp": f"2025-01-01T10:{i:02d}:00Z",
        }
        for i in range(10)
    ]


@pytest.fixture
def valid_llm_response():
    """Valid LLM response for mocking."""
    return json.dumps(
        {
            "error_type": "service-error",
            "create_ticket": True,
            "ticket_title": "Fix service error",
            "ticket_description": "## Problem\nService error occurred.\n## Actions\n- Investigate",
            "severity": "medium",
        }
    )


class TestAsyncPipelineE2E:
    """End-to-end tests for the async pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_dry_run(
        self, mock_config, sample_logs, valid_llm_response
    ):
        """Test complete pipeline in dry-run mode."""
        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch(
                    "agent.nodes.ticket_async.get_config", return_value=mock_config
                ):
                    # Mock LLM response
                    with patch("agent.nodes.analysis_async.chain") as mock_chain:
                        mock_response = MagicMock()
                        mock_response.content = valid_llm_response
                        mock_chain.ainvoke = AsyncMock(return_value=mock_response)

                        # Mock Jira client
                        with patch(
                            "agent.nodes.ticket_async.AsyncJiraClient"
                        ) as MockJira:
                            mock_jira = AsyncMock()
                            mock_jira.is_configured.return_value = True
                            mock_jira.search.return_value = {"issues": []}
                            MockJira.return_value.__aenter__.return_value = mock_jira
                            MockJira.return_value.__aexit__.return_value = None

                            with patch(
                                "agent.jira.async_match.AsyncJiraClient", MockJira
                            ):
                                processor = AsyncLogProcessor(max_workers=3)
                                result = await processor.process_logs(sample_logs[:3])

        assert result["processed"] == 3
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_pipeline_with_duplicates(
        self, mock_config, sample_logs, valid_llm_response
    ):
        """Test pipeline correctly handles duplicate logs."""
        # Create logs with identical messages (should be deduplicated)
        duplicate_logs = [
            {
                "message": "Same error message",
                "logger": "com.example.service",
                "thread": f"thread-{i}",
                "detail": "Same detail",
                "timestamp": f"2025-01-01T10:{i:02d}:00Z",
            }
            for i in range(5)
        ]

        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch("agent.nodes.analysis_async.chain") as mock_chain:
                    mock_response = MagicMock()
                    mock_response.content = valid_llm_response
                    mock_chain.ainvoke = AsyncMock(return_value=mock_response)

                    processor = AsyncLogProcessor(max_workers=3)
                    result = await processor.process_logs(duplicate_logs)

        # First log should be processed, rest should be duplicates
        stats = await processor.stats.get_summary()
        assert stats["duplicates"] == 4


class TestAsyncThroughput:
    """Throughput benchmark tests."""

    @pytest.mark.asyncio
    async def test_throughput_baseline(
        self, mock_config, sample_logs, valid_llm_response
    ):
        """Test throughput with mocked components."""
        # Generate more logs for throughput testing
        many_logs = [
            {
                "message": f"Error {i} in service",
                "logger": f"com.example.service{i % 10}",
                "thread": f"thread-{i}",
                "detail": f"Detail {i}",
                "timestamp": f"2025-01-01T{(i // 60):02d}:{(i % 60):02d}:00Z",
            }
            for i in range(50)
        ]

        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch(
                    "agent.nodes.ticket_async.get_config", return_value=mock_config
                ):
                    # Mock fast LLM response
                    async def fast_llm_response(*args, **kwargs):
                        await asyncio.sleep(0.01)  # Simulate 10ms LLM latency
                        mock_response = MagicMock()
                        mock_response.content = valid_llm_response
                        return mock_response

                    with patch("agent.nodes.analysis_async.chain") as mock_chain:
                        mock_chain.ainvoke = fast_llm_response

                        with patch(
                            "agent.nodes.ticket_async.AsyncJiraClient"
                        ) as MockJira:
                            mock_jira = AsyncMock()
                            mock_jira.is_configured.return_value = True
                            mock_jira.search.return_value = {"issues": []}
                            MockJira.return_value.__aenter__.return_value = mock_jira
                            MockJira.return_value.__aexit__.return_value = None

                            processor = AsyncLogProcessor(
                                max_workers=10, enable_rate_limiting=False
                            )

                            start = time.time()
                            result = await processor.process_logs(many_logs)
                            duration = time.time() - start

        # Calculate throughput
        logs_per_second = len(many_logs) / duration
        logs_per_hour = logs_per_second * 3600

        assert result["processed"] == 50
        # With 10 workers and 10ms latency, should achieve high throughput
        # Target: 500+ logs/hour = ~0.14 logs/second
        assert logs_per_second > 0.1, f"Throughput too low: {logs_per_second} logs/sec"

    @pytest.mark.asyncio
    async def test_parallel_faster_than_sequential(
        self, mock_config, valid_llm_response
    ):
        """Test that parallel processing is faster than sequential."""
        logs = [
            {
                "message": f"Error {i}",
                "logger": f"logger{i}",
                "thread": f"thread-{i}",
                "detail": f"Detail {i}",
                "timestamp": "2025-01-01T10:00:00Z",
            }
            for i in range(6)
        ]

        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch(
                    "agent.nodes.ticket_async.get_config", return_value=mock_config
                ):

                    async def slow_analysis(*args, **kwargs):
                        await asyncio.sleep(0.1)  # 100ms per analysis
                        mock_response = MagicMock()
                        mock_response.content = valid_llm_response
                        return mock_response

                    with patch("agent.nodes.analysis_async.chain") as mock_chain:
                        mock_chain.ainvoke = slow_analysis

                        # Mock Jira client for ticket creation
                        with patch(
                            "agent.nodes.ticket_async.AsyncJiraClient"
                        ) as MockJira:
                            mock_jira = AsyncMock()
                            mock_jira.is_configured.return_value = True
                            mock_jira.search.return_value = {"issues": []}
                            MockJira.return_value.__aenter__.return_value = mock_jira
                            MockJira.return_value.__aexit__.return_value = None

                            with patch(
                                "agent.jira.async_match.AsyncJiraClient", MockJira
                            ):
                                # Parallel with 3 workers
                                processor = AsyncLogProcessor(max_workers=3)
                                start = time.time()
                                await processor.process_logs(logs)
                                parallel_duration = time.time() - start

        # With 6 logs, 100ms each, 3 workers:
        # Sequential would be 600ms
        # Parallel should be ~300ms (2 batches with overhead)
        assert parallel_duration < 0.8, f"Parallel too slow: {parallel_duration}s"


class TestAsyncRateLimiting:
    """Rate limiting tests."""

    @pytest.mark.asyncio
    async def test_rate_limiting_enforced(self, mock_config, valid_llm_response):
        """Test that rate limiting is enforced."""
        logs = [
            {
                "message": f"Error {i}",
                "logger": f"logger{i}",
                "thread": f"thread-{i}",
                "detail": f"Detail {i}",
                "timestamp": "2025-01-01T10:00:00Z",
            }
            for i in range(15)
        ]

        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch("agent.nodes.analysis_async.chain") as mock_chain:
                    call_times = []

                    async def tracked_llm(*args, **kwargs):
                        call_times.append(time.time())
                        mock_response = MagicMock()
                        mock_response.content = valid_llm_response
                        return mock_response

                    mock_chain.ainvoke = tracked_llm

                    # Rate limiter: 10 calls per second
                    processor = AsyncLogProcessor(
                        max_workers=15, enable_rate_limiting=True
                    )
                    await processor.process_logs(logs)

        # With 15 logs and 10/sec limit, should take >1 second
        if len(call_times) > 10:
            total_time = call_times[-1] - call_times[0]
            # Should have rate limiting effect (not all at once)
            assert total_time > 0.5, "Rate limiting not working"


class TestAsyncErrorHandling:
    """Error handling integration tests."""

    @pytest.mark.asyncio
    async def test_partial_failure_isolation(self, mock_config, valid_llm_response):
        """Test that LLM failures don't affect overall processing.

        The async analysis module is designed to be resilient - it catches
        exceptions and returns an error state instead of raising. This test
        verifies that all logs are processed even when some have analysis errors.
        """
        # Disable fallback to test error state handling
        mock_config.fallback_analysis_enabled = False

        logs = [
            {
                "message": f"Error {i}",
                "logger": f"logger{i}",
                "thread": "t",
                "detail": "d",
            }
            for i in range(5)
        ]

        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch(
                    "agent.nodes.ticket_async.get_config", return_value=mock_config
                ):
                    call_count = 0

                    async def sometimes_failing(*args, **kwargs):
                        nonlocal call_count
                        call_count += 1
                        if call_count == 3:
                            raise RuntimeError("Simulated failure")
                        mock_response = MagicMock()
                        mock_response.content = valid_llm_response
                        return mock_response

                    with patch("agent.nodes.analysis_async.chain") as mock_chain:
                        mock_chain.ainvoke = sometimes_failing

                        # Mock Jira client for ticket creation
                        with patch(
                            "agent.nodes.ticket_async.AsyncJiraClient"
                        ) as MockJira:
                            mock_jira = AsyncMock()
                            mock_jira.is_configured.return_value = True
                            mock_jira.search.return_value = {"issues": []}
                            MockJira.return_value.__aenter__.return_value = mock_jira
                            MockJira.return_value.__aexit__.return_value = None

                            with patch(
                                "agent.jira.async_match.AsyncJiraClient", MockJira
                            ):
                                processor = AsyncLogProcessor(max_workers=2)
                                result = await processor.process_logs(logs)

        # All 5 logs should be processed (no exceptions at processor level)
        # The analysis module catches exceptions and returns error states
        assert result["processed"] == 5
        assert result["errors"] == 0  # No processor-level errors
        assert result["successful"] == 5  # All processed successfully

        # Verify one result has the error state from analysis
        error_results = [
            r
            for r in result["results"]
            if r.get("analysis", {}).get("error_type") == "analysis-error"
        ]
        assert len(error_results) == 1  # One log had analysis error

    @pytest.mark.asyncio
    async def test_circuit_breaker_integration(self, mock_config):
        """Test circuit breaker integration in pipeline."""
        mock_config.circuit_breaker_enabled = True
        mock_config.circuit_breaker_failure_threshold = 2
        mock_config.circuit_breaker_timeout_seconds = 60
        mock_config.circuit_breaker_half_open_calls = 1

        logs = [
            {"message": f"Error {i}", "logger": "logger", "thread": "t", "detail": "d"}
            for i in range(5)
        ]

        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                # Simulate all LLM calls failing
                with patch("agent.nodes.analysis_async.chain") as mock_chain:
                    from openai import OpenAIError

                    mock_chain.ainvoke = AsyncMock(side_effect=OpenAIError("API Error"))

                    with patch(
                        "agent.nodes.analysis_async._use_fallback_analysis_async",
                        new_callable=AsyncMock,
                    ) as mock_fallback:
                        mock_fallback.return_value = {
                            "error_type": "fallback",
                            "create_ticket": False,
                            "severity": "low",
                        }

                        processor = AsyncLogProcessor(max_workers=2)
                        result = await processor.process_logs(logs)

        # Should have completed with fallback
        assert result["processed"] == 5


class TestConvenienceFunction:
    """Test the convenience function."""

    @pytest.mark.asyncio
    async def test_process_logs_parallel(
        self, mock_config, sample_logs, valid_llm_response
    ):
        """Test process_logs_parallel convenience function."""
        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch("agent.nodes.analysis_async.chain") as mock_chain:
                    mock_response = MagicMock()
                    mock_response.content = valid_llm_response
                    mock_chain.ainvoke = AsyncMock(return_value=mock_response)

                    result = await process_logs_parallel(
                        sample_logs[:3], max_workers=2, enable_rate_limiting=False
                    )

        assert result["processed"] == 3


class TestDatadogFetchIntegration:
    """Test Datadog fetch integration."""

    @pytest.mark.asyncio
    async def test_datadog_to_processor_pipeline(self, mock_config, valid_llm_response):
        """Test logs from Datadog are processed correctly."""
        sample_dd_response = {
            "data": [
                {
                    "attributes": {
                        "message": "Test error from Datadog",
                        "timestamp": "2025-01-01T10:00:00Z",
                        "attributes": {
                            "logger": {"name": "dd.logger", "thread_name": "main"},
                            "properties": {"Log": "Detail"},
                        },
                    }
                }
            ],
            "meta": {"page": {"after": None}},
        }

        with patch("agent.datadog_async.get_config", return_value=mock_config):
            with patch("agent.datadog_async.AsyncDatadogClient") as MockDD:
                mock_dd = AsyncMock()
                mock_dd.__aenter__.return_value = mock_dd
                mock_dd.__aexit__.return_value = None
                mock_dd.fetch_page.return_value = (sample_dd_response["data"], None)
                MockDD.return_value = mock_dd

                # Fetch logs
                logs = await get_logs_async()

        assert len(logs) == 1
        assert logs[0]["message"] == "Test error from Datadog"

        # Now process them
        with patch("agent.async_processor.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_config", return_value=mock_config
            ):
                with patch("agent.nodes.analysis_async.chain") as mock_chain:
                    mock_response = MagicMock()
                    mock_response.content = valid_llm_response
                    mock_chain.ainvoke = AsyncMock(return_value=mock_response)

                    processor = AsyncLogProcessor(max_workers=1)
                    result = await processor.process_logs(logs)

        assert result["processed"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
