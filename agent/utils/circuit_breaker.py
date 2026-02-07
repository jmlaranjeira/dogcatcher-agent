"""Circuit breaker pattern implementation for resilient external service calls.

This module provides circuit breaker functionality to handle failures gracefully
and automatically recover when services become available again.
"""

import asyncio
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional, Dict, List
from dataclasses import dataclass, field

from agent.utils.logger import log_info, log_warning, log_error, log_debug


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting calls
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerStats:
    """Circuit breaker statistics."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    state_changes: List[str] = field(default_factory=list)
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None

    @property
    def failure_rate(self) -> float:
        """Calculate failure rate percentage."""
        total = self.successful_calls + self.failed_calls
        return (self.failed_calls / total * 100) if total > 0 else 0.0

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        return 100.0 - self.failure_rate


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open."""

    def __init__(self, message: str = "Circuit breaker is open"):
        self.message = message
        super().__init__(self.message)


class CircuitBreakerConfig:
    """Circuit breaker configuration."""

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_seconds: int = 60,
        half_open_max_calls: int = 3,
        expected_exception: type = Exception,
        name: str = "circuit_breaker",
    ):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.half_open_max_calls = half_open_max_calls
        self.expected_exception = expected_exception
        self.name = name


class CircuitBreaker:
    """Circuit breaker implementation for resilient service calls."""

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0
        self.last_failure_time: Optional[float] = None
        self.stats = CircuitBreakerStats()
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            self.stats.total_calls += 1

            # Check if we should attempt the call
            if not await self._should_attempt_call():
                self.stats.rejected_calls += 1
                log_warning(
                    "Circuit breaker rejected call",
                    circuit_name=self.config.name,
                    state=self.state.value,
                    failure_count=self.failure_count,
                )
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.config.name}' is open"
                )

            try:
                # Execute the function
                log_debug(
                    "Circuit breaker executing call",
                    circuit_name=self.config.name,
                    state=self.state.value,
                )

                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)

                # Handle success
                await self._on_success()
                return result

            except self.config.expected_exception as e:
                # Handle expected failures
                await self._on_failure(e)
                raise

            except Exception as e:
                # Handle unexpected exceptions (don't count as circuit breaker failures)
                log_error(
                    "Unexpected exception in circuit breaker",
                    circuit_name=self.config.name,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                raise

    async def _should_attempt_call(self) -> bool:
        """Determine if a call should be attempted based on current state."""
        if self.state == CircuitState.CLOSED:
            return True

        elif self.state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if (
                self.last_failure_time
                and time.time() - self.last_failure_time >= self.config.timeout_seconds
            ):
                await self._transition_to_half_open()
                return True
            return False

        elif self.state == CircuitState.HALF_OPEN:
            # Allow limited calls in half-open state
            return self.half_open_calls < self.config.half_open_max_calls

        return False

    async def _on_success(self) -> None:
        """Handle successful call."""
        self.stats.successful_calls += 1
        self.stats.last_success_time = datetime.now()

        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1

            # If we've had enough successful calls, close the circuit
            if self.half_open_calls >= self.config.half_open_max_calls:
                await self._transition_to_closed()

        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success in closed state
            self.failure_count = 0

        log_debug(
            "Circuit breaker call succeeded",
            circuit_name=self.config.name,
            state=self.state.value,
            success_count=self.stats.successful_calls,
        )

    async def _on_failure(self, exception: Exception) -> None:
        """Handle failed call."""
        self.stats.failed_calls += 1
        self.stats.last_failure_time = datetime.now()
        self.last_failure_time = time.time()

        if self.state == CircuitState.CLOSED:
            self.failure_count += 1

            # Check if we should open the circuit
            if self.failure_count >= self.config.failure_threshold:
                await self._transition_to_open()

        elif self.state == CircuitState.HALF_OPEN:
            # Any failure in half-open state should open the circuit
            await self._transition_to_open()

        log_warning(
            "Circuit breaker call failed",
            circuit_name=self.config.name,
            state=self.state.value,
            failure_count=self.failure_count,
            error=str(exception),
        )

    async def _transition_to_open(self) -> None:
        """Transition circuit breaker to open state."""
        old_state = self.state
        self.state = CircuitState.OPEN
        self.half_open_calls = 0

        self.stats.state_changes.append(f"{old_state.value} -> {self.state.value}")

        log_warning(
            "Circuit breaker opened",
            circuit_name=self.config.name,
            failure_count=self.failure_count,
            threshold=self.config.failure_threshold,
            timeout_seconds=self.config.timeout_seconds,
        )

    async def _transition_to_half_open(self) -> None:
        """Transition circuit breaker to half-open state."""
        old_state = self.state
        self.state = CircuitState.HALF_OPEN
        self.half_open_calls = 0

        self.stats.state_changes.append(f"{old_state.value} -> {self.state.value}")

        log_info(
            "Circuit breaker transitioned to half-open",
            circuit_name=self.config.name,
            max_test_calls=self.config.half_open_max_calls,
        )

    async def _transition_to_closed(self) -> None:
        """Transition circuit breaker to closed state."""
        old_state = self.state
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0

        self.stats.state_changes.append(f"{old_state.value} -> {self.state.value}")

        log_info(
            "Circuit breaker closed - service recovered",
            circuit_name=self.config.name,
            test_calls_succeeded=self.config.half_open_max_calls,
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "name": self.config.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "half_open_calls": self.half_open_calls,
            "stats": {
                "total_calls": self.stats.total_calls,
                "successful_calls": self.stats.successful_calls,
                "failed_calls": self.stats.failed_calls,
                "rejected_calls": self.stats.rejected_calls,
                "success_rate_percent": round(self.stats.success_rate, 2),
                "failure_rate_percent": round(self.stats.failure_rate, 2),
                "last_failure_time": (
                    self.stats.last_failure_time.isoformat()
                    if self.stats.last_failure_time
                    else None
                ),
                "last_success_time": (
                    self.stats.last_success_time.isoformat()
                    if self.stats.last_success_time
                    else None
                ),
                "state_changes": self.stats.state_changes[-10:],  # Last 10 changes
            },
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "timeout_seconds": self.config.timeout_seconds,
                "half_open_max_calls": self.config.half_open_max_calls,
                "expected_exception": self.config.expected_exception.__name__,
            },
        }

    async def reset(self) -> None:
        """Reset circuit breaker to closed state."""
        async with self._lock:
            old_state = self.state
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.half_open_calls = 0
            self.last_failure_time = None

            self.stats.state_changes.append(
                f"{old_state.value} -> {self.state.value} (manual_reset)"
            )

            log_info("Circuit breaker manually reset", circuit_name=self.config.name)

    async def force_open(self) -> None:
        """Force circuit breaker to open state (for testing/maintenance)."""
        async with self._lock:
            old_state = self.state
            await self._transition_to_open()

            self.stats.state_changes.append(
                f"{old_state.value} -> {self.state.value} (forced)"
            )

            log_warning(
                "Circuit breaker manually forced open", circuit_name=self.config.name
            )

    def is_call_permitted(self) -> bool:
        """Check if a call would be permitted (without executing it)."""
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if (
                self.last_failure_time
                and time.time() - self.last_failure_time >= self.config.timeout_seconds
            ):
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            return self.half_open_calls < self.config.half_open_max_calls
        return False


class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers."""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}

    def register(self, name: str, config: CircuitBreakerConfig) -> CircuitBreaker:
        """Register a new circuit breaker."""
        config.name = name
        breaker = CircuitBreaker(config)
        self._breakers[name] = breaker

        log_info(
            "Circuit breaker registered",
            name=name,
            failure_threshold=config.failure_threshold,
            timeout_seconds=config.timeout_seconds,
        )

        return breaker

    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name."""
        return self._breakers.get(name)

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all registered circuit breakers."""
        return {name: breaker.get_stats() for name, breaker in self._breakers.items()}

    async def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for breaker in self._breakers.values():
            await breaker.reset()

        log_info("All circuit breakers reset", count=len(self._breakers))

    def health_check(self) -> Dict[str, Any]:
        """Perform health check on all circuit breakers."""
        health = {
            "timestamp": datetime.now().isoformat(),
            "total_breakers": len(self._breakers),
            "breakers": {},
        }

        healthy_count = 0
        for name, breaker in self._breakers.items():
            is_healthy = breaker.state != CircuitState.OPEN
            health["breakers"][name] = {
                "state": breaker.state.value,
                "healthy": is_healthy,
                "failure_count": breaker.failure_count,
                "call_permitted": breaker.is_call_permitted(),
            }

            if is_healthy:
                healthy_count += 1

        health["healthy_count"] = healthy_count
        health["overall_health"] = healthy_count == len(self._breakers)

        return health


# Global registry instance
_registry = CircuitBreakerRegistry()


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """Get the global circuit breaker registry."""
    return _registry


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    timeout_seconds: int = 60,
    half_open_max_calls: int = 3,
    expected_exception: type = Exception,
):
    """Decorator for applying circuit breaker to functions."""

    def decorator(func: Callable) -> Callable:
        # Register circuit breaker if not exists
        breaker = _registry.get(name)
        if not breaker:
            config = CircuitBreakerConfig(
                failure_threshold=failure_threshold,
                timeout_seconds=timeout_seconds,
                half_open_max_calls=half_open_max_calls,
                expected_exception=expected_exception,
                name=name,
            )
            breaker = _registry.register(name, config)

        async def async_wrapper(*args, **kwargs):
            return await breaker.call(func, *args, **kwargs)

        def sync_wrapper(*args, **kwargs):
            # For sync functions, we need to handle the async circuit breaker
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(breaker.call(func, *args, **kwargs))

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# Convenience functions for common patterns


def create_openai_circuit_breaker() -> CircuitBreaker:
    """Create circuit breaker specifically for OpenAI API calls."""
    from openai import OpenAIError

    config = CircuitBreakerConfig(
        failure_threshold=3,
        timeout_seconds=30,
        half_open_max_calls=2,
        expected_exception=OpenAIError,
        name="openai_api",
    )

    return _registry.register("openai_api", config)


def create_jira_circuit_breaker() -> CircuitBreaker:
    """Create circuit breaker specifically for Jira API calls."""
    import requests

    config = CircuitBreakerConfig(
        failure_threshold=5,
        timeout_seconds=60,
        half_open_max_calls=3,
        expected_exception=requests.RequestException,
        name="jira_api",
    )

    return _registry.register("jira_api", config)


def create_datadog_circuit_breaker() -> CircuitBreaker:
    """Create circuit breaker specifically for Datadog API calls."""
    import requests

    config = CircuitBreakerConfig(
        failure_threshold=3,
        timeout_seconds=45,
        half_open_max_calls=2,
        expected_exception=requests.RequestException,
        name="datadog_api",
    )

    return _registry.register("datadog_api", config)
