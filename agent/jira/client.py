"""HTTP client helpers for Jira API."""

from __future__ import annotations
import base64
import os
from typing import Optional, Dict, Any, List

import requests
from dotenv import load_dotenv
from agent.utils.logger import log_api_response, log_error, log_info
from agent.config import get_config

load_dotenv()


# Export configuration constants for backward compatibility
def get_jira_project_key() -> str:
    # Use flattened config fields (backward-compatible accessor)
    return get_config().jira_project_key


def get_jira_domain() -> str:
    # Use flattened config fields (backward-compatible accessor)
    return get_config().jira_domain


def is_configured() -> bool:
    config = get_config()
    return all(
        [
            config.jira_domain,
            config.jira_user,
            config.jira_api_token,
            config.jira_project_key,
        ]
    )


def _headers() -> Dict[str, str]:
    config = get_config()
    auth_string = f"{config.jira_user}:{config.jira_api_token}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()
    return {
        "Authorization": f"Basic {auth_encoded}",
        "Content-Type": "application/json",
    }


def search(
    jql: str, *, fields: str = "summary,description", max_results: int = None
) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    config = get_config()
    if max_results is None:
        max_results = config.jira_search_max_results
    # Use new /search/jql endpoint (old /search was deprecated Oct 2025)
    url = f"https://{config.jira_domain}/rest/api/3/search/jql"
    try:
        resp = requests.post(
            url,
            headers=_headers(),
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": [f.strip() for f in fields.split(",")],
            },
            timeout=30,
        )
        resp.raise_for_status()
        log_api_response("Jira search", resp.status_code)
        return resp.json()
    except requests.RequestException as e:
        log_error("Jira search failed", error=str(e), jql=jql)
        return None


def create_issue(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    config = get_config()
    url = f"https://{config.jira_domain}/rest/api/3/issue"
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        response_data = resp.json()
        log_api_response("Jira issue creation", resp.status_code, response_data)
        return response_data
    except requests.RequestException as e:
        # Try to log response body for diagnosis (field errors, permissions, etc.)
        resp_preview = None
        try:
            if hasattr(e, "response") and e.response is not None:
                resp_preview = e.response.text[:500]
        except Exception:
            resp_preview = None
        if resp_preview:
            log_error(
                "Failed to create Jira issue", error=str(e), response=resp_preview
            )
        else:
            log_error("Failed to create Jira issue", error=str(e))
        return None


def add_comment(issue_key: str, comment_text: str) -> bool:
    if not is_configured():
        log_error("Missing Jira configuration for commenting")
        return False
    config = get_config()
    url = f"https://{config.jira_domain}/rest/api/3/issue/{issue_key}/comment"
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": comment_text}],
                }
            ],
        }
    }
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=30)
        log_api_response("Jira comment addition", resp.status_code)
        return resp.status_code in (200, 201)
    except requests.RequestException as e:
        log_error("Failed to add comment", error=str(e), issue_key=issue_key)
        return False


def add_labels(issue_key: str, labels_to_add: list[str]) -> bool:
    if not is_configured() or not labels_to_add:
        return False if not is_configured() else True
    config = get_config()
    url = f"https://{config.jira_domain}/rest/api/3/issue/{issue_key}"
    body = {"update": {"labels": [{"add": lbl} for lbl in labels_to_add]}}
    try:
        resp = requests.put(url, headers=_headers(), json=body, timeout=30)
        log_api_response("Jira label addition", resp.status_code)
        return resp.status_code in (200, 204)
    except requests.RequestException as e:
        log_error(
            "Failed to add labels",
            error=str(e),
            issue_key=issue_key,
            labels=labels_to_add,
        )
        return False


def get_transitions(issue_key: str) -> Optional[List[Dict[str, Any]]]:
    """Get available transitions for a Jira issue.

    Args:
        issue_key: Issue key (e.g., DDSIT-123)

    Returns:
        List of available transitions with id, name, and target status
    """
    if not is_configured():
        return None
    config = get_config()
    url = f"https://{config.jira_domain}/rest/api/3/issue/{issue_key}/transitions"
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        transitions = []
        for t in data.get("transitions", []):
            transitions.append(
                {
                    "id": t.get("id"),
                    "name": t.get("name", ""),
                    "to_status": t.get("to", {}).get("name", ""),
                }
            )
        log_api_response("Jira get transitions", resp.status_code)
        return transitions
    except requests.RequestException as e:
        log_error("Failed to get transitions", error=str(e), issue_key=issue_key)
        return None


def transition_issue(
    issue_key: str, transition_id: str, resolution: Optional[str] = None
) -> bool:
    """Transition a Jira issue to a new status.

    Args:
        issue_key: Issue key (e.g., DDSIT-123)
        transition_id: Transition ID (varies by project workflow)
        resolution: Optional resolution name (e.g., "Duplicate", "Done", "Won't Do")

    Returns:
        True if transition successful
    """
    if not is_configured():
        return False
    config = get_config()
    url = f"https://{config.jira_domain}/rest/api/3/issue/{issue_key}/transitions"
    body: Dict[str, Any] = {"transition": {"id": transition_id}}

    # Add resolution field if provided (required for some transitions like Done)
    if resolution:
        body["fields"] = {"resolution": {"name": resolution}}

    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=30)
        log_api_response("Jira transition", resp.status_code)
        return resp.status_code in (200, 204)
    except requests.RequestException as e:
        log_error(
            "Failed to transition issue",
            error=str(e),
            issue_key=issue_key,
            transition_id=transition_id,
        )
        return False


def link_issues(from_key: str, to_key: str, link_type: str = "Duplicate") -> bool:
    """Create a link between two Jira issues.

    Args:
        from_key: Source issue key
        to_key: Target issue key
        link_type: Link type name (e.g., "Duplicate", "Blocks", "Relates")

    Returns:
        True if link created successfully
    """
    if not is_configured():
        return False
    config = get_config()
    url = f"https://{config.jira_domain}/rest/api/3/issueLink"
    body = {
        "type": {"name": link_type},
        "inwardIssue": {"key": from_key},
        "outwardIssue": {"key": to_key},
    }
    try:
        resp = requests.post(url, headers=_headers(), json=body, timeout=30)
        log_api_response("Jira issue link", resp.status_code)
        return resp.status_code in (200, 201)
    except requests.RequestException as e:
        log_error(
            "Failed to link issues", error=str(e), from_key=from_key, to_key=to_key
        )
        return False
