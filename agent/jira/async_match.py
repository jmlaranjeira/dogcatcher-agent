"""Async similarity and issue matching for Jira.

This module provides async versions of duplicate detection logic
for use in parallel processing pipelines.
"""

from __future__ import annotations
import hashlib
import importlib.util
from difflib import SequenceMatcher
from typing import Tuple, Optional, Dict, Any

from .async_client import AsyncJiraClient
from .utils import (
    normalize_text,
    normalize_log_message,
    extract_text_from_description,
)
from agent.config import get_config
from agent.utils.logger import log_debug, log_info, log_error
from agent.performance import (
    get_similarity_cache,
    get_performance_metrics,
    optimize_jira_search_params,
    cached_normalize_text,
    cached_normalize_log_message,
)

# Check for rapidfuzz availability
_USE_RAPIDFUZZ = False
_spec = importlib.util.find_spec("rapidfuzz")
if _spec is not None:
    try:
        from rapidfuzz import fuzz  # type: ignore

        _USE_RAPIDFUZZ = True
    except Exception:
        _USE_RAPIDFUZZ = False


def _sim(a: str, b: str) -> float:
    """Calculate similarity between two strings.

    Args:
        a: First string
        b: Second string

    Returns:
        Similarity score 0.0-1.0
    """
    if not a or not b:
        return 0.0
    if _USE_RAPIDFUZZ:
        return fuzz.token_set_ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


async def find_similar_ticket_async(
    summary: str,
    client: AsyncJiraClient,
    state: Optional[dict] = None,
    similarity_threshold: float = None,
) -> Tuple[Optional[str], float, Optional[str]]:
    """Find similar Jira ticket asynchronously.

    Args:
        summary: Ticket summary to search for
        client: Async Jira client instance
        state: Optional state with log data and error type
        similarity_threshold: Minimum similarity score (defaults to config)

    Returns:
        Tuple of (issue_key, score, issue_summary) or (None, 0.0, None)
    """
    # Start performance timing
    metrics = get_performance_metrics()
    metrics.start_timer("find_similar_ticket_async")

    if not client.is_configured():
        log_error("Missing Jira configuration in .env")
        metrics.end_timer("find_similar_ticket_async")
        return None, 0.0, None

    config = get_config()
    if similarity_threshold is None:
        similarity_threshold = config.jira_similarity_threshold

    # Check cache first
    cache = get_similarity_cache()
    cached_result = cache.get(summary, state)
    if cached_result is not None:
        metrics.end_timer("find_similar_ticket_async")
        return cached_result

    # Use cached normalization for better performance
    norm_summary = cached_normalize_text(summary)
    tokens = [t for t in norm_summary.split() if len(t) >= 4]
    if "pre" in tokens and "persist" in tokens and "pre-persist" not in tokens:
        tokens.append("pre-persist")

    # Phrase hints from either title or the current log message
    phrases = []
    current_log_msg = ((state or {}).get("log_data") or {}).get("message", "")
    norm_current_log = cached_normalize_log_message(current_log_msg)
    haystack = (norm_summary + " " + (norm_current_log or "")).strip()
    if "blob not found" in haystack:
        phrases.append("blob not found")
    if "file size" in haystack:
        phrases.append("file size")

    # JQL token filters (summary/description) + labels
    token_clauses = []
    for t in set(tokens[:8]):
        token_clauses.append(f'summary ~ "\\"{t}\\""')
        token_clauses.append(f'description ~ "\\"{t}\\""')
    for p in phrases:
        token_clauses.append(f'summary ~ "\\"{p}\\""')
        token_clauses.append(f'description ~ "\\"{p}\\""')
    token_clauses.append("labels = datadog-log")
    token_filter = (
        " OR ".join(token_clauses) if token_clauses else "labels = datadog-log"
    )

    # Use optimized search parameters
    optimized_params = optimize_jira_search_params()

    jql = (
        f"project = {config.jira_project_key} AND statusCategory != Done AND created >= -{optimized_params['search_window_days']}d AND ("
        + token_filter
        + ") ORDER BY created DESC"
    )
    log_info(
        "JQL query built (async)",
        jql=jql,
        optimized_window=optimized_params["search_window_days"],
    )

    # Fast path: exact label via loghash
    if norm_current_log:
        loghash = hashlib.sha1(
            norm_current_log.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:12]
        jql_hash = (
            f"project = {config.jira_project_key} AND statusCategory != Done AND labels = loghash-{loghash} "
            f"ORDER BY created DESC"
        )
        resp_hash = await client.search(
            jql_hash, fields="summary,description,labels,created,status", max_results=10
        )
        issues_hash = (resp_hash or {}).get("issues", [])
        if issues_hash:
            first = issues_hash[0]
            log_info(
                "Exact duplicate found by label (async)",
                loghash=loghash,
                issue_key=first.get("key"),
            )
            result = (
                first.get("key"),
                1.00,
                first.get("fields", {}).get("summary", ""),
            )
            cache.set(summary, state, result)
            metrics.end_timer("find_similar_ticket_async")
            return result

    # General search with optimized max results
    resp = await client.search(
        jql,
        fields="summary,description,labels,created,status",
        max_results=optimized_params["search_max_results"],
    )
    issues = (resp or {}).get("issues", [])

    etype = (state or {}).get("error_type") if state else None
    logger = ((state or {}).get("log_data") or {}).get("logger") if state else None
    q_text = norm_summary

    best: Tuple[Optional[str], float, Optional[str]] = (None, 0.0, None)
    for issue in issues:
        fields = issue.get("fields", {})
        # Direct Original Log check
        issue_desc_text = extract_text_from_description(fields.get("description"))
        norm_issue_log = (
            normalize_log_message(issue_desc_text.split("Original Log:")[-1].strip())
            if issue_desc_text
            else ""
        )
        log_sim = None
        if norm_current_log and norm_issue_log:
            log_sim = _sim(norm_current_log, norm_issue_log)
            if log_sim >= config.jira_direct_log_threshold:
                log_info(
                    "Direct log match found (async)",
                    similarity=log_sim,
                    issue_key=issue.get("key"),
                    action="short-circuiting as duplicate",
                )
                result = (issue.get("key"), 1.00, fields.get("summary", ""))
                cache.set(summary, state, result)
                metrics.end_timer("find_similar_ticket_async")
                return result

        s = normalize_text(fields.get("summary", ""))
        d = normalize_text(issue_desc_text)
        title_sim = _sim(q_text, s)
        desc_sim = _sim(q_text, d)

        score = 0.6 * title_sim + 0.3 * desc_sim
        if etype and (etype in s or etype in d):
            score += 0.10
        if logger and (logger.lower() in s or logger.lower() in d):
            score += 0.05
        if any(t in s or t in d for t in tokens):
            score += 0.05
        if (
            log_sim is not None
            and config.jira_partial_log_threshold
            <= log_sim
            < config.jira_direct_log_threshold
        ):
            score += 0.05

        if score > best[1]:
            best = (issue.get("key"), score, fields.get("summary", ""))

    # Cache the result
    result = (None, 0.0, None)
    if best[0] and best[1] >= similarity_threshold:
        log_info(
            "Similar ticket found (async)",
            similarity_score=best[1],
            issue_summary=best[2],
        )
        result = best
    else:
        log_info("No similar ticket found with advanced matching (async)")

    # Cache the result for future use
    cache.set(summary, state, result)

    # End performance timing
    duration = metrics.end_timer("find_similar_ticket_async")
    log_debug(
        "find_similar_ticket_async completed", duration_ms=round(duration * 1000, 2)
    )

    return result


async def check_fingerprint_duplicate_async(
    fingerprint: str, client: AsyncJiraClient
) -> Tuple[bool, Optional[str]]:
    """Check if a fingerprint already exists in Jira.

    Args:
        fingerprint: Error fingerprint to check
        client: Async Jira client instance

    Returns:
        Tuple of (is_duplicate, existing_issue_key)
    """
    if not client.is_configured():
        return False, None

    config = get_config()
    jql = (
        f"project = {config.jira_project_key} AND statusCategory != Done AND "
        f"labels = fingerprint-{fingerprint} ORDER BY created DESC"
    )

    resp = await client.search(jql, fields="summary,key", max_results=1)
    issues = (resp or {}).get("issues", [])

    if issues:
        issue_key = issues[0].get("key")
        log_info(
            "Fingerprint duplicate found (async)",
            fingerprint=fingerprint,
            issue_key=issue_key,
        )
        return True, issue_key

    return False, None
