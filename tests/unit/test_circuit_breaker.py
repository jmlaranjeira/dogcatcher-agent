"""Comprehensive unit tests for circuit breaker implementation."""

import pytest
import asyncio
import time
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

from agent.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    CircuitBreakerRegistry,
    get_circuit_breaker_registry
)


class TestCircuitBreakerConfig:
    """Test circuit breaker configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.timeout_seconds == 60
        assert config.half_open_max_calls == 3
        assert config.expected_exception == Exception
        assert config.name == "circuit_breaker"

    def test_custom_config(self):
        """Test custom configuration values."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=30,
            half_open_max_calls=2,
            expected_exception=ValueError,
            name="test_breaker"
        )
        assert config.failure_threshold == 3
        assert config.timeout_seconds == 30
        assert config.half_open_max_calls == 2
        assert config.expected_exception == ValueError
        assert config.name == "test_breaker"


class TestCircuitBreakerStateTransitions:
    """Test circuit breaker state transitions."""

    @pytest.fixture
    def breaker(self):
        """Create circuit breaker for testing."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=1,  # Short timeout for testing
            half_open_max_calls=2,
            name="test_breaker"
        )
        return CircuitBreaker(config)

    @pytest.mark.asyncio
    async def test_initial_state_is_closed(self, breaker):
        """Test initial state is CLOSED."""
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_successful_call_in_closed_state(self, breaker):
        """Test successful call in CLOSED state."""
        async def successful_func():
            return "success"

        result = await breaker.call(successful_func)
        assert result == "success"
        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.successful_calls == 1
        assert breaker.stats.failed_calls == 0

    @pytest.mark.asyncio
    async def test_failed_call_increments_failure_count(self, breaker):
        """Test failed call increments failure count."""
        async def failing_func():
            raise Exception("Test error")

        with pytest.raises(Exception):
            await breaker.call(failing_func)

        assert breaker.failure_count == 1
        assert breaker.stats.failed_calls == 1
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self, breaker):
        """Test circuit opens after reaching failure threshold."""
        async def failing_func():
            raise Exception("Test error")

        # Fail 3 times to reach threshold
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_calls(self, breaker):
        """Test OPEN circuit rejects calls immediately."""
        async def failing_func():
            raise Exception("Test error")

        # Open the circuit
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN

        # Next call should be rejected without execution
        async def should_not_execute():
            pytest.fail("This function should not be executed")

        with pytest.raises(CircuitBreakerOpenError):
            await breaker.call(should_not_execute)

        assert breaker.stats.rejected_calls == 1

    @pytest.mark.asyncio
    async def test_transition_to_half_open_after_timeout(self, breaker):
        """Test circuit transitions to HALF_OPEN after timeout."""
        async def failing_func():
            raise Exception("Test error")

        # Open the circuit
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN

        # Wait for timeout (1 second)
        await asyncio.sleep(1.1)

        # Next call should transition to HALF_OPEN
        async def test_func():
            return "test"

        result = await breaker.call(test_func)
        assert result == "test"
        assert breaker.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_closes_on_success(self, breaker):
        """Test HALF_OPEN closes after successful test calls."""
        async def failing_func():
            raise Exception("Test error")

        # Open the circuit
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        # Wait for timeout
        await asyncio.sleep(1.1)

        # Successful calls should close the circuit
        async def successful_func():
            return "success"

        # Need 2 successful calls (half_open_max_calls=2)
        await breaker.call(successful_func)
        assert breaker.state == CircuitState.HALF_OPEN

        await breaker.call(successful_func)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_half_open_reopens_on_failure(self, breaker):
        """Test HALF_OPEN reopens immediately on failure."""
        async def failing_func():
            raise Exception("Test error")

        # Open the circuit
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        # Wait for timeout
        await asyncio.sleep(1.1)

        # First call transitions to HALF_OPEN
        async def successful_func():
            return "success"
        await breaker.call(successful_func)
        assert breaker.state == CircuitState.HALF_OPEN

        # Failure should reopen circuit
        with pytest.raises(Exception):
            await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count_in_closed(self, breaker):
        """Test success resets failure count in CLOSED state."""
        async def failing_func():
            raise Exception("Test error")

        async def successful_func():
            return "success"

        # Fail twice
        for i in range(2):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        assert breaker.failure_count == 2

        # Success should reset count
        await breaker.call(successful_func)
        assert breaker.failure_count == 0
        assert breaker.state == CircuitState.CLOSED


class TestCircuitBreakerStatistics:
    """Test circuit breaker statistics tracking."""

    @pytest.fixture
    def breaker(self):
        """Create circuit breaker for testing."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=1,
            name="stats_breaker"
        )
        return CircuitBreaker(config)

    @pytest.mark.asyncio
    async def test_stats_track_calls(self, breaker):
        """Test statistics track all calls."""
        async def successful_func():
            return "success"

        async def failing_func():
            raise Exception("Test error")

        # Make some calls
        await breaker.call(successful_func)
        await breaker.call(successful_func)

        with pytest.raises(Exception):
            await breaker.call(failing_func)

        assert breaker.stats.total_calls == 3
        assert breaker.stats.successful_calls == 2
        assert breaker.stats.failed_calls == 1

    @pytest.mark.asyncio
    async def test_stats_track_rejections(self, breaker):
        """Test statistics track rejected calls."""
        async def failing_func():
            raise Exception("Test error")

        # Open the circuit
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        # Try to make calls while open
        async def should_not_execute():
            pass

        for i in range(2):
            with pytest.raises(CircuitBreakerOpenError):
                await breaker.call(should_not_execute)

        assert breaker.stats.rejected_calls == 2

    @pytest.mark.asyncio
    async def test_stats_success_rate(self, breaker):
        """Test success rate calculation."""
        async def successful_func():
            return "success"

        async def failing_func():
            raise Exception("Test error")

        # 3 successes, 2 failures = 60% success rate
        await breaker.call(successful_func)
        await breaker.call(successful_func)
        await breaker.call(successful_func)

        with pytest.raises(Exception):
            await breaker.call(failing_func)

        with pytest.raises(Exception):
            await breaker.call(failing_func)

        assert breaker.stats.successful_calls == 3
        assert breaker.stats.failed_calls == 2
        assert breaker.stats.success_rate == 60.0
        assert breaker.stats.failure_rate == 40.0

    @pytest.mark.asyncio
    async def test_get_stats_returns_complete_info(self, breaker):
        """Test get_stats returns complete information."""
        async def successful_func():
            return "success"

        await breaker.call(successful_func)

        stats = breaker.get_stats()

        assert stats["name"] == "stats_breaker"
        assert stats["state"] == CircuitState.CLOSED.value
        assert "stats" in stats
        assert "config" in stats
        assert stats["stats"]["successful_calls"] == 1
        assert stats["config"]["failure_threshold"] == 3


class TestCircuitBreakerRegistry:
    """Test circuit breaker registry."""

    @pytest.fixture
    def registry(self):
        """Create fresh registry for testing."""
        return CircuitBreakerRegistry()

    def test_register_circuit_breaker(self, registry):
        """Test registering a circuit breaker."""
        config = CircuitBreakerConfig(name="test_breaker")
        breaker = registry.register("test_breaker", config)

        assert breaker is not None
        assert breaker.config.name == "test_breaker"

    def test_get_circuit_breaker(self, registry):
        """Test getting a registered circuit breaker."""
        config = CircuitBreakerConfig(name="test_breaker")
        original = registry.register("test_breaker", config)

        retrieved = registry.get("test_breaker")

        assert retrieved is original

    def test_get_nonexistent_circuit_breaker(self, registry):
        """Test getting non-existent circuit breaker returns None."""
        result = registry.get("nonexistent")
        assert result is None

    def test_get_all_stats(self, registry):
        """Test getting statistics for all circuit breakers."""
        config1 = CircuitBreakerConfig(name="breaker1")
        config2 = CircuitBreakerConfig(name="breaker2")

        registry.register("breaker1", config1)
        registry.register("breaker2", config2)

        all_stats = registry.get_all_stats()

        assert "breaker1" in all_stats
        assert "breaker2" in all_stats

    @pytest.mark.asyncio
    async def test_reset_all(self, registry):
        """Test resetting all circuit breakers."""
        config = CircuitBreakerConfig(failure_threshold=2, name="test_breaker")
        breaker = registry.register("test_breaker", config)

        # Fail to open circuit
        async def failing_func():
            raise Exception("Test error")

        for i in range(2):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN

        # Reset all
        await registry.reset_all()

        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_health_check(self, registry):
        """Test health check of all circuit breakers."""
        config1 = CircuitBreakerConfig(name="healthy")
        config2 = CircuitBreakerConfig(name="unhealthy")

        breaker1 = registry.register("healthy", config1)
        breaker2 = registry.register("unhealthy", config2)

        # Force one breaker open
        breaker2.state = CircuitState.OPEN

        health = registry.health_check()

        assert health["total_breakers"] == 2
        assert health["healthy_count"] == 1
        assert health["overall_health"] is False
        assert health["breakers"]["healthy"]["healthy"] is True
        assert health["breakers"]["unhealthy"]["healthy"] is False


class TestCircuitBreakerManualControl:
    """Test manual circuit breaker control."""

    @pytest.fixture
    def breaker(self):
        """Create circuit breaker for testing."""
        config = CircuitBreakerConfig(name="manual_breaker")
        return CircuitBreaker(config)

    @pytest.mark.asyncio
    async def test_manual_reset(self, breaker):
        """Test manual reset of circuit breaker."""
        async def failing_func():
            raise Exception("Test error")

        # Open the circuit
        for i in range(5):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN

        # Manual reset
        await breaker.reset()

        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_force_open(self, breaker):
        """Test forcing circuit breaker open."""
        assert breaker.state == CircuitState.CLOSED

        await breaker.force_open()

        assert breaker.state == CircuitState.OPEN

        # Should reject calls
        async def should_not_execute():
            pytest.fail("Should not execute")

        with pytest.raises(CircuitBreakerOpenError):
            await breaker.call(should_not_execute)

    def test_is_call_permitted(self, breaker):
        """Test checking if calls are permitted."""
        # Should be permitted in CLOSED state
        assert breaker.is_call_permitted() is True

        # Force open
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = time.time()

        assert breaker.is_call_permitted() is False

        # After timeout should be permitted
        breaker.last_failure_time = time.time() - 100  # 100 seconds ago
        assert breaker.is_call_permitted() is True


class TestCircuitBreakerSyncAsyncSupport:
    """Test circuit breaker works with both sync and async functions."""

    @pytest.fixture
    def breaker(self):
        """Create circuit breaker for testing."""
        config = CircuitBreakerConfig(name="sync_async_breaker")
        return CircuitBreaker(config)

    @pytest.mark.asyncio
    async def test_async_function_support(self, breaker):
        """Test circuit breaker works with async functions."""
        async def async_func():
            return "async_result"

        result = await breaker.call(async_func)
        assert result == "async_result"

    @pytest.mark.asyncio
    async def test_sync_function_support(self, breaker):
        """Test circuit breaker works with sync functions."""
        def sync_func():
            return "sync_result"

        result = await breaker.call(sync_func)
        assert result == "sync_result"


class TestCircuitBreakerEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.fixture
    def breaker(self):
        """Create circuit breaker for testing."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=1,
            name="edge_case_breaker"
        )
        return CircuitBreaker(config)

    @pytest.mark.asyncio
    async def test_unexpected_exception_not_counted(self, breaker):
        """Test unexpected exceptions are not counted as circuit breaker failures."""
        # Configure to expect ValueError only
        breaker.config.expected_exception = ValueError

        async def throws_runtime_error():
            raise RuntimeError("Unexpected error")

        # RuntimeError should not count toward circuit breaker
        with pytest.raises(RuntimeError):
            await breaker.call(throws_runtime_error)

        # Should still be closed and no failures counted
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_expected_exception_counted(self, breaker):
        """Test expected exceptions are counted as failures."""
        # Default expected_exception is Exception (catches all)
        async def throws_value_error():
            raise ValueError("Expected error")

        with pytest.raises(ValueError):
            await breaker.call(throws_value_error)

        # Should count as failure
        assert breaker.failure_count == 1
        assert breaker.stats.failed_calls == 1

    @pytest.mark.asyncio
    async def test_half_open_call_limit(self, breaker):
        """Test HALF_OPEN state limits number of calls."""
        async def failing_func():
            raise Exception("Test error")

        # Open circuit
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        # Wait for timeout
        await asyncio.sleep(1.1)

        # Transition to HALF_OPEN
        async def successful_func():
            return "success"
        await breaker.call(successful_func)

        assert breaker.state == CircuitState.HALF_OPEN
        assert breaker.half_open_calls == 1

        # Should allow up to half_open_max_calls (3)
        await breaker.call(successful_func)
        assert breaker.half_open_calls == 2

        # Third call should close circuit
        await breaker.call(successful_func)
        assert breaker.state == CircuitState.CLOSED


class TestGlobalRegistry:
    """Test global registry singleton."""

    def test_get_global_registry(self):
        """Test getting global registry."""
        registry1 = get_circuit_breaker_registry()
        registry2 = get_circuit_breaker_registry()

        # Should return same instance
        assert registry1 is registry2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
