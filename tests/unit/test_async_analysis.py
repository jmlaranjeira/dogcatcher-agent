"""Unit tests for async LLM analysis module.

Tests async LLM analysis operations, circuit breaker integration,
fallback analysis, and error handling.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from agent.run_config import RunConfig
from agent.nodes.analysis_async import (
    analyze_log_async,
    analyze_logs_batch_async,
    _call_llm_async,
    _use_fallback_analysis_async,
)


@pytest.fixture
def mock_config():
    """Mock configuration for analysis."""
    config = MagicMock()
    config.circuit_breaker_enabled = True
    config.circuit_breaker_failure_threshold = 5
    config.circuit_breaker_timeout_seconds = 60
    config.circuit_breaker_half_open_calls = 3
    config.fallback_analysis_enabled = True
    return config


@pytest.fixture
def sample_log_data():
    """Sample log data for testing."""
    return {
        "message": "NullPointerException in UserService.findById()",
        "logger": "com.example.UserService",
        "thread": "http-nio-8080-exec-1",
        "detail": "java.lang.NullPointerException: user not found",
        "timestamp": "2025-01-01T10:00:00Z",
    }


@pytest.fixture
def sample_state(sample_log_data):
    """Sample state for testing."""
    return {
        "run_config": RunConfig(
            circuit_breaker_enabled=True,
            fallback_analysis_enabled=True,
        ),
        "log_data": sample_log_data,
        "log_message": sample_log_data["message"],
    }


@pytest.fixture
def valid_llm_response():
    """Valid LLM response JSON."""
    return json.dumps(
        {
            "error_type": "null-pointer-exception",
            "create_ticket": True,
            "ticket_title": "Fix NullPointerException in UserService",
            "ticket_description": "## Problem\nNullPointerException when user not found.\n## Causes\n- Missing null check\n## Actions\n- Add null validation",
            "severity": "high",
        }
    )


class TestAnalyzeLogAsync:
    """Test async log analysis."""

    @pytest.mark.asyncio
    async def test_analyze_log_success(
        self, mock_config, sample_state, valid_llm_response
    ):
        """Test successful async log analysis."""
        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.return_value = valid_llm_response

                result = await analyze_log_async(sample_state)

        assert result["error_type"] == "null-pointer-exception"
        assert result["create_ticket"] is True
        assert result["severity"] == "high"
        assert "Fix NullPointerException" in result["ticket_title"]

    @pytest.mark.asyncio
    async def test_analyze_log_with_code_block(self, mock_config, sample_state):
        """Test analysis handles JSON wrapped in code block."""
        json_in_code_block = """```json
        {
            "error_type": "database-error",
            "create_ticket": true,
            "ticket_title": "Database connection error",
            "ticket_description": "Description here",
            "severity": "medium"
        }
        ```"""

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.return_value = json_in_code_block

                result = await analyze_log_async(sample_state)

        assert result["error_type"] == "database-error"
        assert result["create_ticket"] is True

    @pytest.mark.asyncio
    async def test_analyze_log_invalid_json(self, mock_config, sample_state):
        """Test analysis handles invalid JSON response."""
        mock_config.fallback_analysis_enabled = True

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.return_value = "This is not valid JSON"

                with patch(
                    "agent.nodes.analysis_async._use_fallback_analysis_async",
                    new_callable=AsyncMock,
                ) as mock_fallback:
                    mock_fallback.return_value = {
                        **sample_state,
                        "error_type": "unknown",
                        "create_ticket": False,
                        "severity": "low",
                    }

                    result = await analyze_log_async(sample_state)

        # Should have used fallback
        mock_fallback.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_log_missing_fields(self, mock_config, sample_state):
        """Test analysis handles missing required fields."""
        incomplete_response = json.dumps(
            {
                "error_type": "some-error"
                # Missing ticket_title and ticket_description
            }
        )

        mock_config.fallback_analysis_enabled = True

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.return_value = incomplete_response

                with patch(
                    "agent.nodes.analysis_async._use_fallback_analysis_async",
                    new_callable=AsyncMock,
                ) as mock_fallback:
                    mock_fallback.return_value = {
                        **sample_state,
                        "error_type": "unknown",
                        "create_ticket": False,
                    }

                    result = await analyze_log_async(sample_state)

        mock_fallback.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_log_empty_state(self, mock_config, valid_llm_response):
        """Test analysis with minimal state."""
        empty_state = {"log_data": {}, "log_message": ""}

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.return_value = valid_llm_response

                result = await analyze_log_async(empty_state)

        assert "error_type" in result


class TestAnalyzeLogAsyncCircuitBreaker:
    """Test circuit breaker integration."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_fallback(self, mock_config, sample_state):
        """Test fallback when circuit breaker is open."""
        from agent.utils.circuit_breaker import CircuitBreakerOpenError

        mock_config.fallback_analysis_enabled = True

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.side_effect = CircuitBreakerOpenError("Circuit breaker open")

                with patch(
                    "agent.nodes.analysis_async._use_fallback_analysis_async",
                    new_callable=AsyncMock,
                ) as mock_fallback:
                    mock_fallback.return_value = {
                        **sample_state,
                        "error_type": "fallback-error",
                        "create_ticket": False,
                        "severity": "low",
                    }

                    result = await analyze_log_async(sample_state)

        mock_fallback.assert_called_once()
        assert result["error_type"] == "fallback-error"

    @pytest.mark.asyncio
    async def test_circuit_breaker_disabled(
        self, mock_config, sample_state, valid_llm_response
    ):
        """Test analysis when circuit breaker is disabled."""
        mock_config.circuit_breaker_enabled = False

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch("agent.nodes.analysis_async._build_chain") as mock_build:
                mock_chain = MagicMock()
                mock_response = MagicMock()
                mock_response.content = valid_llm_response
                mock_chain.ainvoke = AsyncMock(return_value=mock_response)
                mock_build.return_value = mock_chain

                result = await analyze_log_async(sample_state)

        assert result["error_type"] == "null-pointer-exception"

    @pytest.mark.asyncio
    async def test_fallback_disabled_returns_error_state(
        self, mock_config, sample_state
    ):
        """Test error state when fallback is disabled."""
        from agent.utils.circuit_breaker import CircuitBreakerOpenError

        sample_state["run_config"] = RunConfig(
            circuit_breaker_enabled=True,
            fallback_analysis_enabled=False,
        )

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.side_effect = CircuitBreakerOpenError("Circuit breaker open")

                result = await analyze_log_async(sample_state)

        assert result["error_type"] == "llm-unavailable"
        assert result["create_ticket"] is False


class TestAnalyzeLogAsyncErrorHandling:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_with_fallback(self, mock_config, sample_state):
        """Test unexpected exception triggers fallback."""
        sample_state["run_config"] = RunConfig(
            circuit_breaker_enabled=True,
            fallback_analysis_enabled=True,
        )

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.side_effect = RuntimeError("Unexpected error")

                with patch(
                    "agent.nodes.analysis_async._use_fallback_analysis_async",
                    new_callable=AsyncMock,
                ) as mock_fallback:
                    mock_fallback.return_value = {
                        **sample_state,
                        "error_type": "fallback-error",
                        "create_ticket": False,
                    }

                    result = await analyze_log_async(sample_state)

        mock_fallback.assert_called_once()

    @pytest.mark.asyncio
    async def test_unexpected_exception_without_fallback(
        self, mock_config, sample_state
    ):
        """Test unexpected exception without fallback returns error state."""
        sample_state["run_config"] = RunConfig(
            circuit_breaker_enabled=True,
            fallback_analysis_enabled=False,
        )

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async._call_llm_async", new_callable=AsyncMock
            ) as mock_llm:
                mock_llm.side_effect = RuntimeError("Unexpected error")

                result = await analyze_log_async(sample_state)

        assert result["error_type"] == "analysis-error"
        assert result["create_ticket"] is False


class TestAnalyzeLogsBatchAsync:
    """Test batch async analysis."""

    @pytest.mark.asyncio
    async def test_batch_analysis_success(self, mock_config, valid_llm_response):
        """Test successful batch analysis."""
        logs = [
            {"message": "Error 1", "logger": "app.service1"},
            {"message": "Error 2", "logger": "app.service2"},
            {"message": "Error 3", "logger": "app.service3"},
        ]

        with patch(
            "agent.nodes.analysis_async.analyze_log_async", new_callable=AsyncMock
        ) as mock_analyze:
            mock_analyze.return_value = {
                "error_type": "test-error",
                "create_ticket": True,
                "severity": "medium",
            }

            results = await analyze_logs_batch_async(logs, max_concurrent=2)

        assert len(results) == 3
        assert mock_analyze.call_count == 3

    @pytest.mark.asyncio
    async def test_batch_analysis_partial_failure(self, mock_config):
        """Test batch analysis handles partial failures."""
        logs = [
            {"message": "Error 1", "logger": "app.service1"},
            {"message": "Error 2", "logger": "app.service2"},
            {"message": "Error 3", "logger": "app.service3"},
        ]

        call_count = 0

        async def mock_analyze(state):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Analysis failed")
            return {"error_type": "test-error", "create_ticket": True}

        with patch(
            "agent.nodes.analysis_async.analyze_log_async", side_effect=mock_analyze
        ):
            results = await analyze_logs_batch_async(logs)

        # All 3 should return results (1 with error state)
        assert len(results) == 3
        assert results[1]["error_type"] == "batch-analysis-error"
        assert results[1]["create_ticket"] is False

    @pytest.mark.asyncio
    async def test_batch_analysis_respects_concurrency(self, mock_config):
        """Test batch analysis respects max_concurrent limit."""
        import asyncio

        logs = [{"message": f"Error {i}", "logger": "app"} for i in range(5)]

        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def mock_analyze(state):
            nonlocal concurrent_count, max_concurrent

            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)

            await asyncio.sleep(0.05)

            async with lock:
                concurrent_count -= 1

            return {"error_type": "test", "create_ticket": False}

        with patch(
            "agent.nodes.analysis_async.analyze_log_async", side_effect=mock_analyze
        ):
            await analyze_logs_batch_async(logs, max_concurrent=2)

        assert max_concurrent <= 2


class TestUseFallbackAnalysisAsync:
    """Test fallback analysis function."""

    @pytest.mark.asyncio
    async def test_fallback_analysis_basic(self, sample_state, sample_log_data):
        """Test basic fallback analysis."""
        with patch(
            "agent.nodes.analysis_async.get_fallback_analyzer"
        ) as mock_get_analyzer:
            mock_analyzer = MagicMock()
            mock_analyzer.analyze_log.return_value = {
                "error_type": "rule-based-error",
                "create_ticket": False,
                "ticket_title": "Fallback title",
                "ticket_description": "Fallback description",
                "severity": "low",
            }
            mock_get_analyzer.return_value = mock_analyzer

            result = await _use_fallback_analysis_async(sample_state, sample_log_data)

        assert result["error_type"] == "rule-based-error"
        mock_analyzer.analyze_log.assert_called_once_with(sample_log_data)


class TestCallLlmAsync:
    """Test direct LLM calling function."""

    @pytest.mark.asyncio
    async def test_call_llm_circuit_breaker_disabled(
        self, mock_config, valid_llm_response
    ):
        """Test LLM call when circuit breaker is disabled."""
        mock_config.circuit_breaker_enabled = False

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch("agent.nodes.analysis_async._build_chain") as mock_build:
                mock_chain = MagicMock()
                mock_response = MagicMock()
                mock_response.content = valid_llm_response
                mock_chain.ainvoke = AsyncMock(return_value=mock_response)
                mock_build.return_value = mock_chain

                result = await _call_llm_async("Test log context")

        assert result == valid_llm_response

    @pytest.mark.asyncio
    async def test_call_llm_circuit_breaker_enabled(
        self, mock_config, valid_llm_response
    ):
        """Test LLM call with circuit breaker enabled."""
        mock_config.circuit_breaker_enabled = True

        with patch("agent.nodes.analysis_async.get_config", return_value=mock_config):
            with patch(
                "agent.nodes.analysis_async.get_circuit_breaker_registry"
            ) as mock_registry:
                mock_breaker = MagicMock()

                async def breaker_call(func):
                    return await func()

                mock_breaker.call = breaker_call
                mock_registry.return_value.get.return_value = mock_breaker

                with patch("agent.nodes.analysis_async._build_chain") as mock_build:
                    mock_chain = MagicMock()
                    mock_response = MagicMock()
                    mock_response.content = valid_llm_response
                    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
                    mock_build.return_value = mock_chain

                    result = await _call_llm_async("Test log context")

        assert result == valid_llm_response


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
