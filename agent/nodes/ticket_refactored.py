"""Refactored ticket creation with clean separation of concerns.

This module provides a cleaner, more testable version of the ticket creation logic
with proper separation of validation, duplicate checking, payload building, and execution.
"""
from typing import Dict, Any, Tuple, Optional
import os
import json
import hashlib
from dataclasses import dataclass

from agent.jira import create_ticket as create_jira_ticket, find_similar_ticket, comment_on_issue
from agent.jira.utils import normalize_log_message, should_comment, update_comment_timestamp
from agent.jira.utils import priority_name_from_severity
from agent.utils.logger import log_info, log_error, log_warning, log_ticket_operation, log_duplicate_detection


@dataclass
class TicketValidationResult:
    """Result of ticket field validation."""
    is_valid: bool
    title: Optional[str] = None
    description: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class DuplicateCheckResult:
    """Result of duplicate checking."""
    is_duplicate: bool
    existing_ticket_key: Optional[str] = None
    similarity_score: Optional[float] = None
    message: Optional[str] = None


@dataclass
class TicketPayload:
    """Jira ticket payload with metadata."""
    payload: Dict[str, Any]
    title: str
    description: str
    labels: list[str]
    fingerprint: str


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
        return TicketValidationResult(
            is_valid=False,
            error_message=error_msg
        )
    
    title = state.get("ticket_title")
    description = state.get("ticket_description")
    
    # Validate field content
    if not title or not description:
        error_msg = "Ticket title or description is empty"
        log_error("Ticket validation failed", error=error_msg)
        return TicketValidationResult(
            is_valid=False,
            error_message=error_msg
        )
    
    if description is None:
        error_msg = "Ticket description is None"
        log_error("Ticket validation failed", error=error_msg)
        return TicketValidationResult(
            is_valid=False,
            error_message=error_msg
        )
    
    log_info("Ticket fields validated successfully", 
             title_preview=title[:50], 
             description_length=len(description))
    
    return TicketValidationResult(
        is_valid=True,
        title=title,
        description=description
    )


def _check_duplicates(state: Dict[str, Any], title: str) -> DuplicateCheckResult:
    """Check for duplicate tickets using multiple strategies.
    
    Args:
        state: Current agent state
        title: Proposed ticket title
        
    Returns:
        DuplicateCheckResult with duplicate status and details
    """
    log_info("Checking for duplicate tickets")
    
    # 1. Check fingerprint cache (fastest)
    fingerprint = _compute_fingerprint(state)
    processed = _load_processed_fingerprints()
    
    if fingerprint in processed:
        log_info("Duplicate found in fingerprint cache", fingerprint=fingerprint)
        return DuplicateCheckResult(
            is_duplicate=True,
            message="Log already processed previously (fingerprint match)"
        )
    
    # 2. Check LLM decision
    if not state.get("create_ticket", False):
        log_info("LLM decided not to create ticket")
        return DuplicateCheckResult(
            is_duplicate=False,
            message="LLM decision: do not create a ticket for this log"
        )
    
    # 3. Check Jira for similar tickets
    try:
        key, score, existing_summary = find_similar_ticket(title, state)
        if key:
            log_duplicate_detection(score, key, existing_summary=existing_summary)
            
            # Add comment to existing ticket if configured
            _maybe_comment_duplicate(key, score, state)
            
            # Update fingerprint cache
            processed.add(fingerprint)
            _save_processed_fingerprints(processed)
            
            return DuplicateCheckResult(
                is_duplicate=True,
                existing_ticket_key=key,
                similarity_score=score,
                message=f"Duplicate in Jira: {key} — {existing_summary}"
            )
    except Exception as e:
        log_error("Error during Jira duplicate check", error=str(e))
        # Continue with creation if duplicate check fails
    
    log_info("No duplicates found, proceeding with ticket creation")
    return DuplicateCheckResult(is_duplicate=False)


def _build_jira_payload(state: Dict[str, Any], title: str, description: str) -> TicketPayload:
    """Build the Jira ticket payload with proper formatting and labels.
    
    Args:
        state: Current agent state
        title: Ticket title
        description: Ticket description
        
    Returns:
        TicketPayload with complete Jira payload and metadata
    """
    log_info("Building Jira ticket payload")
    
    # Compute fingerprint for labels
    fingerprint = _compute_fingerprint(state)
    
    # Build enhanced description with context
    full_description = _build_enhanced_description(state, description)
    
    # Build labels
    labels = _build_labels(state, fingerprint)
    
    # Clean title
    clean_title = _clean_title(title, state.get("error_type"))
    
    # Build payload
    payload = {
        "fields": {
            "project": {"key": os.getenv("JIRA_PROJECT_KEY")},
            "summary": clean_title,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"text": full_description, "type": "text"}]}],
            },
            "issuetype": {"name": "Bug"},
            "labels": labels,
            "priority": {"name": priority_name_from_severity(state.get("severity"))},
            "customfield_10767": [{"value": "Team Vega"}],
        }
    }
    
    log_info("Jira payload built successfully", 
             title=clean_title, 
             label_count=len(labels))
    
    return TicketPayload(
        payload=payload,
        title=clean_title,
        description=full_description,
        labels=labels,
        fingerprint=fingerprint
    )


def _execute_ticket_creation(state: Dict[str, Any], payload: TicketPayload) -> Dict[str, Any]:
    """Execute the actual ticket creation or simulation.
    
    Args:
        state: Current agent state
        payload: Complete ticket payload
        
    Returns:
        Updated state with creation results
    """
    log_info("Executing ticket creation")
    
    # Check per-run cap
    if _is_cap_reached(state):
        cap_msg = f"Ticket creation limit reached for this run (max {_get_max_tickets()})"
        log_warning("Ticket creation cap reached", max_tickets=_get_max_tickets())
        return {**state, "message": cap_msg, "ticket_created": True}
    
    # Increment counter
    state["_tickets_created_in_run"] = state.get("_tickets_created_in_run", 0) + 1
    
    # Create or simulate based on configuration
    auto_create = os.getenv("AUTO_CREATE_TICKET", "false").lower() == "true"
    
    if auto_create:
        return _create_real_ticket(state, payload)
    else:
        return _simulate_ticket_creation(state, payload)


def _compute_fingerprint(state: Dict[str, Any]) -> str:
    """Compute a stable fingerprint for the log entry."""
    log_data = state.get("log_data", {})
    fp_source = f"{log_data.get('logger','')}|{log_data.get('thread','')}|{log_data.get('message','')}"
    return hashlib.sha1(fp_source.encode("utf-8")).hexdigest()[:12]


def _load_processed_fingerprints() -> set[str]:
    """Load the set of processed fingerprints from cache."""
    try:
        import json
        import pathlib
        cache_path = pathlib.Path(".agent_cache/processed_logs.json")
        if cache_path.exists():
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
    except Exception:
        pass
    return set()


def _save_processed_fingerprints(fingerprints: set[str]) -> None:
    """Save the set of processed fingerprints to cache."""
    try:
        import json
        import pathlib
        cache_path = pathlib.Path(".agent_cache/processed_logs.json")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(fingerprints)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("Failed to save processed fingerprints", error=str(e))


def _maybe_comment_duplicate(issue_key: str, score: float, state: Dict[str, Any]) -> None:
    """Add a comment to an existing duplicate ticket if configured."""
    if os.getenv("COMMENT_ON_DUPLICATE", "true").lower() not in ("1", "true", "yes"):
        return
    
    try:
        cooldown_min = int(os.getenv("COMMENT_COOLDOWN_MINUTES", "120") or "0")
    except Exception:
        cooldown_min = 120
    
    if should_comment(issue_key, cooldown_min):
        log_data = state.get("log_data", {})
        win = state.get("window_hours", 48)
        fp_source = f"{log_data.get('logger','')}|{log_data.get('thread','')}|{log_data.get('message','')}"
        occ = (state.get("fp_counts") or {}).get(fp_source, 1)
        
        comment = (
            f"Detected by Datadog Logs Agent as a likely duplicate (score {score:.2f}).\n"
            f"Logger: {log_data.get('logger', 'N/A')} | Thread: {log_data.get('thread', 'N/A')} | Timestamp: {log_data.get('timestamp', 'N/A')}\n"
            f"Occurrences in last {win}h: {occ}\n"
            f"Original message: {log_data.get('message', 'N/A')}\n"
        )
        comment_on_issue(issue_key, comment)
        update_comment_timestamp(issue_key)


def _build_enhanced_description(state: Dict[str, Any], description: str) -> str:
    """Build enhanced description with additional context."""
    log_data = state.get("log_data", {})
    win = state.get("window_hours", 48)
    fp_source = f"{log_data.get('logger','')}|{log_data.get('thread','')}|{log_data.get('message','')}"
    occ = (state.get("fp_counts") or {}).get(fp_source, 1)
    
    extra_info = f"""
---
🕒 Timestamp: {log_data.get('timestamp', 'N/A')}
🧩 Logger: {log_data.get('logger', 'N/A')}
🧵 Thread: {log_data.get('thread', 'N/A')}
📝 Original Log: {log_data.get('message', 'N/A')}
🔍 Detail: {log_data.get('detail', 'N/A')}
📈 Occurrences in last {win}h: {occ}
""".strip()
    
    return f"{description.strip()}\n{extra_info}"


def _build_labels(state: Dict[str, Any], fingerprint: str) -> list[str]:
    """Build labels for the ticket."""
    labels = ["datadog-log"]
    
    # Add loghash label
    try:
        norm_msg = normalize_log_message((state.get("log_data") or {}).get("message", ""))
        if norm_msg:
            loghash = hashlib.sha1(norm_msg.encode("utf-8")).hexdigest()[:12]
            labels.append(f"loghash-{loghash}")
    except Exception:
        pass
    
    # Add aggregation labels based on error type
    etype = (state.get("error_type") or "").strip().lower()
    
    if os.getenv("AGGREGATE_EMAIL_NOT_FOUND", "false").lower() in ("1", "true", "yes") and etype == "email-not-found":
        labels.append("aggregate-email-not-found")
    
    if os.getenv("AGGREGATE_KAFKA_CONSUMER", "false").lower() in ("1", "true", "yes") and etype == "kafka-consumer":
        labels.append("aggregate-kafka-consumer")
    
    return labels


def _clean_title(title: str, error_type: Optional[str]) -> str:
    """Clean and format the ticket title."""
    base_title = title.replace("**", "").strip()
    
    # Handle aggregation cases
    if error_type == "email-not-found" and os.getenv("AGGREGATE_EMAIL_NOT_FOUND", "false").lower() in ("1", "true", "yes"):
        base_title = "Investigate Email Not Found errors (aggregated)"
    elif error_type == "kafka-consumer" and os.getenv("AGGREGATE_KAFKA_CONSUMER", "false").lower() in ("1", "true", "yes"):
        base_title = "Investigate Kafka Consumer errors (aggregated)"
    
    # Truncate if too long
    max_title = 120
    if len(base_title) > max_title:
        base_title = base_title[:max_title - 1] + "…"
    
    # Add prefix
    prefix = "[Datadog]" + (f"[{error_type}]" if error_type else "")
    return f"{prefix} {base_title}".strip()


def _is_cap_reached(state: Dict[str, Any]) -> bool:
    """Check if the per-run ticket creation cap has been reached."""
    max_tickets = _get_max_tickets()
    if max_tickets <= 0:
        return False
    return state.get("_tickets_created_in_run", 0) >= max_tickets


def _get_max_tickets() -> int:
    """Get the maximum number of tickets per run."""
    try:
        return int(os.getenv("MAX_TICKETS_PER_RUN", "3") or "0")
    except Exception:
        return 3


def _create_real_ticket(state: Dict[str, Any], payload: TicketPayload) -> Dict[str, Any]:
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
            log_ticket_operation("Ticket created successfully", 
                               ticket_key=issue_key, 
                               title=payload.title)
            
            # Update fingerprint cache
            processed = _load_processed_fingerprints()
            processed.add(payload.fingerprint)
            _save_processed_fingerprints(processed)
            
            return {**result_state, "ticket_created": True}
        else:
            log_error("No Jira issue key found after ticket creation")
            return {**state, "ticket_created": True, "message": "Failed to create ticket"}
            
    except Exception as e:
        log_error("Failed to create Jira ticket", error=str(e))
        return {**state, "ticket_created": True, "message": f"Failed to create ticket: {e}"}


def _simulate_ticket_creation(state: Dict[str, Any], payload: TicketPayload) -> Dict[str, Any]:
    """Simulate ticket creation for dry-run mode."""
    log_ticket_operation("Simulating ticket creation", title=payload.title)
    
    # Update state with payload info
    state["ticket_description"] = payload.description
    state["ticket_title"] = payload.title
    state["jira_payload"] = payload.payload
    
    # Optionally persist fingerprint even in simulation
    persist_sim = os.getenv("PERSIST_SIM_FP", "false").lower() in ("1", "true", "yes")
    if persist_sim:
        processed = _load_processed_fingerprints()
        processed.add(payload.fingerprint)
        _save_processed_fingerprints(processed)
    
    log_info("Ticket creation simulated", 
             title=payload.title, 
             persist_fingerprint=persist_sim)
    
    return {**state, "ticket_created": True, "message": "Ticket creation simulated (dry run)"}


def create_ticket(state: Dict[str, Any]) -> Dict[str, Any]:
    """Main ticket creation orchestrator with clean separation of concerns.
    
    This function orchestrates the ticket creation process by:
    1. Validating required fields
    2. Checking for duplicates
    3. Building the Jira payload
    4. Executing creation or simulation
    
    Args:
        state: Current agent state containing LLM analysis results
        
    Returns:
        Updated state with ticket creation results
    """
    log_ticket_operation("Starting ticket creation workflow")
    
    # Initialize run counter
    state.setdefault("_tickets_created_in_run", 0)
    
    # 1. Validate ticket fields
    validation = _validate_ticket_fields(state)
    if not validation.is_valid:
        return {**state, "message": validation.error_message, "ticket_created": True}
    
    # 2. Check for duplicates
    duplicate_check = _check_duplicates(state, validation.title)
    if duplicate_check.is_duplicate:
        return {**state, "message": duplicate_check.message, "ticket_created": True}
    
    # 3. Build Jira payload
    payload = _build_jira_payload(state, validation.title, validation.description)
    
    # 4. Execute ticket creation
    result = _execute_ticket_creation(state, payload)
    
    log_ticket_operation("Ticket creation workflow completed", 
                        ticket_created=result.get("ticket_created", False))
    
    return result
