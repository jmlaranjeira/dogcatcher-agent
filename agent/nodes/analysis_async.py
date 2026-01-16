"""Async LLM analysis node for parallel log processing.

Provides true async analysis using LangChain's ainvoke() method,
with circuit breaker protection and fallback analysis support.
"""
from __future__ import annotations
import os
import re
import json
from typing import Dict, Any

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from agent.utils.logger import log_info, log_error, log_debug, log_warning
from agent.utils.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    get_circuit_breaker_registry
)
from agent.utils.fallback_analysis import get_fallback_analyzer
from agent.config import get_config

# LLM configuration via environment variables
_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_temp_raw = os.getenv("OPENAI_TEMPERATURE", "0")
_resp_fmt = (os.getenv("OPENAI_RESPONSE_FORMAT", "json_object") or "json_object").lower()

try:
    _temp = float(_temp_raw)
except Exception:
    _temp = 0.0

if _resp_fmt == "json_object":
    _model_kwargs = {"response_format": {"type": "json_object"}}
else:
    _model_kwargs = {}

# Initialize async-capable LLM
llm = ChatOpenAI(
    model=_model,
    temperature=_temp,
    model_kwargs=_model_kwargs,
)

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are a senior support engineer. Analyze the input log context and RETURN ONLY JSON (no code block). "
            "Fields required: "
            "error_type (kebab-case, e.g. pre-persist, db-constraint, kafka-consumer), "
            "create_ticket (boolean), "
            "ticket_title (short, action-oriented, no prefixes like [Datadog]), "
            "ticket_description (markdown including: Problem summary; Possible Causes as bullets; Suggested Actions as bullets), "
            "severity (one of: low, medium, high)."
        ),
    ),
    ("human", "{log_message}")
])

chain = prompt | llm

# Circuit breaker state
_circuit_breaker_initialized = False


def _initialize_circuit_breaker_async():
    """Initialize circuit breaker for async OpenAI API calls."""
    global _circuit_breaker_initialized
    if _circuit_breaker_initialized:
        return

    config = get_config()

    if config.circuit_breaker_enabled:
        from openai import OpenAIError

        registry = get_circuit_breaker_registry()

        # Check if already registered
        if not registry.get("openai_llm_async"):
            cb_config = CircuitBreakerConfig(
                failure_threshold=config.circuit_breaker_failure_threshold,
                timeout_seconds=config.circuit_breaker_timeout_seconds,
                half_open_max_calls=config.circuit_breaker_half_open_calls,
                expected_exception=OpenAIError,
                name="openai_llm_async"
            )
            registry.register("openai_llm_async", cb_config)

            log_info(
                "Circuit breaker initialized for async LLM",
                failure_threshold=config.circuit_breaker_failure_threshold,
                timeout_seconds=config.circuit_breaker_timeout_seconds
            )

    _circuit_breaker_initialized = True


async def _call_llm_async(contextual_log: str) -> str:
    """Call LLM asynchronously using ainvoke.

    Args:
        contextual_log: Formatted log context for analysis

    Returns:
        LLM response content
    """
    config = get_config()

    if not config.circuit_breaker_enabled:
        # Circuit breaker disabled, call LLM directly
        response = await chain.ainvoke({"log_message": contextual_log})
        return response.content

    # Get circuit breaker from registry
    registry = get_circuit_breaker_registry()
    breaker = registry.get("openai_llm_async")

    if not breaker:
        # Fallback if breaker not initialized
        log_warning("Async circuit breaker not found, initializing now")
        _initialize_circuit_breaker_async()
        breaker = registry.get("openai_llm_async")

    # Call LLM through circuit breaker
    async def _invoke_chain():
        response = await chain.ainvoke({"log_message": contextual_log})
        return response.content

    return await breaker.call(_invoke_chain)


async def analyze_log_async(state: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a log entry asynchronously using an LLM.

    Uses circuit breaker pattern to protect against LLM service failures.
    Falls back to rule-based analysis when LLM is unavailable.

    Args:
        state: Current state containing log_data with message, logger, thread, detail

    Returns:
        Updated state with error_type, create_ticket, ticket_title,
        ticket_description, and severity fields
    """
    config = get_config()
    log_data = state.get("log_data", {})
    msg = log_data.get("message", "")
    logger = log_data.get("logger", "unknown.logger")
    thread = log_data.get("thread", "unknown.thread")
    detail = log_data.get("detail", "")

    contextual_log = (
        f"[Logger]: {logger if logger else 'unknown.logger'}\n"
        f"[Thread]: {thread if thread else 'unknown.thread'}\n"
        f"[Message]: {msg if msg else '<no message>'}\n"
        f"[Detail]: {detail if detail else '<no detail>'}"
    )

    # Initialize circuit breaker if needed
    if config.circuit_breaker_enabled and not _circuit_breaker_initialized:
        _initialize_circuit_breaker_async()

    content = None
    try:
        # Call LLM asynchronously with circuit breaker protection
        content = await _call_llm_async(contextual_log)

        log_debug("Async LLM analysis completed", content_preview=content[:200] if content else "N/A")

        # Parse LLM response
        match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
        raw_json = match.group(1) if match else content

        parsed = json.loads(raw_json)
        title = parsed.get("ticket_title")
        desc = parsed.get("ticket_description")

        if not title or not desc:
            raise ValueError("Missing title or description")

        log_info(
            "Log analyzed successfully with async LLM",
            error_type=parsed.get('error_type'),
            create_ticket=parsed.get('create_ticket')
        )

        return {**state, **parsed, "severity": parsed.get("severity", "low")}

    except CircuitBreakerOpenError as e:
        # Circuit breaker is open - use fallback analysis
        log_warning(
            "Async circuit breaker open, using fallback analysis",
            circuit_name="openai_llm_async",
            reason=str(e)
        )

        if config.fallback_analysis_enabled:
            return await _use_fallback_analysis_async(state, log_data)
        else:
            return {
                **state,
                "error_type": "llm-unavailable",
                "create_ticket": False,
                "ticket_title": "LLM service unavailable",
                "ticket_description": f"Circuit breaker open: {str(e)}",
                "severity": "low"
            }

    except (json.JSONDecodeError, ValueError) as e:
        # LLM returned invalid response - try fallback
        log_error(
            "Async LLM analysis failed with invalid response",
            error=str(e),
            content_preview=content[:200] if content else "N/A"
        )

        if config.fallback_analysis_enabled:
            log_info("Falling back to rule-based analysis due to async LLM error")
            return await _use_fallback_analysis_async(state, log_data)
        else:
            return {
                **state,
                "error_type": "unknown",
                "create_ticket": False,
                "ticket_title": "LLM returned invalid or incomplete data",
                "ticket_description": content if content else "No content"
            }

    except Exception as e:
        # Unexpected error - try fallback
        log_error(
            "Unexpected error during async LLM analysis",
            error=str(e),
            error_type=type(e).__name__
        )

        if config.fallback_analysis_enabled:
            log_info("Falling back to rule-based analysis due to unexpected async error")
            return await _use_fallback_analysis_async(state, log_data)
        else:
            return {
                **state,
                "error_type": "analysis-error",
                "create_ticket": False,
                "ticket_title": "Analysis failed",
                "ticket_description": f"Error: {str(e)}"
            }


async def _use_fallback_analysis_async(state: Dict[str, Any], log_data: Dict[str, Any]) -> Dict[str, Any]:
    """Use rule-based fallback analysis when LLM is unavailable.

    This is a thin async wrapper around the sync fallback analyzer.
    The fallback analyzer is CPU-bound and fast, so no async benefit.

    Args:
        state: Current agent state
        log_data: Log data to analyze

    Returns:
        Updated state with analysis results
    """
    fallback_analyzer = get_fallback_analyzer()

    log_info("Using fallback rule-based analysis (async path)")

    # Perform fallback analysis (sync, but fast)
    result = fallback_analyzer.analyze_log(log_data)

    return {**state, **result}


async def analyze_logs_batch_async(
    logs: list[Dict[str, Any]],
    max_concurrent: int = 5
) -> list[Dict[str, Any]]:
    """Analyze multiple logs concurrently.

    Args:
        logs: List of log entries to analyze
        max_concurrent: Maximum concurrent analysis tasks

    Returns:
        List of analysis results
    """
    import asyncio

    semaphore = asyncio.Semaphore(max_concurrent)

    async def analyze_with_semaphore(log: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            state = {
                "log_data": log,
                "log_message": log.get("message", "")
            }
            return await analyze_log_async(state)

    tasks = [analyze_with_semaphore(log) for log in logs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions and log them
    valid_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log_error(f"Batch analysis error for log {i}", error=str(result))
            valid_results.append({
                "error_type": "batch-analysis-error",
                "create_ticket": False,
                "ticket_title": f"Analysis failed: {str(result)}",
                "ticket_description": str(result),
                "severity": "low"
            })
        else:
            valid_results.append(result)

    log_info(
        "Batch async analysis completed",
        total=len(logs),
        successful=sum(1 for r in valid_results if r.get("create_ticket"))
    )

    return valid_results
