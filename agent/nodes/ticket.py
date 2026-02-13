"""Refactored ticket creation with clean separation of concerns.

This module provides a cleaner, more testable version of the ticket creation logic
with proper separation of validation, duplicate checking, payload building, and execution.

Duplicate detection is now delegated to ``agent.dedup.DuplicateDetector``.  Only
the Jira-facing strategies (fingerprint cache, loghash, error_type, similarity)
run here — the in-memory ``seen_logs`` dedup runs earlier in the graph
(``graph.py:analyze_log_wrapper``).
"""

from typing import Dict, Any, Optional
import os
import json
from dataclasses import dataclass

from agent.jira import (
    create_ticket as create_jira_ticket,
    comment_on_issue,
)
from agent.jira.payload import JiraPayloadBuilder, TicketPayload
from agent.jira.utils import (
    normalize_log_message,
    sanitize_for_jira,
    should_comment,
    update_comment_timestamp,
)
from agent.utils.logger import (
    log_info,
    log_error,
    log_warning,
    log_ticket_operation,
)
from agent.config import get_config
from agent.run_config import get_run_config
from agent.performance import get_performance_metrics
from agent.dedup import DuplicateDetector
from agent.dedup.result import DuplicateCheckResult
from agent.dedup.strategies import (
    FingerprintCache,
    LoghashLabelSearch,
    ErrorTypeLabelSearch,
    SimilaritySearch,
)
import pathlib
import datetime

# Configuration will be loaded lazily in functions


_AUDIT_LOG_DIR = pathlib.Path(".agent_cache")

# Detector used inside the create_ticket node: strategies 2-5 (Jira-facing).
# Strategy 1 (InMemorySeenLogs) runs earlier in the graph.
_ticket_dedup = DuplicateDetector(
    strategies=[
        FingerprintCache(),
        LoghashLabelSearch(),
        ErrorTypeLabelSearch(),
        SimilaritySearch(),
    ]
)


def _get_audit_log_path(team_id: str | None = None) -> pathlib.Path:
    if team_id:
        return _AUDIT_LOG_DIR / "teams" / team_id / "audit_logs.jsonl"
    return _AUDIT_LOG_DIR / "audit_logs.jsonl"


def _utcnow_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _append_audit_log(entry: dict, team_id: str | None = None) -> None:
    try:
        path = _get_audit_log_path(team_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # Best-effort; do not fail the workflow
        log_warning("Failed to append audit log", error=str(e))


def _append_audit(
    *,
    decision: str,
    state: Dict[str, Any],
    fingerprint: str,
    occ: int,
    jira_key: str | None = None,
    duplicate: bool = False,
    create: bool = False,
    message: str = "",
    strategy_name: str | None = None,
) -> None:
    team_id = state.get("team_id")
    entry = {
        "timestamp": _utcnow_iso(),
        "fingerprint": fingerprint,
        "error_type": state.get("error_type"),
        "severity": state.get("severity"),
        "create_ticket": bool(create),
        "duplicate": bool(duplicate),
        "decision": decision,
        "existing_issue_key": jira_key,
        "jira_key": jira_key,
        "occurrences": occ,
        "message": message,
    }
    if strategy_name:
        entry["strategy_name"] = strategy_name
    if team_id:
        entry["team_id"] = team_id
        entry["team_service"] = state.get("team_service", "")
    _append_audit_log(entry, team_id=team_id)


@dataclass
class TicketValidationResult:
    """Result of ticket field validation."""

    is_valid: bool
    title: Optional[str] = None
    description: Optional[str] = None
    error_message: Optional[str] = None


def _validate_ticket_fields(state: Dict[str, Any]) -> TicketValidationResult:
    """Validate that required ticket fields are present and valid.

    Args:
        state: Current agent state containing LLM analysis results

    Returns:
        TicketValidationResult with validation status and extracted fields
    """
    log_info("Validating ticket fields from LLM analysis")

    # Check for required fields
    if "ticket_title" not in state or "ticket_description" not in state:
        error_msg = "Missing LLM fields before ticket creation"
        log_error("Ticket validation failed", error=error_msg)
        return TicketValidationResult(is_valid=False, error_message=error_msg)

    title = state.get("ticket_title")
    description = state.get("ticket_description")

    # Validate field content
    if not title or not description:
        error_msg = "Ticket title or description is empty"
        log_error("Ticket validation failed", error=error_msg)
        return TicketValidationResult(is_valid=False, error_message=error_msg)

    if description is None:
        error_msg = "Ticket description is None"
        log_error("Ticket validation failed", error=error_msg)
        return TicketValidationResult(is_valid=False, error_message=error_msg)

    log_info(
        "Ticket fields validated successfully",
        title_preview=title[:50],
        description_length=len(description),
    )

    return TicketValidationResult(is_valid=True, title=title, description=description)


def _check_duplicates(state: Dict[str, Any], title: str) -> DuplicateCheckResult:
    """Check for duplicate tickets using the unified DuplicateDetector.

    Runs strategies 2-5 (fingerprint cache, loghash label, error_type label,
    similarity search).  Strategy 1 (InMemorySeenLogs) runs earlier in
    ``graph.py``.

    Args:
        state: Current agent state
        title: Proposed ticket title (unused now, kept for API compat)

    Returns:
        DuplicateCheckResult with duplicate status and details
    """
    log_info("Checking for duplicate tickets via DuplicateDetector")

    log_data = state.get("log_data", {})
    result = _ticket_dedup.check(log_data, state)

    if result.is_duplicate:
        # Emit audit entry and metrics
        fingerprint = _compute_fingerprint(state)
        raw_msg = log_data.get("message", "")
        fp_source = f"{log_data.get('logger', '')}|{raw_msg}"
        occ = (state.get("fp_counts") or {}).get(fp_source, 1)

        # Map strategy name to audit decision and metric
        strategy_to_decision = {
            "fingerprint_cache": "duplicate-fingerprint",
            "loghash_label_search": "duplicate-jira",
            "error_type_label_search": "duplicate-error-type",
            "similarity_search": "duplicate-jira",
        }
        strategy_to_metric = {
            "fingerprint_cache": "duplicates.fingerprint",
            "loghash_label_search": "duplicates.jira",
            "error_type_label_search": "duplicates.jira",
            "similarity_search": "duplicates.jira",
        }

        decision = strategy_to_decision.get(
            result.strategy_name or "", "duplicate-unknown"
        )
        metric = strategy_to_metric.get(result.strategy_name or "", "duplicates.jira")

        _append_audit(
            decision=decision,
            state=state,
            fingerprint=fingerprint,
            occ=occ,
            jira_key=result.existing_ticket_key,
            duplicate=True,
            create=False,
            message=result.message or "Duplicate detected",
            strategy_name=result.strategy_name,
        )

        from agent.metrics import incr as _m_incr

        _m_incr(metric, team_id=state.get("team_id"))

        # Comment on duplicate if it's a Jira-found duplicate
        if result.existing_ticket_key and result.similarity_score:
            _maybe_comment_duplicate(
                result.existing_ticket_key, result.similarity_score, state
            )

    else:
        log_info("No duplicates found, proceeding with ticket creation")

    return result


def _build_jira_payload(
    state: Dict[str, Any], title: str, description: str
) -> TicketPayload:
    """Build the Jira ticket payload with proper formatting and labels.

    Delegates to :class:`~agent.jira.payload.JiraPayloadBuilder`.

    Args:
        state: Current agent state
        title: Ticket title
        description: Ticket description

    Returns:
        TicketPayload with complete Jira payload and metadata
    """
    rc = get_run_config(state)
    log_info("Building Jira ticket payload")

    builder = JiraPayloadBuilder(rc)
    result = builder.build(state, title, description)

    log_info(
        "Jira payload built successfully",
        title=result.title,
        label_count=len(result.labels),
    )

    return result


def _execute_ticket_creation(
    state: Dict[str, Any], payload: TicketPayload
) -> Dict[str, Any]:
    """Execute the actual ticket creation or simulation.

    Args:
        state: Current agent state
        payload: Complete ticket payload

    Returns:
        Updated state with creation results
    """
    rc = get_run_config(state)
    log_info("Executing ticket creation")

    # Check per-run cap (strict enforcement)
    if _is_cap_reached(state):
        max_t = rc.max_tickets_per_run
        cap_msg = f"Ticket creation limit reached for this run (max {max_t})"
        log_warning("Ticket creation cap reached", max_tickets=max_t)
        # Audit cap reached
        _append_audit(
            decision="cap-reached",
            state=state,
            fingerprint=payload.fingerprint,
            occ=1,
            jira_key=None,
            duplicate=False,
            create=False,
            message=cap_msg,
        )
        from agent.metrics import incr as _m_incr

        _m_incr("tickets.cap_reached", team_id=state.get("team_id"))
        return {**state, "message": cap_msg, "ticket_created": True}

    # Create or simulate based on configuration
    if rc.auto_create_ticket:
        return _create_real_ticket(state, payload)
    else:
        return _simulate_ticket_creation(state, payload)


def _compute_fingerprint(state: Dict[str, Any]) -> str:
    """Compute a stable fingerprint for the log entry."""
    return JiraPayloadBuilder.compute_fingerprint(state)


def _load_processed_fingerprints(team_id: str | None = None) -> set[str]:
    """Load the set of processed fingerprints from cache."""
    from agent.jira.utils import load_processed_fingerprints as _load_fps

    return _load_fps(team_id)


def _save_processed_fingerprints(
    fingerprints: set[str], team_id: str | None = None
) -> None:
    """Save the set of processed fingerprints to cache."""
    from agent.jira.utils import save_processed_fingerprints as _save_fps

    _save_fps(fingerprints, team_id)


def _maybe_comment_duplicate(
    issue_key: str, score: float, state: Dict[str, Any]
) -> None:
    """Add a comment to an existing duplicate ticket if configured."""
    rc = get_run_config(state)
    if not rc.comment_on_duplicate:
        return

    cooldown_min = rc.comment_cooldown_minutes

    if should_comment(issue_key, cooldown_min, team_id=state.get("team_id")):
        log_data = state.get("log_data", {})
        win = state.get("window_hours", 48)
        raw_msg = log_data.get("message", "")
        norm_msg = normalize_log_message(raw_msg)
        fp_source = f"{log_data.get('logger','')}|{raw_msg}"
        occ = (state.get("fp_counts") or {}).get(fp_source, 1)

        comment = (
            f"Detected by Datadog Logs Agent as a likely duplicate (score {score:.2f}).\n"
            f"Logger: {log_data.get('logger', 'N/A')} | Thread: {log_data.get('thread', 'N/A')} | Timestamp: {log_data.get('timestamp', 'N/A')}\n"
            f"Occurrences in last {win}h: {occ}\n"
            f"Original message: {sanitize_for_jira(log_data.get('message', 'N/A'))}\n"
        )
        comment_on_issue(issue_key, comment)
        update_comment_timestamp(issue_key, team_id=state.get("team_id"))


def _is_cap_reached(state: Dict[str, Any]) -> bool:
    """Check if the per-run ticket creation cap has been reached."""
    rc = get_run_config(state)
    max_tickets = rc.max_tickets_per_run
    if max_tickets <= 0:
        return False
    return state.get("_tickets_created_in_run", 0) >= max_tickets


def _invoke_patchy(state: Dict[str, Any], issue_key: str) -> None:
    """Invoke Patchy to create a draft PR for the ticket.

    Args:
        state: Current agent state with log_data
        issue_key: Jira issue key for the created ticket
    """
    if os.getenv("INVOKE_PATCHY", "").lower() != "true":
        return

    if not os.getenv("GITHUB_TOKEN"):
        log_warning("Patchy requested but GITHUB_TOKEN not set, skipping")
        return

    try:
        log_data = state.get("log_data", {})
        logger_name = log_data.get("logger", "")
        error_type = state.get("error_type", "unknown")
        rc = get_run_config(state)
        service = state.get("service") or rc.datadog_service

        if not logger_name:
            log_info("No logger name in log data, skipping Patchy")
            return

        log_info("Invoking Patchy", service=service, logger=logger_name, jira=issue_key)

        from patchy.patchy_graph import build_graph as build_patchy_graph

        patchy_state = {
            "service": service,
            "error_type": error_type,
            "loghash": state.get("log_fingerprint", ""),
            "jira": issue_key,
            "logger": logger_name,
            "hint": "",
            "stacktrace": log_data.get("detail", ""),
            "mode": "note",
            "draft": True,
        }

        patchy_graph = build_patchy_graph()
        result = patchy_graph.invoke(patchy_state, config={"recursion_limit": 2000})

        pr_url = result.get("pr_url")
        if pr_url:
            log_info("Patchy created PR", pr_url=pr_url, jira=issue_key)
        else:
            log_info("Patchy completed", message=result.get("message", "no PR created"))

    except Exception as e:
        log_error("Patchy invocation failed", error=str(e), jira=issue_key)


def _create_real_ticket(
    state: Dict[str, Any], payload: TicketPayload
) -> Dict[str, Any]:
    """Create a real Jira ticket."""
    log_ticket_operation("Creating real ticket", title=payload.title)

    try:
        # Update state with payload info
        state["ticket_description"] = payload.description
        state["ticket_title"] = payload.title
        state["jira_payload"] = payload.payload
        state["log_fingerprint"] = payload.fingerprint

        # Create the ticket
        result_state = create_jira_ticket(state)

        issue_key = result_state.get("jira_response_key")
        if issue_key:
            log_ticket_operation(
                "Ticket created successfully", ticket_key=issue_key, title=payload.title
            )

            # Update fingerprint caches (in-run and persisted)
            team_id = state.get("team_id")
            processed = _load_processed_fingerprints(team_id)
            processed.add(payload.fingerprint)
            _save_processed_fingerprints(processed, team_id)
            state.setdefault("created_fingerprints", set()).add(payload.fingerprint)
            # Increment counter only on success
            state["_tickets_created_in_run"] = (
                state.get("_tickets_created_in_run", 0) + 1
            )
            # Audit created
            _append_audit(
                decision="created",
                state=state,
                fingerprint=payload.fingerprint,
                occ=1,
                jira_key=issue_key,
                duplicate=False,
                create=True,
                message="Ticket created successfully",
            )
            from agent.metrics import incr as _m_incr

            _m_incr("tickets.created", team_id=state.get("team_id"))

            # Invoke Patchy if enabled
            _invoke_patchy(result_state, issue_key)

            return {**result_state, "ticket_created": True}
        else:
            log_error("No Jira issue key found after ticket creation")
            return {
                **state,
                "ticket_created": True,
                "message": "Failed to create ticket",
            }

    except Exception as e:
        log_error("Failed to create Jira ticket", error=str(e))
        return {
            **state,
            "ticket_created": True,
            "message": f"Failed to create ticket: {e}",
        }


def _simulate_ticket_creation(
    state: Dict[str, Any], payload: TicketPayload
) -> Dict[str, Any]:
    """Simulate ticket creation for dry-run mode."""
    rc = get_run_config(state)
    log_ticket_operation("Simulating ticket creation", title=payload.title)

    # Update state with payload info
    state["ticket_description"] = payload.description
    state["ticket_title"] = payload.title
    state["jira_payload"] = payload.payload

    # Optionally persist fingerprint even in simulation
    persist_sim = rc.persist_sim_fp
    if persist_sim:
        team_id = state.get("team_id")
        processed = _load_processed_fingerprints(team_id)
        processed.add(payload.fingerprint)
        _save_processed_fingerprints(processed, team_id)

    log_info(
        "Ticket creation simulated",
        title=payload.title,
        persist_fingerprint=persist_sim,
    )
    # Audit simulation
    _append_audit(
        decision="simulated",
        state=state,
        fingerprint=payload.fingerprint,
        occ=1,
        jira_key=None,
        duplicate=False,
        create=False,
        message="Ticket creation simulated (dry run)",
    )
    from agent.metrics import incr as _m_incr

    _m_incr("tickets.simulated", team_id=state.get("team_id"))
    return {
        **state,
        "ticket_created": True,
        "message": "Ticket creation simulated (dry run)",
    }


def create_ticket(state: Dict[str, Any]) -> Dict[str, Any]:
    """Main ticket creation orchestrator with clean separation of concerns.

    This function orchestrates the ticket creation process by:
    1. Validating required fields
    2. Checking for duplicates (via DuplicateDetector, strategies 2-5)
    3. Building the Jira payload
    4. Executing creation or simulation

    Note: The LLM ``create_ticket=False`` decision is handled by the graph's
    conditional edge *before* this node is reached — it is no longer mixed
    into the dedup chain.

    Args:
        state: Current agent state containing LLM analysis results

    Returns:
        Updated state with ticket creation results
    """
    # Start performance timing
    metrics = get_performance_metrics()
    metrics.start_timer("create_ticket")

    log_ticket_operation("Starting ticket creation workflow")

    # Initialize run counter
    state.setdefault("_tickets_created_in_run", 0)

    # 1. Validate ticket fields
    validation = _validate_ticket_fields(state)
    if not validation.is_valid:
        return {**state, "message": validation.error_message, "ticket_created": True}

    # 2. Check for duplicates (strategies 2-5 via DuplicateDetector)
    duplicate_check = _check_duplicates(state, validation.title)
    if duplicate_check.is_duplicate:
        return {**state, "message": duplicate_check.message, "ticket_created": True}

    # 3. Build Jira payload
    payload = _build_jira_payload(state, validation.title, validation.description)

    # 4. Execute ticket creation
    result = _execute_ticket_creation(state, payload)

    # End performance timing
    duration = metrics.end_timer("create_ticket")

    log_ticket_operation(
        "Ticket creation workflow completed",
        ticket_created=result.get("ticket_created", False),
        duration_ms=round(duration * 1000, 2),
    )

    return result
