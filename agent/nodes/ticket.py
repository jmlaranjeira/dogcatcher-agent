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
from agent.config import get_config
from agent.performance import get_performance_metrics
import pathlib
import datetime

# Configuration will be loaded lazily in functions


_AUDIT_LOG_PATH = pathlib.Path(".agent_cache/audit_logs.jsonl")


def _utcnow_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _append_audit_log(entry: dict) -> None:
    try:
        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # Best-effort; do not fail the workflow
        log_warning("Failed to append audit log", error=str(e))


def _append_audit(*, decision: str, state: Dict[str, Any], fingerprint: str, occ: int,
                  jira_key: str | None = None, duplicate: bool = False, create: bool = False,
                  message: str = "") -> None:
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
    _append_audit_log(entry)

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
    created_in_run: set[str] = state.get("created_fingerprints", set())
    
    if fingerprint in created_in_run or fingerprint in processed:
        log_info("Duplicate found in fingerprint cache", fingerprint=fingerprint)
        # Approximate occurrence lookup
        log_data = state.get("log_data", {})
        raw_msg = log_data.get('message','')
        norm_msg = normalize_log_message(raw_msg)
        fp_source = f"{log_data.get('logger','')}|{norm_msg or raw_msg}"
        occ = (state.get("fp_counts") or {}).get(fp_source, 1)
        _append_audit(
            decision="duplicate-fingerprint",
            state=state,
            fingerprint=fingerprint,
            occ=occ,
            jira_key=None,
            duplicate=True,
            create=False,
            message="Duplicate log skipped (fingerprint cache)",
        )
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

    # 3. Check for recent tickets with same error_type (prevents cross-logger duplicates)
    error_type = state.get("error_type", "")
    if error_type and error_type != "unknown":
        try:
            config = get_config()
            jql = (
                f"project = {config.jira_project_key} "
                f"AND labels = datadog-log "
                f"AND labels = {error_type} "
                f"AND created >= -7d "
                f"ORDER BY created DESC"
            )
            from agent.jira.client import search as jira_search
            resp = jira_search(jql, max_results=1)
            if resp and resp.get("issues"):
                existing = resp["issues"][0]
                existing_key = existing.get("key")
                existing_summary = existing.get("fields", {}).get("summary", "")
                log_info(
                    "Duplicate found by error_type label",
                    error_type=error_type,
                    existing_key=existing_key
                )
                # Update fingerprint cache
                processed.add(fingerprint)
                _save_processed_fingerprints(processed)

                _append_audit(
                    decision="duplicate-error-type",
                    state=state,
                    fingerprint=fingerprint,
                    occ=1,
                    jira_key=existing_key,
                    duplicate=True,
                    create=False,
                    message=f"Duplicate by error_type '{error_type}': {existing_key}",
                )
                return DuplicateCheckResult(
                    is_duplicate=True,
                    existing_ticket_key=existing_key,
                    similarity_score=0.95,
                    message=f"Recent ticket with same error_type: {existing_key} - {existing_summary}"
                )
        except Exception as e:
            log_error("Error during error_type duplicate check", error=str(e))

    # 4. Check Jira for similar tickets
    try:
        key, score, existing_summary = find_similar_ticket(title, state)
        if key:
            log_duplicate_detection(score, key, existing_summary=existing_summary)
            
            # Add comment to existing ticket if configured
            _maybe_comment_duplicate(key, score, state)
            
            # Update fingerprint cache
            processed.add(fingerprint)
            _save_processed_fingerprints(processed)
            # Audit duplicate in Jira
            log_data = state.get("log_data", {})
            raw_msg = log_data.get('message','')
            norm_msg = normalize_log_message(raw_msg)
            fp_source = f"{log_data.get('logger','')}|{norm_msg or raw_msg}"
            occ = (state.get("fp_counts") or {}).get(fp_source, 1)
            _append_audit(
                decision="duplicate-jira",
                state=state,
                fingerprint=fingerprint,
                occ=occ,
                jira_key=key,
                duplicate=True,
                create=False,
                message=f"Duplicate in Jira: {key} — {existing_summary}",
            )
            
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
    config = get_config()
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
            "project": {"key": config.jira_project_key},
            "summary": clean_title,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"text": full_description, "type": "text"}]}],
            },
            "issuetype": {"name": "Bug"},
            "labels": labels,
            "priority": {"name": priority_name_from_severity(state.get("severity"))},
        }
    }

    # Optional team custom field injection via env vars
    # Set JIRA_TEAM_FIELD_ID and JIRA_TEAM_VALUE in .env to enable
    try:
        team_field_id = os.getenv("JIRA_TEAM_FIELD_ID")
        team_field_value = os.getenv("JIRA_TEAM_VALUE")
        if team_field_id and team_field_value:
            payload["fields"][team_field_id] = [{"value": team_field_value}]
    except Exception:
        # Do not fail payload building if optional field injection fails
        pass
    
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
    config = get_config()
    log_info("Executing ticket creation")
    
    # Check per-run cap (strict enforcement)
    if _is_cap_reached(state):
        cap_msg = f"Ticket creation limit reached for this run (max {_get_max_tickets()})"
        log_warning("Ticket creation cap reached", max_tickets=_get_max_tickets())
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
        return {**state, "message": cap_msg, "ticket_created": True}
    
    # Create or simulate based on configuration
    auto_create = config.auto_create_ticket
    
    if auto_create:
        return _create_real_ticket(state, payload)
    else:
        return _simulate_ticket_creation(state, payload)


def _compute_fingerprint(state: Dict[str, Any]) -> str:
    """Compute a stable fingerprint for the log entry.

    Uses error_type (from LLM analysis) + normalized message to group
    similar errors regardless of which logger produced them.
    """
    log_data = state.get("log_data", {})
    raw_msg = log_data.get('message', '')
    norm_msg = normalize_log_message(raw_msg)

    # Use error_type from LLM analysis (more stable than logger name)
    error_type = state.get("error_type", "unknown")

    fp_source = f"{error_type}|{norm_msg or raw_msg}"
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
    config = get_config()
    if not config.comment_on_duplicate:
        return
    
    cooldown_min = config.comment_cooldown_minutes
    
    if should_comment(issue_key, cooldown_min):
        log_data = state.get("log_data", {})
        win = state.get("window_hours", 48)
        raw_msg = log_data.get('message','')
        norm_msg = normalize_log_message(raw_msg)
        fp_source = f"{log_data.get('logger','')}|{norm_msg or raw_msg}"
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
    """Build enhanced description with additional context including MDC fields and Datadog links."""
    config = get_config()
    log_data = state.get("log_data", {})
    win = state.get("window_hours", 48)
    raw_msg = log_data.get('message','')
    norm_msg = normalize_log_message(raw_msg)
    fp_source = f"{log_data.get('logger','')}|{norm_msg or raw_msg}"
    occ = (state.get("fp_counts") or {}).get(fp_source, 1)

    # Extract MDC fields from log attributes (if available)
    attributes = log_data.get('attributes', {})
    request_id = attributes.get('requestId') or attributes.get('request_id') or log_data.get('requestId', '')
    user_id = attributes.get('userId') or attributes.get('user_id') or log_data.get('userId', '')
    error_type = attributes.get('errorType') or attributes.get('error_type') or log_data.get('errorType', '')

    # Build basic context info
    extra_info = f"""
---
🕒 Timestamp: {log_data.get('timestamp', 'N/A')}
🧩 Logger: {log_data.get('logger', 'N/A')}
🧵 Thread: {log_data.get('thread', 'N/A')}
📝 Original Log: {log_data.get('message', 'N/A')}
🔍 Detail: {log_data.get('detail', 'N/A')}
📈 Occurrences in last {win}h: {occ}"""

    # Add MDC context if available
    mdc_context = []
    if request_id:
        mdc_context.append(f"📋 Request ID: {request_id}")
    if user_id:
        mdc_context.append(f"👤 User ID: {user_id}")
    if error_type:
        mdc_context.append(f"🏷️ Error Type: {error_type}")

    if mdc_context:
        extra_info += "\n---\n" + "\n".join(mdc_context)

    # Build Datadog trace links
    datadog_links = _build_datadog_links(config, log_data, request_id, user_id)
    if datadog_links:
        extra_info += f"\n---\n🔗 Datadog Links:\n{datadog_links}"

    return f"{description.strip()}\n{extra_info}"


def _build_datadog_links(config, log_data: Dict[str, Any], request_id: str, user_id: str) -> str:
    """Build Datadog query links for tracing."""
    links = []
    base_url = config.datadog_logs_url
    service = config.datadog_service

    # Link to full request trace (if requestId available)
    if request_id:
        query = f"service:{service} @requestId:{request_id}"
        encoded_query = query.replace(" ", "%20").replace(":", "%3A").replace("@", "%40")
        links.append(f"• Request Trace: {base_url}?query={encoded_query}")

    # Link to user activity (if userId available)
    if user_id:
        query = f"service:{service} @userId:{user_id}"
        encoded_query = query.replace(" ", "%20").replace(":", "%3A").replace("@", "%40")
        links.append(f"• User Activity: {base_url}?query={encoded_query}")

    # Link to similar errors (by logger)
    logger = log_data.get('logger', '')
    if logger:
        query = f"service:{service} @logger_name:{logger} status:error"
        encoded_query = query.replace(" ", "%20").replace(":", "%3A").replace("@", "%40")
        links.append(f"• Similar Errors: {base_url}?query={encoded_query}")

    return "\n".join(links)


def _build_labels(state: Dict[str, Any], fingerprint: str) -> list[str]:
    """Build labels for the ticket."""
    config = get_config()
    labels = ["datadog-log"]

    # Add loghash label
    try:
        norm_msg = normalize_log_message((state.get("log_data") or {}).get("message", ""))
        if norm_msg:
            loghash = hashlib.sha1(norm_msg.encode("utf-8")).hexdigest()[:12]
            labels.append(f"loghash-{loghash}")
    except Exception:
        pass

    # Add error_type label (enables duplicate detection via error_type label search)
    etype = (state.get("error_type") or "").strip().lower()
    if etype and etype != "unknown":
        labels.append(etype)

    # Add aggregation labels based on error type
    
    if config.aggregate_email_not_found and etype == "email-not-found":
        labels.append("aggregate-email-not-found")
    
    if config.aggregate_kafka_consumer and etype == "kafka-consumer":
        labels.append("aggregate-kafka-consumer")
    
    return labels


def _clean_title(title: str, error_type: Optional[str]) -> str:
    """Clean and format the ticket title."""
    config = get_config()
    base_title = title.replace("**", "").strip()
    
    # Handle aggregation cases
    if error_type == "email-not-found" and config.aggregate_email_not_found:
        base_title = "Investigate Email Not Found errors (aggregated)"
    elif error_type == "kafka-consumer" and config.aggregate_kafka_consumer:
        base_title = "Investigate Kafka Consumer errors (aggregated)"
    
    # Truncate if too long
    max_title = config.max_title_length
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
    config = get_config()
    return config.max_tickets_per_run


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
        service = state.get("service") or get_config().datadog_service

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

            # Update fingerprint caches (in-run and persisted)
            processed = _load_processed_fingerprints()
            processed.add(payload.fingerprint)
            _save_processed_fingerprints(processed)
            state.setdefault("created_fingerprints", set()).add(payload.fingerprint)
            # Increment counter only on success
            state["_tickets_created_in_run"] = state.get("_tickets_created_in_run", 0) + 1
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

            # Invoke Patchy if enabled
            _invoke_patchy(result_state, issue_key)

            return {**result_state, "ticket_created": True}
        else:
            log_error("No Jira issue key found after ticket creation")
            return {**state, "ticket_created": True, "message": "Failed to create ticket"}

    except Exception as e:
        log_error("Failed to create Jira ticket", error=str(e))
        return {**state, "ticket_created": True, "message": f"Failed to create ticket: {e}"}


def _simulate_ticket_creation(state: Dict[str, Any], payload: TicketPayload) -> Dict[str, Any]:
    """Simulate ticket creation for dry-run mode."""
    config = get_config()
    log_ticket_operation("Simulating ticket creation", title=payload.title)
    
    # Update state with payload info
    state["ticket_description"] = payload.description
    state["ticket_title"] = payload.title
    state["jira_payload"] = payload.payload
    
    # Optionally persist fingerprint even in simulation
    persist_sim = config.persist_sim_fp
    if persist_sim:
        processed = _load_processed_fingerprints()
        processed.add(payload.fingerprint)
        _save_processed_fingerprints(processed)
    
    log_info("Ticket creation simulated", 
             title=payload.title, 
             persist_fingerprint=persist_sim)
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
    
    # 2. Check for duplicates
    duplicate_check = _check_duplicates(state, validation.title)
    if duplicate_check.is_duplicate:
        return {**state, "message": duplicate_check.message, "ticket_created": True}
    
    # 3. Build Jira payload
    payload = _build_jira_payload(state, validation.title, validation.description)
    
    # 4. Execute ticket creation
    result = _execute_ticket_creation(state, payload)
    
    # End performance timing
    duration = metrics.end_timer("create_ticket")
    
    log_ticket_operation("Ticket creation workflow completed", 
                        ticket_created=result.get("ticket_created", False),
                        duration_ms=round(duration * 1000, 2))
    
    return result
