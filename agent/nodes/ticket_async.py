"""Async ticket creation module for parallel log processing.

Provides true async ticket creation using AsyncJiraClient,
with duplicate detection, validation, and audit logging.
"""

from __future__ import annotations
import os
import json
import pathlib
import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass

from agent.jira.async_client import AsyncJiraClient
from agent.jira.async_match import (
    find_similar_ticket_async,
    check_fingerprint_duplicate_async,
)
from agent.jira.payload import JiraPayloadBuilder, TicketPayload
from agent.jira.utils import (
    normalize_log_message,
    should_comment,
    update_comment_timestamp,
)
from agent.utils.logger import (
    log_info,
    log_error,
    log_warning,
    log_ticket_operation,
    log_duplicate_detection,
)
from agent.config import get_config
from agent.run_config import get_run_config
from agent.performance import get_performance_metrics

_AUDIT_LOG_PATH = pathlib.Path(".agent_cache/audit_logs.jsonl")


def _utcnow_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _append_audit_log(entry: dict) -> None:
    try:
        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log_warning("Failed to append audit log (async)", error=str(e))


def _append_audit(
    *,
    decision: str,
    state: Dict[str, Any],
    fingerprint: str,
    occ: int,
    jira_key: Optional[str] = None,
    duplicate: bool = False,
    create: bool = False,
    message: str = "",
) -> None:
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
        "async": True,
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


def _validate_ticket_fields(state: Dict[str, Any]) -> TicketValidationResult:
    """Validate that required ticket fields are present and valid."""
    log_info("Validating ticket fields (async)")

    if "ticket_title" not in state or "ticket_description" not in state:
        error_msg = "Missing LLM fields before ticket creation"
        log_error("Ticket validation failed (async)", error=error_msg)
        return TicketValidationResult(is_valid=False, error_message=error_msg)

    title = state.get("ticket_title")
    description = state.get("ticket_description")

    if not title or not description:
        error_msg = "Ticket title or description is empty"
        log_error("Ticket validation failed (async)", error=error_msg)
        return TicketValidationResult(is_valid=False, error_message=error_msg)

    log_info(
        "Ticket fields validated (async)",
        title_preview=title[:50],
        description_length=len(description),
    )

    return TicketValidationResult(is_valid=True, title=title, description=description)


def _compute_fingerprint(state: Dict[str, Any]) -> str:
    """Compute a stable fingerprint for the log entry."""
    return JiraPayloadBuilder.compute_fingerprint(state)


def _load_processed_fingerprints() -> set[str]:
    """Load the set of processed fingerprints from cache."""
    try:
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
        cache_path = pathlib.Path(".agent_cache/processed_logs.json")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(fingerprints)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("Failed to save processed fingerprints (async)", error=str(e))


async def _check_duplicates_async(
    state: Dict[str, Any], title: str, client: AsyncJiraClient
) -> DuplicateCheckResult:
    """Check for duplicate tickets using multiple strategies asynchronously.

    Args:
        state: Current agent state
        title: Proposed ticket title
        client: Async Jira client instance

    Returns:
        DuplicateCheckResult with duplicate status and details
    """
    log_info("Checking for duplicate tickets (async)")

    # 1. Check fingerprint cache (fastest)
    fingerprint = _compute_fingerprint(state)
    processed = _load_processed_fingerprints()
    created_in_run: set[str] = state.get("created_fingerprints", set())

    if fingerprint in created_in_run or fingerprint in processed:
        log_info(
            "Duplicate found in fingerprint cache (async)", fingerprint=fingerprint
        )
        log_data = state.get("log_data", {})
        raw_msg = log_data.get("message", "")
        norm_msg = normalize_log_message(raw_msg)
        fp_source = f"{log_data.get('logger', '')}|{norm_msg or raw_msg}"
        occ = (state.get("fp_counts") or {}).get(fp_source, 1)
        _append_audit(
            decision="duplicate-fingerprint",
            state=state,
            fingerprint=fingerprint,
            occ=occ,
            jira_key=None,
            duplicate=True,
            create=False,
            message="Duplicate log skipped (fingerprint cache, async)",
        )
        return DuplicateCheckResult(
            is_duplicate=True,
            message="Log already processed previously (fingerprint match)",
        )

    # 2. Check LLM decision
    if not state.get("create_ticket", False):
        log_info("LLM decided not to create ticket (async)")
        return DuplicateCheckResult(
            is_duplicate=False,
            message="LLM decision: do not create a ticket for this log",
        )

    # 3. Check fingerprint label in Jira (async)
    try:
        is_fp_dup, fp_key = await check_fingerprint_duplicate_async(
            fingerprint, client, state
        )
        if is_fp_dup:
            log_duplicate_detection(1.0, fp_key, existing_summary="fingerprint match")
            log_data = state.get("log_data", {})
            raw_msg = log_data.get("message", "")
            norm_msg = normalize_log_message(raw_msg)
            fp_source = f"{log_data.get('logger', '')}|{norm_msg or raw_msg}"
            occ = (state.get("fp_counts") or {}).get(fp_source, 1)
            _append_audit(
                decision="duplicate-jira-fingerprint",
                state=state,
                fingerprint=fingerprint,
                occ=occ,
                jira_key=fp_key,
                duplicate=True,
                create=False,
                message=f"Fingerprint duplicate in Jira: {fp_key}",
            )
            return DuplicateCheckResult(
                is_duplicate=True,
                existing_ticket_key=fp_key,
                similarity_score=1.0,
                message=f"Fingerprint duplicate in Jira: {fp_key}",
            )
    except Exception as e:
        log_error("Error during async fingerprint check", error=str(e))

    # 4. Check for recent tickets with same error_type (prevents cross-logger duplicates)
    error_type = state.get("error_type", "")
    if error_type and error_type != "unknown":
        try:
            rc = get_run_config(state)
            jql = (
                f"project = {rc.jira_project_key} "
                f"AND labels = datadog-log "
                f"AND labels = {error_type} "
                f"AND created >= -7d "
                f"ORDER BY created DESC"
            )
            resp = await client.search(jql, max_results=1)
            if resp and resp.get("issues"):
                existing = resp["issues"][0]
                existing_key = existing.get("key")
                existing_summary = existing.get("fields", {}).get("summary", "")
                log_info(
                    "Duplicate found by error_type label (async)",
                    error_type=error_type,
                    existing_key=existing_key,
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
                    message=f"Recent ticket with same error_type: {existing_key} - {existing_summary}",
                )
        except Exception as e:
            log_error("Error during error_type duplicate check (async)", error=str(e))

    # 5. Check Jira for similar tickets (async)
    try:
        key, score, existing_summary = await find_similar_ticket_async(
            title, client, state
        )
        if key:
            log_duplicate_detection(score, key, existing_summary=existing_summary)

            # Update fingerprint cache
            processed.add(fingerprint)
            _save_processed_fingerprints(processed)

            log_data = state.get("log_data", {})
            raw_msg = log_data.get("message", "")
            norm_msg = normalize_log_message(raw_msg)
            fp_source = f"{log_data.get('logger', '')}|{norm_msg or raw_msg}"
            occ = (state.get("fp_counts") or {}).get(fp_source, 1)
            _append_audit(
                decision="duplicate-jira",
                state=state,
                fingerprint=fingerprint,
                occ=occ,
                jira_key=key,
                duplicate=True,
                create=False,
                message=f"Duplicate in Jira (async): {key} - {existing_summary}",
            )

            return DuplicateCheckResult(
                is_duplicate=True,
                existing_ticket_key=key,
                similarity_score=score,
                message=f"Duplicate in Jira: {key} - {existing_summary}",
            )
    except Exception as e:
        log_error("Error during async Jira duplicate check", error=str(e))

    log_info("No duplicates found (async), proceeding with ticket creation")
    return DuplicateCheckResult(is_duplicate=False)


def _build_jira_payload(
    state: Dict[str, Any], title: str, description: str
) -> TicketPayload:
    """Build the Jira ticket payload with proper formatting and labels.

    Delegates to :class:`~agent.jira.payload.JiraPayloadBuilder`.
    Async-created tickets include the ``async-created`` label.
    """
    rc = get_run_config(state)
    log_info("Building Jira ticket payload (async)")

    builder = JiraPayloadBuilder(rc)
    result = builder.build(state, title, description, extra_labels=["async-created"])

    log_info(
        "Jira payload built (async)",
        title=result.title,
        label_count=len(result.labels),
    )

    return result


def _is_cap_reached(state: Dict[str, Any]) -> bool:
    """Check if the per-run ticket creation cap has been reached."""
    rc = get_run_config(state)
    max_tickets = rc.max_tickets_per_run
    if max_tickets <= 0:
        return False
    return state.get("_tickets_created_in_run", 0) >= max_tickets


async def _execute_ticket_creation_async(
    state: Dict[str, Any], payload: TicketPayload, client: AsyncJiraClient
) -> Dict[str, Any]:
    """Execute the actual ticket creation or simulation asynchronously.

    Args:
        state: Current agent state
        payload: Complete ticket payload
        client: Async Jira client instance

    Returns:
        Updated state with creation results
    """
    rc = get_run_config(state)
    log_info("Executing ticket creation (async)")

    # Check per-run cap
    if _is_cap_reached(state):
        cap_msg = (
            f"Ticket creation limit reached for this run (max {rc.max_tickets_per_run})"
        )
        log_warning(
            "Ticket creation cap reached (async)",
            max_tickets=rc.max_tickets_per_run,
        )
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

    if rc.auto_create_ticket:
        return await _create_real_ticket_async(state, payload, client)
    else:
        return _simulate_ticket_creation(state, payload)


def _invoke_patchy_sync(state: Dict[str, Any], issue_key: str) -> None:
    """Invoke Patchy synchronously to create a draft PR for the ticket.

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
        service = state.get("service") or get_run_config(state).datadog_service

        if not logger_name:
            log_info("No logger name in log data, skipping Patchy")
            return

        log_info(
            "Invoking Patchy (from async)",
            service=service,
            logger=logger_name,
            jira=issue_key,
        )

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


async def _create_real_ticket_async(
    state: Dict[str, Any], payload: TicketPayload, client: AsyncJiraClient
) -> Dict[str, Any]:
    """Create a real Jira ticket asynchronously."""
    log_ticket_operation("Creating real ticket (async)", title=payload.title)

    try:
        # Create the ticket via async client
        result = await client.create_issue(payload.payload)

        if result:
            issue_key = result.get("key")
            if issue_key:
                log_ticket_operation(
                    "Ticket created successfully (async)",
                    ticket_key=issue_key,
                    title=payload.title,
                )

                # Update fingerprint caches
                processed = _load_processed_fingerprints()
                processed.add(payload.fingerprint)
                _save_processed_fingerprints(processed)
                state.setdefault("created_fingerprints", set()).add(payload.fingerprint)

                # Increment counter
                state["_tickets_created_in_run"] = (
                    state.get("_tickets_created_in_run", 0) + 1
                )

                _append_audit(
                    decision="created",
                    state=state,
                    fingerprint=payload.fingerprint,
                    occ=1,
                    jira_key=issue_key,
                    duplicate=False,
                    create=True,
                    message="Ticket created successfully (async)",
                )

                # Invoke Patchy if enabled (runs sync, not blocking critical path)
                _invoke_patchy_sync(state, issue_key)

                return {
                    **state,
                    "ticket_created": True,
                    "jira_response_key": issue_key,
                    "ticket_key": issue_key,
                    "ticket_title": payload.title,
                    "ticket_description": payload.description,
                }

        log_error("No Jira issue key found after async ticket creation")
        return {
            **state,
            "ticket_created": True,
            "message": "Failed to create ticket (async)",
        }

    except Exception as e:
        log_error("Failed to create Jira ticket (async)", error=str(e))
        return {
            **state,
            "ticket_created": True,
            "message": f"Failed to create ticket (async): {e}",
        }


def _simulate_ticket_creation(
    state: Dict[str, Any], payload: TicketPayload
) -> Dict[str, Any]:
    """Simulate ticket creation for dry-run mode."""
    rc = get_run_config(state)
    log_ticket_operation("Simulating ticket creation (async)", title=payload.title)

    state["ticket_description"] = payload.description
    state["ticket_title"] = payload.title
    state["jira_payload"] = payload.payload

    persist_sim = rc.persist_sim_fp
    if persist_sim:
        processed = _load_processed_fingerprints()
        processed.add(payload.fingerprint)
        _save_processed_fingerprints(processed)

    log_info(
        "Ticket creation simulated (async)",
        title=payload.title,
        persist_fingerprint=persist_sim,
    )
    _append_audit(
        decision="simulated",
        state=state,
        fingerprint=payload.fingerprint,
        occ=1,
        jira_key=None,
        duplicate=False,
        create=False,
        message="Ticket creation simulated (dry run, async)",
    )
    return {
        **state,
        "ticket_created": True,
        "message": "Ticket creation simulated (dry run)",
    }


async def create_ticket_async(state: Dict[str, Any]) -> Dict[str, Any]:
    """Main async ticket creation orchestrator.

    This function orchestrates the ticket creation process by:
    1. Validating required fields
    2. Checking for duplicates (async Jira searches)
    3. Building the Jira payload
    4. Executing creation or simulation

    Args:
        state: Current agent state containing LLM analysis results

    Returns:
        Updated state with ticket creation results
    """
    metrics = get_performance_metrics()
    metrics.start_timer("create_ticket_async")

    log_ticket_operation("Starting async ticket creation workflow")

    state.setdefault("_tickets_created_in_run", 0)

    # 1. Validate ticket fields
    validation = _validate_ticket_fields(state)
    if not validation.is_valid:
        return {**state, "message": validation.error_message, "ticket_created": True}

    # 2-4. Use async Jira client for all operations
    async with AsyncJiraClient() as client:
        # 2. Check for duplicates
        duplicate_check = await _check_duplicates_async(state, validation.title, client)
        if duplicate_check.is_duplicate:
            return {**state, "message": duplicate_check.message, "ticket_created": True}

        # 3. Build Jira payload
        payload = _build_jira_payload(state, validation.title, validation.description)

        # 4. Execute ticket creation
        result = await _execute_ticket_creation_async(state, payload, client)

    duration = metrics.end_timer("create_ticket_async")

    log_ticket_operation(
        "Async ticket creation workflow completed",
        ticket_created=result.get("ticket_created", False),
        duration_ms=round(duration * 1000, 2),
    )

    return result


async def create_tickets_batch_async(
    states: list[Dict[str, Any]], max_concurrent: int = 3
) -> list[Dict[str, Any]]:
    """Create multiple tickets concurrently.

    Args:
        states: List of states with analysis results
        max_concurrent: Maximum concurrent ticket creation tasks

    Returns:
        List of ticket creation results
    """
    import asyncio

    log_info("Starting batch async ticket creation", count=len(states))

    semaphore = asyncio.Semaphore(max_concurrent)

    async def create_with_semaphore(state: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            return await create_ticket_async(state)

    tasks = [create_with_semaphore(state) for state in states]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log_error(f"Batch ticket creation error for state {i}", error=str(result))
            valid_results.append(
                {
                    **states[i],
                    "ticket_created": True,
                    "message": f"Batch creation failed: {str(result)}",
                }
            )
        else:
            valid_results.append(result)

    created_count = sum(1 for r in valid_results if r.get("jira_response_key"))
    log_info(
        "Batch async ticket creation completed",
        total=len(states),
        created=created_count,
    )

    return valid_results
