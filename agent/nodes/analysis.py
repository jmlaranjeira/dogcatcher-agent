"""LLM analysis node (prompt, chain, analyze_log)."""

from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()

from langchain_core.prompts import ChatPromptTemplate
from agent.utils.logger import log_info, log_error, log_debug, log_warning
from agent.utils.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    get_circuit_breaker_registry,
)
from agent.utils.fallback_analysis import get_fallback_analyzer
from agent.config import get_config
from agent.run_config import get_run_config
from agent.nodes.prompt_context import build_contextual_log
from agent.team_loader import get_team
from agent.llm_factory import get_langchain_llm, get_circuit_breaker_exception_class

import re
import json

from agent.nodes.json_sanitizer import parse_llm_json as _parse_llm_json

_SYSTEM_PROMPT = (
    "You are a senior support engineer. Analyze the input log context and RETURN ONLY JSON (no code block). "
    "Fields required: "
    "error_type (kebab-case, e.g. pre-persist, db-constraint, kafka-consumer), "
    "create_ticket (boolean), "
    "ticket_title (short, action-oriented, no prefixes like [Datadog]), "
    "ticket_description (markdown including: Problem summary; Possible Causes as bullets; Suggested Actions as bullets), "
    "severity (one of: low, medium, high). "
    "Context fields [Service], [Environment], [Occurrences in last Nh], and [Severity hints] "
    "are provided when available — use them to calibrate severity and create_ticket decisions."
)


def _build_chain():
    """Build a fresh LLM chain.

    Avoids module-level boto3 session creation which binds an
    asyncio.Lock to the import-time event loop and breaks async workers.
    """
    llm = get_langchain_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _SYSTEM_PROMPT),
            ("human", "{log_message}"),
        ]
    )
    return prompt | llm

# Initialize circuit breaker for LLM calls (Phase 1.2)
_circuit_breaker_initialized = False


def _initialize_circuit_breaker():
    """Initialize circuit breaker for LLM API calls."""
    global _circuit_breaker_initialized
    if _circuit_breaker_initialized:
        return

    config = get_config()

    if config.circuit_breaker_enabled:
        exc_class = get_circuit_breaker_exception_class()

        registry = get_circuit_breaker_registry()
        cb_config = CircuitBreakerConfig(
            failure_threshold=config.circuit_breaker_failure_threshold,
            timeout_seconds=config.circuit_breaker_timeout_seconds,
            half_open_max_calls=config.circuit_breaker_half_open_calls,
            expected_exception=exc_class,
            name="llm",
        )
        registry.register("llm", cb_config)

        log_info(
            "Circuit breaker initialized for LLM",
            failure_threshold=config.circuit_breaker_failure_threshold,
            timeout_seconds=config.circuit_breaker_timeout_seconds,
        )

    _circuit_breaker_initialized = True


async def _call_llm_with_circuit_breaker(contextual_log: str) -> str:
    """Call LLM with circuit breaker protection."""
    config = get_config()

    chain = _build_chain()

    if not config.circuit_breaker_enabled:
        # Circuit breaker disabled, call LLM directly
        response = chain.invoke({"log_message": contextual_log})
        return response.content

    # Get circuit breaker from registry
    registry = get_circuit_breaker_registry()
    breaker = registry.get("llm")

    if not breaker:
        # Fallback if breaker not initialized
        log_warning("Circuit breaker not found, initializing now")
        _initialize_circuit_breaker()
        breaker = registry.get("llm")

    # Call LLM through circuit breaker
    async def _invoke_chain():
        response = chain.invoke({"log_message": contextual_log})
        return response.content

    return await breaker.call(_invoke_chain)


def analyze_log(state: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a log entry using an LLM to extract structured incident data.

    Uses circuit breaker pattern to protect against LLM service failures.
    Falls back to rule-based analysis when LLM is unavailable.
    """
    rc = get_run_config(state)
    log_data = state.get("log_data", {})

    # Load team severity rules for multi-tenant prompt enrichment
    team_severity_rules = None
    team_id = rc.team_id or state.get("team_id")
    if team_id:
        team = get_team(team_id)
        if team and team.severity_rules:
            team_severity_rules = team.severity_rules

    contextual_log = build_contextual_log(
        log_data, state, rc, team_severity_rules=team_severity_rules
    )

    # Initialize circuit breaker if needed
    if rc.circuit_breaker_enabled and not _circuit_breaker_initialized:
        _initialize_circuit_breaker()

    # Try LLM analysis with circuit breaker protection
    try:
        import asyncio

        # Run async circuit breaker call in sync context
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        content = loop.run_until_complete(
            _call_llm_with_circuit_breaker(contextual_log)
        )

        log_debug("LLM analysis completed", content_preview=content[:200])

        # Parse LLM response
        match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
        raw_json = match.group(1) if match else content

        parsed = _parse_llm_json(raw_json)
        title = parsed.get("ticket_title")
        desc = parsed.get("ticket_description")

        if not title or not desc:
            raise ValueError("Missing title or description")

        log_info(
            "Log analyzed successfully with LLM",
            error_type=parsed.get("error_type"),
            create_ticket=parsed.get("create_ticket"),
        )

        return {**state, **parsed, "severity": parsed.get("severity", "low")}

    except CircuitBreakerOpenError as e:
        # Circuit breaker is open - use fallback analysis
        log_warning(
            "Circuit breaker open, using fallback analysis",
            circuit_name="llm",
            reason=str(e),
        )

        if rc.fallback_analysis_enabled:
            return _use_fallback_analysis(state, log_data)
        else:
            # Fallback disabled, return error state
            return {
                **state,
                "error_type": "llm-unavailable",
                "create_ticket": False,
                "ticket_title": "LLM service unavailable",
                "ticket_description": f"Circuit breaker open: {str(e)}",
                "severity": "low",
            }

    except (json.JSONDecodeError, ValueError) as e:
        # LLM returned invalid response - try fallback
        log_error(
            "LLM analysis failed with invalid response",
            error=str(e),
            content_preview=content[:200] if "content" in locals() else "N/A",
        )

        if rc.fallback_analysis_enabled:
            log_info("Falling back to rule-based analysis due to LLM error")
            return _use_fallback_analysis(state, log_data)
        else:
            return {
                **state,
                "error_type": "unknown",
                "create_ticket": False,
                "ticket_title": "LLM returned invalid or incomplete data",
                "ticket_description": (
                    content if "content" in locals() else "No content"
                ),
            }

    except Exception as e:
        # Unexpected error - try fallback
        log_error(
            "Unexpected error during LLM analysis",
            error=str(e),
            error_type=type(e).__name__,
        )

        if rc.fallback_analysis_enabled:
            log_info("Falling back to rule-based analysis due to unexpected error")
            return _use_fallback_analysis(state, log_data)
        else:
            return {
                **state,
                "error_type": "analysis-error",
                "create_ticket": False,
                "ticket_title": "Analysis failed",
                "ticket_description": f"Error: {str(e)}",
            }


def _use_fallback_analysis(
    state: Dict[str, Any], log_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Use rule-based fallback analysis when LLM is unavailable."""
    fallback_analyzer = get_fallback_analyzer()

    log_info("Using fallback rule-based analysis")

    # Perform fallback analysis
    result = fallback_analyzer.analyze_log(log_data)

    # Merge with state
    return {**state, **result}
