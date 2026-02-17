"""Sleuth agent node implementations and state definition.

This module contains the SleuthState TypedDict and all node functions
for the Sleuth investigation workflow.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TypedDict

import requests

from agent.config import get_config
from agent.jira.client import search as jira_search, is_configured as jira_is_configured
from agent.jira.match import find_similar_ticket
from agent.jira.utils import normalize_text
from agent.utils.logger import log_info, log_error, log_warning


class SleuthState(TypedDict, total=False):
    """State for the Sleuth investigation workflow."""

    # Input
    query: str  # Natural language description
    service: Optional[str]  # Service filter (optional, can be inferred)
    env: str  # Environment (default: prod)
    hours_back: int  # Time window (default: 24)
    no_patchy: bool  # Disable Patchy suggestions
    all_status: bool  # Search all statuses, not just errors

    # Generated
    dd_query: str  # Query built for Datadog
    logs: List[Dict]  # Logs found

    # Correlation
    related_tickets: List[Dict]  # Related Jira tickets

    # Analysis
    summary: str  # LLM summary
    root_cause: str  # Identified root cause (if applicable)
    suggested_fix: str  # Fix suggestion (if applicable)

    # Action
    can_auto_fix: bool  # Whether Patchy could fix it
    patchy_invoked: bool  # Whether Patchy was invoked
    patchy_result: str  # Patchy result

    # Status
    error: Optional[str]  # Error message if something failed


def parse_query(state: Dict[str, Any]) -> Dict[str, Any]:
    """Parse and extract information from the natural language query.

    Extracts service names, error patterns, and other entities
    from the user's natural language query.
    """
    from .utils.query_builder import extract_entities

    query = state.get("query", "")
    entities = extract_entities(query)

    # Try to infer service if not provided
    service = state.get("service")
    if not service and entities.get("services"):
        service = entities["services"][0]
        log_info("Service inferred from query", service=service)

    log_info(
        "Query parsed",
        query=query,
        service=service,
        entities_found=len(entities.get("keywords", [])),
    )

    return {
        **state,
        "service": service,
        "_entities": entities,  # Internal use for other nodes
    }


def build_dd_query(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build an optimized Datadog query using LLM.

    Uses the parsed query and context to generate an effective
    Datadog search query.
    """
    from .utils.query_builder import build_datadog_query

    query = state.get("query", "")
    service = state.get("service")
    config = get_config()
    env = state.get("env") or config.datadog_env
    all_status = state.get("all_status", False)

    dd_query = build_datadog_query(
        user_query=query,
        service=service,
        env=env,
        use_llm=True,
        all_status=all_status,
    )

    log_info("Datadog query built", dd_query=dd_query)

    return {
        **state,
        "dd_query": dd_query,
    }


def search_logs(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the Datadog search with the generated query.

    Fetches logs from Datadog using the built query and
    configured time window.
    """
    config = get_config()

    # Check Datadog configuration
    if not config.datadog_api_key or not config.datadog_app_key:
        log_error("Missing Datadog configuration")
        return {**state, "logs": [], "error": "Missing Datadog API credentials"}

    dd_query = state.get("dd_query", "")
    hours_back = state.get("hours_back", 24)

    now = datetime.utcnow()
    start = now - timedelta(hours=hours_back)

    base_url = f"https://api.{config.datadog_site}/api/v2/logs/events/search"
    headers = {
        "DD-API-KEY": config.datadog_api_key,
        "DD-APPLICATION-KEY": config.datadog_app_key,
        "Content-Type": "application/json",
    }

    payload = {
        "filter": {
            "from": start.isoformat() + "Z",
            "to": now.isoformat() + "Z",
            "query": dd_query,
        },
        "page": {"limit": min(config.datadog_limit, 100)},
        "sort": "timestamp",
    }

    try:
        resp = requests.post(
            base_url, json=payload, headers=headers, timeout=config.datadog_timeout
        )
        resp.raise_for_status()
        data = resp.json()

        logs = []
        for log in data.get("data", []):
            attr = log.get("attributes", {})
            logs.append(
                {
                    "message": attr.get("message", "<no message>"),
                    "timestamp": attr.get("timestamp"),
                    "service": attr.get("service"),
                    "status": attr.get("status"),
                    "logger": attr.get("attributes", {}).get("logger", {}).get("name"),
                    "host": attr.get("host"),
                }
            )

        log_info("Logs fetched from Datadog", count=len(logs))
        return {**state, "logs": logs}

    except requests.RequestException as e:
        log_error("Datadog search failed", error=str(e))
        return {**state, "logs": [], "error": f"Datadog search failed: {e}"}


def correlate_jira(state: Dict[str, Any]) -> Dict[str, Any]:
    """Search for related Jira tickets based on the logs found.

    Uses similarity matching to find existing tickets that
    might be related to the investigated errors.
    """
    if not jira_is_configured():
        log_warning("Jira not configured, skipping correlation")
        return {**state, "related_tickets": []}

    logs = state.get("logs", [])
    if not logs:
        return {**state, "related_tickets": []}

    config = get_config()
    related_tickets: List[Dict] = []
    seen_keys: set = set()

    # Group logs by unique message patterns (deduplicate)
    unique_messages = {}
    for log in logs:
        msg = log.get("message", "")
        norm_msg = normalize_text(msg)[:100]  # Truncate for grouping
        if norm_msg not in unique_messages:
            unique_messages[norm_msg] = log

    # Search for similar tickets for each unique log pattern
    for norm_msg, log in list(unique_messages.items())[
        :10
    ]:  # Limit to 10 unique patterns
        msg = log.get("message", "")

        # Create a pseudo state for find_similar_ticket
        pseudo_state = {"log_data": log}

        key, score, summary = find_similar_ticket(
            msg,
            state=pseudo_state,
            similarity_threshold=0.5,  # Lower threshold for investigation
        )

        if key and key not in seen_keys:
            seen_keys.add(key)
            related_tickets.append(
                {
                    "key": key,
                    "summary": summary,
                    "score": round(score, 2),
                    "matched_log": msg[:100],
                }
            )

    # Also do a direct JQL search for recent related tickets
    query = state.get("query", "")
    keywords = query.split()[:5]  # Use first 5 words
    if keywords:
        keyword_clauses = " OR ".join(
            [f'summary ~ "{kw}"' for kw in keywords if len(kw) >= 3]
        )
        if keyword_clauses:
            jql = f"project = {config.jira_project_key} AND ({keyword_clauses}) ORDER BY created DESC"
            try:
                resp = jira_search(jql, fields="summary,status,created", max_results=5)
                for issue in (resp or {}).get("issues", []):
                    key = issue.get("key")
                    if key and key not in seen_keys:
                        seen_keys.add(key)
                        fields = issue.get("fields", {})
                        related_tickets.append(
                            {
                                "key": key,
                                "summary": fields.get("summary", ""),
                                "status": fields.get("status", {}).get("name", ""),
                                "created": fields.get("created", "")[:10],  # Date only
                                "score": 0.0,  # JQL match, no similarity score
                            }
                        )
            except Exception as e:
                log_warning("JQL search for related tickets failed", error=str(e))

    log_info("Jira correlation completed", tickets_found=len(related_tickets))
    return {**state, "related_tickets": related_tickets}


def analyze_results(state: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze the logs and related tickets using LLM.

    Generates a summary, identifies potential root causes,
    and suggests fixes if applicable.
    """
    config = get_config()

    logs = state.get("logs", [])
    tickets = state.get("related_tickets", [])
    query = state.get("query", "")

    if not logs:
        return {
            **state,
            "summary": "No logs found matching the query.",
            "root_cause": "",
            "suggested_fix": "",
            "can_auto_fix": False,
        }

    if config.llm_provider == "openai" and not config.openai_api_key:
        # Fallback to basic analysis
        return _basic_analysis(state)

    # Prepare logs summary for LLM
    logs_summary = []
    for i, log in enumerate(logs[:20], 1):  # Limit to 20 logs
        logs_summary.append(
            f"{i}. [{log.get('status', 'unknown')}] {log.get('message', '')[:200]}"
        )
    logs_text = "\n".join(logs_summary)

    # Prepare tickets summary
    tickets_summary = []
    for t in tickets[:5]:
        status = t.get("status", "")
        tickets_summary.append(
            f"- {t['key']}: {t.get('summary', '')} (Score: {t.get('score', 0)}, Status: {status})"
        )
    tickets_text = (
        "\n".join(tickets_summary) if tickets_summary else "No related tickets found."
    )

    prompt = f"""Analyze these error logs and related Jira tickets.
User asked: "{query}"

Logs found ({len(logs)} total, showing first 20):
{logs_text}

Related tickets:
{tickets_text}

Provide a JSON response with:
{{
    "summary": "2-3 sentence executive summary of the findings",
    "root_cause": "Probable root cause if identifiable, otherwise empty string",
    "suggested_fix": "Fix suggestion if applicable, otherwise empty string",
    "can_auto_fix": true/false - whether this looks like a code bug that could be auto-fixed
}}

Focus on:
1. Common patterns in the errors
2. Whether existing tickets cover this issue
3. If a code fix is possible and safe"""

    try:
        from agent.llm_factory import chat_completion
        import json

        raw = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            json_response=True,
        )

        result = json.loads(raw)

        log_info(
            "Analysis completed",
            can_auto_fix=result.get("can_auto_fix", False),
            has_root_cause=bool(result.get("root_cause")),
        )

        return {
            **state,
            "summary": result.get("summary", ""),
            "root_cause": result.get("root_cause", ""),
            "suggested_fix": result.get("suggested_fix", ""),
            "can_auto_fix": result.get("can_auto_fix", False),
        }

    except Exception as e:
        log_error("LLM analysis failed", error=str(e))
        return _basic_analysis(state)


def _basic_analysis(state: Dict[str, Any]) -> Dict[str, Any]:
    """Basic rule-based analysis when LLM is not available."""
    logs = state.get("logs", [])
    tickets = state.get("related_tickets", [])

    # Count error patterns
    error_patterns = {}
    for log in logs:
        msg = log.get("message", "")[:50]
        error_patterns[msg] = error_patterns.get(msg, 0) + 1

    top_pattern = (
        max(error_patterns.items(), key=lambda x: x[1]) if error_patterns else ("", 0)
    )

    summary = f"Found {len(logs)} error logs. "
    if top_pattern[1] > 1:
        summary += f"Most common pattern ({top_pattern[1]} occurrences): '{top_pattern[0]}...'. "
    if tickets:
        summary += f"Found {len(tickets)} related Jira tickets."

    return {
        **state,
        "summary": summary,
        "root_cause": "",
        "suggested_fix": "",
        "can_auto_fix": False,
    }


def suggest_action(state: Dict[str, Any]) -> Dict[str, Any]:
    """Suggest next actions including potential Patchy invocation.

    If the error looks auto-fixable and Patchy is not disabled,
    prepares the state for optional Patchy invocation.
    """
    can_auto_fix = state.get("can_auto_fix", False)
    no_patchy = state.get("no_patchy", False)

    if can_auto_fix and not no_patchy:
        log_info("Auto-fix possible, Patchy invocation suggested")
        return {
            **state,
            "patchy_invoked": False,
            "patchy_result": "",
        }

    return {
        **state,
        "can_auto_fix": False,
        "patchy_invoked": False,
        "patchy_result": "",
    }


def invoke_patchy(state: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke Patchy to attempt an automatic fix.

    This node is called when the user confirms they want to
    attempt an automatic fix.
    """
    service = state.get("service")
    if not service:
        return {
            **state,
            "patchy_invoked": True,
            "patchy_result": "Cannot invoke Patchy: no service identified",
        }

    # Get error type from analysis
    root_cause = state.get("root_cause", "")

    # Get a representative log for context
    logs = state.get("logs", [])
    hint = ""
    if logs:
        hint = logs[0].get("message", "")[:100]

    # Get related Jira ticket if any
    tickets = state.get("related_tickets", [])
    jira_key = tickets[0]["key"] if tickets else ""

    try:
        from patchy.patchy_graph import build_graph as build_patchy_graph

        patchy_state = {
            "service": service,
            "error_type": root_cause[:50] if root_cause else "unknown",
            "hint": hint,
            "jira": jira_key,
            "draft": True,
            "mode": "note",  # Safe mode - just creates a note
        }

        graph = build_patchy_graph()
        result = graph.invoke(patchy_state, config={"recursion_limit": 100})

        pr_url = result.get("pr_url", "")
        message = result.get("message", "Patchy completed")

        log_info("Patchy invoked", pr_url=pr_url, message=message)

        return {
            **state,
            "patchy_invoked": True,
            "patchy_result": pr_url if pr_url else message,
        }

    except Exception as e:
        log_error("Patchy invocation failed", error=str(e))
        return {
            **state,
            "patchy_invoked": True,
            "patchy_result": f"Patchy failed: {e}",
        }


def search_jira_direct(
    state: Dict[str, Any], status_filter: Optional[str] = None
) -> Dict[str, Any]:
    """Search Jira directly for tickets matching the query.

    Used with --jira flag to skip Datadog and find duplicate tickets directly.

    Args:
        state: State containing query string
        status_filter: Optional status filter (e.g., "open", "done", "tested")

    Returns:
        State with related_tickets populated from Jira search
    """
    if not jira_is_configured():
        log_error("Jira not configured")
        return {**state, "related_tickets": [], "error": "Jira not configured"}

    config = get_config()
    query = state.get("query", "")

    # Build JQL from query keywords
    keywords = [kw for kw in query.split() if len(kw) >= 3]
    if not keywords:
        return {**state, "related_tickets": [], "error": "Query too short"}

    # Search by summary OR description containing keywords
    keyword_clauses = " OR ".join(
        [f'(summary ~ "{kw}" OR description ~ "{kw}")' for kw in keywords[:5]]
    )
    jql = f"project = {config.jira_project_key} AND ({keyword_clauses})"

    # Add status filter if provided
    if status_filter:
        # Handle common status aliases
        status_lower = status_filter.lower()
        if status_lower == "open":
            jql += ' AND status NOT IN (Done, Closed, Resolved, Tested, "Won\'t Do")'
        elif status_lower in ("closed", "done", "resolved"):
            jql += f' AND status IN (Done, Closed, Resolved, Tested, "Won\'t Do")'
        else:
            # Use exact status name
            jql += f' AND status = "{status_filter}"'

    jql += " ORDER BY created DESC"

    log_info("Searching Jira directly", jql=jql)

    try:
        resp = jira_search(jql, fields="summary,status,created,labels", max_results=20)
        tickets = []
        for issue in (resp or {}).get("issues", []):
            fields = issue.get("fields", {})
            tickets.append(
                {
                    "key": issue.get("key"),
                    "summary": fields.get("summary", ""),
                    "status": fields.get("status", {}).get("name", ""),
                    "created": fields.get("created", "")[:10],
                    "labels": fields.get("labels", []),
                    "score": 0.0,  # JQL match, no similarity score
                }
            )

        log_info("Jira direct search completed", tickets_found=len(tickets))
        return {
            **state,
            "related_tickets": tickets,
            "logs": [],  # No logs in Jira-only mode
            "dd_query": f"[Jira search] {jql}",
        }

    except Exception as e:
        log_error("Jira direct search failed", error=str(e))
        return {**state, "related_tickets": [], "error": f"Jira search failed: {e}"}


def _find_close_transition(transitions: List[Dict]) -> Optional[str]:
    """Find a transition that closes/resolves/completes a ticket.

    Args:
        transitions: List of available transitions from get_transitions

    Returns:
        Transition ID if found, None otherwise
    """
    # Priority order: look for transitions to these target statuses (case-insensitive)
    close_statuses = [
        "done",
        "closed",
        "resolved",
        "complete",
        "completed",
        "fixed",
        "won't do",
        "won't fix",
    ]

    if not transitions:
        return None

    # First pass: match target status name
    for status_name in close_statuses:
        for t in transitions:
            to_status = t.get("to_status", "").lower()
            if status_name in to_status:
                return t.get("id")

    # Second pass: match transition name (e.g., "Close", "Done", "Resolve")
    close_names = [
        "close",
        "done",
        "resolve",
        "complete",
        "finish",
        "won't do",
        "won't fix",
    ]
    for name in close_names:
        for t in transitions:
            t_name = t.get("name", "").lower()
            if name in t_name:
                return t.get("id")

    return None


def consolidate_duplicates(state: Dict[str, Any]) -> Dict[str, Any]:
    """Consolidate duplicate tickets by closing them and linking to primary.

    Keeps the oldest ticket as the primary and closes newer duplicates,
    adding a link comment to each closed ticket.

    Args:
        state: State containing related_tickets from correlate_jira

    Returns:
        Updated state with consolidation results
    """
    from agent.jira.client import (
        add_comment,
        transition_issue,
        link_issues,
        get_transitions,
    )

    tickets = state.get("related_tickets", [])

    if len(tickets) < 2:
        log_info("Not enough tickets to consolidate", count=len(tickets))
        return {
            **state,
            "consolidation_result": "Not enough tickets to consolidate (need at least 2)",
            "consolidated_count": 0,
        }

    # Sort by key (older tickets have lower numbers)
    # DDSIT-154 < DDSIT-159
    def ticket_number(t):
        key = t.get("key", "")
        try:
            return int(key.split("-")[-1])
        except (ValueError, IndexError):
            return 999999

    sorted_tickets = sorted(tickets, key=ticket_number)
    primary = sorted_tickets[0]
    duplicates = sorted_tickets[1:]

    # Filter out already-closed tickets
    closed_statuses = {
        "done",
        "closed",
        "resolved",
        "tested",
        "won't do",
        "won't fix",
        "cancelled",
    }
    open_duplicates = []
    already_closed = []
    for dup in duplicates:
        status = dup.get("status", "").lower()
        if status in closed_statuses:
            already_closed.append(dup.get("key"))
        else:
            open_duplicates.append(dup)

    if already_closed:
        log_info(
            f"Skipping {len(already_closed)} already-closed tickets",
            tickets=already_closed,
        )

    if not open_duplicates:
        log_info("No open duplicates to consolidate")
        return {
            **state,
            "consolidation_result": f"No open duplicates to consolidate ({len(already_closed)} already closed)",
            "consolidated_count": 0,
            "primary_ticket": primary["key"],
            "skipped_closed": already_closed,
        }

    log_info(
        "Consolidating tickets",
        primary=primary.get("key"),
        duplicates=[d.get("key") for d in open_duplicates],
        skipped_closed=len(already_closed),
    )

    consolidated = []
    errors = []

    for dup in open_duplicates:
        dup_key = dup.get("key")
        try:
            # 1. Add link comment to duplicate
            comment = (
                f"This ticket has been identified as a duplicate of [{primary['key']}].\n\n"
                f"Closing this ticket and linking to the primary ticket for tracking.\n\n"
                f"_Consolidated by Sleuth agent._"
            )
            add_comment(dup_key, comment)

            # 2. Link duplicate to primary (if link_issues is available)
            try:
                link_issues(dup_key, primary["key"], link_type="Duplicate")
            except Exception:
                # Link might fail if not supported, continue anyway
                pass

            # 3. Get available transitions and find one that closes the ticket
            transitions = get_transitions(dup_key)
            close_transition_id = (
                _find_close_transition(transitions) if transitions else None
            )

            transitioned = False
            if close_transition_id:
                # Pass resolution="Duplicate" since we're closing as duplicate
                transitioned = transition_issue(
                    dup_key, close_transition_id, resolution="Duplicate"
                )
                if transitioned:
                    log_info(
                        f"Closed ticket using transition",
                        duplicate=dup_key,
                        transition_id=close_transition_id,
                        resolution="Duplicate",
                    )

            if transitioned:
                consolidated.append(dup_key)
                log_info(
                    f"Consolidated duplicate ticket",
                    duplicate=dup_key,
                    primary=primary["key"],
                )
            else:
                # Could not transition but comment was added
                consolidated.append(dup_key)
                available = [
                    f"{t.get('name')} -> {t.get('to_status')}"
                    for t in (transitions or [])
                ]
                log_warning(
                    f"Added comment but could not close ticket",
                    duplicate=dup_key,
                    available_transitions=available,
                )

        except Exception as e:
            log_error(f"Failed to consolidate ticket", duplicate=dup_key, error=str(e))
            errors.append(f"{dup_key}: {str(e)}")

    result_msg = f"Consolidated {len(consolidated)} tickets into {primary['key']}"
    if already_closed:
        result_msg += f" (skipped {len(already_closed)} already closed)"
    if errors:
        result_msg += f". Errors: {len(errors)}"

    return {
        **state,
        "consolidation_result": result_msg,
        "consolidated_count": len(consolidated),
        "primary_ticket": primary["key"],
        "closed_tickets": consolidated,
        "skipped_closed": already_closed,
        "consolidation_errors": errors,
    }
