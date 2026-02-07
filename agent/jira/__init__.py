"""Public Jira API for the agent.

This module re-exports the functions used by the rest of the app, while
internally delegating to submodules for client, matching and utilities.
"""

from __future__ import annotations
import hashlib
import os
from typing import Dict, Any

from .client import (
    get_jira_project_key,
    get_jira_domain,
    is_configured,
    create_issue as jira_create_issue,
    add_comment as jira_add_comment,
    add_labels as jira_add_labels,
)
from .match import find_similar_ticket
from .utils import (
    normalize_log_message,
    load_processed_fingerprints,
    save_processed_fingerprints,
    priority_name_from_severity,
)

__all__ = [
    "find_similar_ticket",
    "create_ticket",
    "comment_on_issue",
    "normalize_log_message",
]

# --- Internal helpers to keep create_ticket() simple ---


def _cap_reached(state: Dict[str, Any]) -> tuple[bool, str | None]:
    """Deprecated: cap is enforced upstream in agent.nodes.ticket."""
    return False, None


def _compute_fingerprint(state: Dict[str, Any]) -> tuple[str, str]:
    log_data = state.get("log_data", {})
    try:
        from .utils import normalize_log_message as _norm

        raw_msg = log_data.get("message", "")
        norm_msg = _norm(raw_msg)
        base = norm_msg or raw_msg
    except Exception:
        base = log_data.get("message", "")
    # Ignore thread to avoid per-thread duplicates
    fp_source = f"{log_data.get('logger','')}|{base}"
    fingerprint = hashlib.sha1(
        fp_source.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    return fingerprint, fp_source


def _base_labels(state: Dict[str, Any]) -> list[str]:
    labels: list[str] = ["datadog-log"]
    try:
        from .utils import (
            normalize_log_message,
        )  # local import to avoid cycles in some IDEs

        norm_msg = normalize_log_message(
            (state.get("log_data") or {}).get("message", "")
        )
        if norm_msg:
            loghash = hashlib.sha1(
                norm_msg.encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:12]
            labels.append(f"loghash-{loghash}")
    except Exception:
        pass
    return labels


def _priority_name(sev: str | None) -> str:
    return priority_name_from_severity(sev)


def _try_handle_duplicate(
    state: Dict[str, Any], title: str, fp_source: str, processed: set[str]
) -> tuple[Dict[str, Any], bool]:
    key, score, existing_summary = find_similar_ticket(title, state)
    if not key:
        return state, False

    print(f"⚠️ Duplicate detected → {key} ({existing_summary}) with score {score:.2f}")

    if os.getenv("COMMENT_ON_DUPLICATE", "true").lower() in ("1", "true", "yes"):
        log_data = state.get("log_data", {})
        comment = (
            f"Detected by Datadog Logs Agent as a likely duplicate (score {score:.2f}).\n"
            f"Logger: {log_data.get('logger', 'N/A')} | Thread: {log_data.get('thread', 'N/A')} | Timestamp: {log_data.get('timestamp', 'N/A')}\n"
            f"Occurrences in last {state.get('window_hours', 48)}h: {(state.get('fp_counts') or {}).get(fp_source, 1)}\n"
            f"Original message: {log_data.get('message', 'N/A')}\n"
        )
        jira_add_comment(key, comment)

    # Seed loghash label to accelerate future lookups
    try:
        norm_msg = normalize_log_message(
            (state.get("log_data") or {}).get("message", "")
        )
        if norm_msg:
            loghash = hashlib.sha1(
                norm_msg.encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:12]
            jira_add_labels(key, [f"loghash-{loghash}"])
    except Exception:
        pass

    if state.get("log_fingerprint"):
        processed.add(state["log_fingerprint"])
        save_processed_fingerprints(processed)

    state = {
        **state,
        "message": f"⚠️ Duplicate in Jira: {key} — {existing_summary}",
        "ticket_created": True,
    }
    return state, True


def _create_or_simulate(
    state: Dict[str, Any], payload: Dict[str, Any], processed: set[str]
) -> Dict[str, Any]:
    auto = os.getenv("AUTO_CREATE_TICKET", "").lower() in ("1", "true", "yes")
    if auto:
        print(f"🚀 Creating ticket in project: {get_jira_project_key()}")
        print(f"🧾 Summary sent to Jira: {payload['fields']['summary']}")
        resp = jira_create_issue(payload)
        if resp:
            issue_key = resp.get("key", "UNKNOWN")
            jira_url = f"https://{get_jira_domain()}/browse/{issue_key}"
            print(f"✅ Jira ticket created: {issue_key}")
            print(f"🔗 {jira_url}")
            state["jira_response_key"] = issue_key
            state["jira_response_url"] = jira_url
            state["jira_response_raw"] = resp
            if state.get("log_fingerprint"):
                processed.add(state["log_fingerprint"])
                save_processed_fingerprints(processed)
        return state

    # Dry-run branch
    print(f"ℹ️ Simulated ticket creation: {payload['fields']['summary']}")
    print("✅ Ticket creation skipped (simulation mode enabled)\n")
    state["ticket_created"] = True
    persist_sim = os.getenv("PERSIST_SIM_FP", "false").lower() in ("1", "true", "yes")
    if persist_sim and state.get("log_fingerprint"):
        processed.add(state["log_fingerprint"])
        save_processed_fingerprints(processed)
    return state


def comment_on_issue(issue_key: str, comment_text: str) -> bool:
    return jira_add_comment(issue_key, comment_text)


def create_ticket(state: Dict[str, Any]) -> Dict[str, Any]:
    print(
        f"🛠️ Entered create_ticket() | AUTO_CREATE_TICKET={os.getenv('AUTO_CREATE_TICKET')}"
    )

    assert (
        "ticket_title" in state and "ticket_description" in state
    ), "Missing LLM fields before jira.create_ticket"
    description = state.get("ticket_description")
    title = state.get("ticket_title")

    print(f"🧾 Title to create: {title}")
    print(
        f"📝 Description to create: {description[:160]}{'...' if description and len(description) > 160 else ''}"
    )

    # Cap is enforced in agent.nodes.ticket._execute_ticket_creation

    if title is None or description is None:
        return state
    if not is_configured():
        return state

    # Fingerprint
    fingerprint, fp_source = _compute_fingerprint(state)
    processed = load_processed_fingerprints()
    if fingerprint in processed:
        print(
            f"🔁 Skipping ticket creation: fingerprint already processed: {fingerprint}"
        )
        return {
            **state,
            "message": "⚠️ Log already processed previously (fingerprint match).",
            "ticket_created": True,
        }
    state["log_fingerprint"] = fingerprint

    # Duplicate handling path (Jira search)
    state, handled = _try_handle_duplicate(state, title, fp_source, processed)
    if handled:
        return state

    # Build payload: prefer pre-built payload from state (refactored path)
    payload = state.get("jira_payload")
    if not payload:
        clean_title = (title or "").replace("**", "").strip()
        labels = _base_labels(state)
        priority_name = _priority_name(state.get("severity"))
        payload = {
            "fields": {
                "project": {"key": get_jira_project_key()},
                "summary": clean_title,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "text": state.get("ticket_description"),
                                    "type": "text",
                                }
                            ],
                        }
                    ],
                },
                "issuetype": {"name": "Bug"},
                "labels": labels,
                "priority": {"name": priority_name},
            }
        }
        # Optional: inject team custom field via env
        try:
            team_field_id = os.getenv("JIRA_TEAM_FIELD_ID")
            team_field_value = os.getenv("JIRA_TEAM_VALUE")
            if team_field_id and team_field_value:
                payload["fields"][team_field_id] = [{"value": team_field_value}]
        except Exception:
            pass

    # Create or simulate
    return _create_or_simulate(state, payload, processed)
