

"""HTTP client helpers for Jira API."""
from __future__ import annotations
import base64
import os
from typing import Optional, Dict, Any

import requests
from dotenv import load_dotenv
from agent.utils.logger import log_api_response, log_error, log_info
from agent.config import get_config

load_dotenv()

# Export configuration constants for backward compatibility
def get_jira_project_key() -> str:
    return get_config().jira.project_key

def get_jira_domain() -> str:
    return get_config().jira.domain

def is_configured() -> bool:
    config = get_config()
    return all([config.jira.domain, config.jira.user, config.jira.api_token, config.jira.project_key])


def _headers() -> Dict[str, str]:
    config = get_config()
    auth_string = f"{config.jira.user}:{config.jira.api_token}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()
    return {"Authorization": f"Basic {auth_encoded}", "Content-Type": "application/json"}


def search(jql: str, *, fields: str = "summary,description", max_results: int = None) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    config = get_config()
    if max_results is None:
        max_results = config.jira.search_max_results
    url = f"https://{config.jira.domain}/rest/api/3/search"
    try:
        resp = requests.get(url, headers=_headers(), params={
            "jql": jql,
            "maxResults": max_results,
            "fields": fields,
        })
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
    url = f"https://{config.jira.domain}/rest/api/3/issue"
    try:
        resp = requests.post(url, headers=_headers(), json=payload)
        resp.raise_for_status()
        response_data = resp.json()
        log_api_response("Jira issue creation", resp.status_code, response_data)
        return response_data
    except requests.RequestException as e:
        log_error("Failed to create Jira issue", error=str(e))
        return None


def add_comment(issue_key: str, comment_text: str) -> bool:
    if not is_configured():
        log_error("Missing Jira configuration for commenting")
        return False
    config = get_config()
    url = f"https://{config.jira.domain}/rest/api/3/issue/{issue_key}/comment"
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": comment_text}]}
            ],
        }
    }
    try:
        resp = requests.post(url, headers=_headers(), json=body)
        log_api_response("Jira comment addition", resp.status_code)
        return resp.status_code in (200, 201)
    except requests.RequestException as e:
        log_error("Failed to add comment", error=str(e), issue_key=issue_key)
        return False


def add_labels(issue_key: str, labels_to_add: list[str]) -> bool:
    if not is_configured() or not labels_to_add:
        return False if not is_configured() else True
    config = get_config()
    url = f"https://{config.jira.domain}/rest/api/3/issue/{issue_key}"
    body = {"update": {"labels": [{"add": lbl} for lbl in labels_to_add]}}
    try:
        resp = requests.put(url, headers=_headers(), json=body)
        log_api_response("Jira label addition", resp.status_code)
        return resp.status_code in (200, 204)
    except requests.RequestException as e:
        log_error("Failed to add labels", error=str(e), issue_key=issue_key, labels=labels_to_add)
        return False