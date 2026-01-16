"""Async HTTP client for Jira API using httpx.

This module provides async Jira API operations for parallel processing.
Uses connection pooling for optimal performance.
"""
from __future__ import annotations
import base64
from typing import Optional, Dict, Any
import httpx

from agent.utils.logger import log_api_response, log_error, log_info
from agent.config import get_config


class AsyncJiraClient:
    """Async Jira API client with connection pooling."""

    def __init__(self):
        """Initialize async client with configuration."""
        self.config = get_config()
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Context manager entry - creates HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20
            )
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> Dict[str, str]:
        """Generate authorization headers.

        Returns:
            Headers dictionary with Basic auth
        """
        auth_string = f"{self.config.jira_user}:{self.config.jira_api_token}"
        auth_encoded = base64.b64encode(auth_string.encode()).decode()
        return {
            "Authorization": f"Basic {auth_encoded}",
            "Content-Type": "application/json"
        }

    def is_configured(self) -> bool:
        """Check if Jira is properly configured.

        Returns:
            True if all required config present
        """
        return all([
            self.config.jira_domain,
            self.config.jira_user,
            self.config.jira_api_token,
            self.config.jira_project_key,
        ])

    async def search(
        self,
        jql: str,
        *,
        fields: str = "summary,description",
        max_results: int = None
    ) -> Optional[Dict[str, Any]]:
        """Search Jira issues with JQL.

        Args:
            jql: JQL query string
            fields: Comma-separated field names to return
            max_results: Maximum results (defaults to config)

        Returns:
            Search results or None on error
        """
        if not self.is_configured():
            log_error("Jira not configured")
            return None

        if not self._client:
            log_error("AsyncJiraClient not initialized - use 'async with' context")
            return None

        if max_results is None:
            max_results = self.config.jira_search_max_results

        url = f"https://{self.config.jira_domain}/rest/api/3/search"

        try:
            # Use POST instead of GET (Atlassian deprecated GET for search)
            resp = await self._client.post(
                url,
                headers=self._headers(),
                json={
                    "jql": jql,
                    "maxResults": max_results,
                    "fields": [f.strip() for f in fields.split(",")],
                }
            )
            resp.raise_for_status()
            log_api_response("Jira search (async)", resp.status_code)
            return resp.json()

        except httpx.HTTPError as e:
            log_error("Jira async search failed", error=str(e), jql=jql)
            return None

    async def create_issue(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a Jira issue.

        Args:
            payload: Issue creation payload

        Returns:
            Created issue data or None on error
        """
        if not self.is_configured():
            log_error("Jira not configured")
            return None

        if not self._client:
            log_error("AsyncJiraClient not initialized - use 'async with' context")
            return None

        url = f"https://{self.config.jira_domain}/rest/api/3/issue"

        try:
            resp = await self._client.post(
                url,
                headers=self._headers(),
                json=payload
            )
            resp.raise_for_status()
            response_data = resp.json()
            log_api_response("Jira issue creation (async)", resp.status_code, response_data)
            return response_data

        except httpx.HTTPError as e:
            # Try to log response body for diagnosis
            resp_preview = None
            try:
                if hasattr(e, "response") and e.response is not None:
                    resp_preview = e.response.text[:500]
            except Exception:
                resp_preview = None

            if resp_preview:
                log_error("Failed to create Jira issue (async)", error=str(e), response=resp_preview)
            else:
                log_error("Failed to create Jira issue (async)", error=str(e))
            return None

    async def add_comment(self, issue_key: str, comment_text: str) -> bool:
        """Add a comment to a Jira issue.

        Args:
            issue_key: Issue key (e.g., "PROJECT-123")
            comment_text: Comment text

        Returns:
            True if successful
        """
        if not self.is_configured():
            log_error("Missing Jira configuration for commenting")
            return False

        if not self._client:
            log_error("AsyncJiraClient not initialized - use 'async with' context")
            return False

        url = f"https://{self.config.jira_domain}/rest/api/3/issue/{issue_key}/comment"
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
            resp = await self._client.post(
                url,
                headers=self._headers(),
                json=body
            )
            log_api_response("Jira comment addition (async)", resp.status_code)
            return resp.status_code in (200, 201)

        except httpx.HTTPError as e:
            log_error("Failed to add comment (async)", error=str(e), issue_key=issue_key)
            return False

    async def add_labels(self, issue_key: str, labels_to_add: list[str]) -> bool:
        """Add labels to a Jira issue.

        Args:
            issue_key: Issue key (e.g., "PROJECT-123")
            labels_to_add: List of label strings

        Returns:
            True if successful
        """
        if not self.is_configured() or not labels_to_add:
            return False if not self.is_configured() else True

        if not self._client:
            log_error("AsyncJiraClient not initialized - use 'async with' context")
            return False

        url = f"https://{self.config.jira_domain}/rest/api/3/issue/{issue_key}"
        body = {"update": {"labels": [{"add": lbl} for lbl in labels_to_add]}}

        try:
            resp = await self._client.put(
                url,
                headers=self._headers(),
                json=body
            )
            log_api_response("Jira label addition (async)", resp.status_code)
            return resp.status_code in (200, 204)

        except httpx.HTTPError as e:
            log_error("Failed to add labels (async)", error=str(e), issue_key=issue_key, labels=labels_to_add)
            return False


# Convenience functions for backward compatibility
_global_client: Optional[AsyncJiraClient] = None


async def get_client() -> AsyncJiraClient:
    """Get or create the global async Jira client.

    Returns:
        Async Jira client instance
    """
    global _global_client
    if _global_client is None:
        _global_client = AsyncJiraClient()
    return _global_client


async def search_async(
    jql: str,
    *,
    fields: str = "summary,description",
    max_results: int = None
) -> Optional[Dict[str, Any]]:
    """Async Jira search convenience function.

    Args:
        jql: JQL query string
        fields: Comma-separated field names
        max_results: Maximum results

    Returns:
        Search results or None
    """
    async with AsyncJiraClient() as client:
        return await client.search(jql, fields=fields, max_results=max_results)


async def create_issue_async(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Async Jira issue creation convenience function.

    Args:
        payload: Issue creation payload

    Returns:
        Created issue data or None
    """
    async with AsyncJiraClient() as client:
        return await client.create_issue(payload)


async def add_comment_async(issue_key: str, comment_text: str) -> bool:
    """Async Jira comment addition convenience function.

    Args:
        issue_key: Issue key
        comment_text: Comment text

    Returns:
        True if successful
    """
    async with AsyncJiraClient() as client:
        return await client.add_comment(issue_key, comment_text)


async def add_labels_async(issue_key: str, labels_to_add: list[str]) -> bool:
    """Async Jira label addition convenience function.

    Args:
        issue_key: Issue key
        labels_to_add: List of labels

    Returns:
        True if successful
    """
    async with AsyncJiraClient() as client:
        return await client.add_labels(issue_key, labels_to_add)
