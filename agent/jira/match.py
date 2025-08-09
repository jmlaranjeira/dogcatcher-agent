

"""Similarity and issue matching for Jira."""
from __future__ import annotations
import base64
import hashlib
import importlib.util
from difflib import SequenceMatcher
from typing import Tuple, Optional

from . import client
from .utils import (
    normalize_text,
    normalize_log_message,
    extract_text_from_description,
)

_USE_RAPIDFUZZ = False
_spec = importlib.util.find_spec("rapidfuzz")
if _spec is not None:
    try:
        from rapidfuzz import fuzz  # type: ignore
        _USE_RAPIDFUZZ = True
    except Exception:
        _USE_RAPIDFUZZ = False


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if _USE_RAPIDFUZZ:
        return fuzz.token_set_ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


def find_similar_ticket(
    summary: str,
    state: Optional[dict] = None,
    similarity_threshold: float = 0.82,
):
    """Return (issue_key, score, issue_summary) if >= threshold, else (None, 0.0, None)."""
    if not client.is_configured():
        print("❌ Missing Jira configuration in .env")
        return None, 0.0, None

    norm_summary = normalize_text(summary)
    tokens = [t for t in norm_summary.split() if len(t) >= 4]
    if "pre" in tokens and "persist" in tokens and "pre-persist" not in tokens:
        tokens.append("pre-persist")

    # Phrase hints from either title or the current log message
    phrases = []
    current_log_msg = ((state or {}).get("log_data") or {}).get("message", "")
    norm_current_log = normalize_log_message(current_log_msg)
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
    token_clauses.append('labels = datadog-log')
    token_filter = " OR ".join(token_clauses) if token_clauses else "labels = datadog-log"

    jql = (
        f"project = {client.JIRA_PROJECT_KEY} AND statusCategory != Done AND created >= -365d AND ("
        + token_filter
        + ") ORDER BY created DESC"
    )
    print(f"🔍 JQL used: {jql}")

    # Fast path: exact label via loghash
    if norm_current_log:
        loghash = hashlib.sha1(norm_current_log.encode("utf-8")).hexdigest()[:12]
        jql_hash = (
            f"project = {client.JIRA_PROJECT_KEY} AND statusCategory != Done AND labels = loghash-{loghash} "
            f"ORDER BY created DESC"
        )
        resp_hash = client.search(jql_hash, fields="summary,description,labels,created,status", max_results=10)
        issues_hash = (resp_hash or {}).get("issues", [])
        if issues_hash:
            first = issues_hash[0]
            print(f"⚠️ Exact duplicate by label loghash-{loghash}: {first.get('key')}")
            return first.get("key"), 1.00, first.get("fields", {}).get("summary", "")

    # General search
    resp = client.search(jql, fields="summary,description,labels,created,status", max_results=200)
    issues = (resp or {}).get("issues", [])

    etype = (state or {}).get("error_type") if state else None
    logger = ((state or {}).get("log_data") or {}).get("logger") if state else None
    q_text = norm_summary

    best: Tuple[Optional[str], float, Optional[str]] = (None, 0.0, None)
    for issue in issues:
        fields = issue.get("fields", {})
        # Direct Original Log check
        issue_desc_text = extract_text_from_description(fields.get("description"))
        norm_issue_log = normalize_log_message(issue_desc_text.split("Original Log:")[-1].strip()) if issue_desc_text else ""
        log_sim = None
        if norm_current_log and norm_issue_log:
            log_sim = _sim(norm_current_log, norm_issue_log)
            if log_sim >= 0.90:
                print(
                    f"⚠️ Direct log match found (sim={log_sim:.2f}) against {issue.get('key')} — short-circuiting as duplicate."
                )
                return issue.get("key"), 1.00, fields.get("summary", "")

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
        if log_sim is not None and 0.70 <= log_sim < 0.90:
            score += 0.05

        if score > best[1]:
            best = (issue.get("key"), score, fields.get("summary", ""))

    if best[0] and best[1] >= similarity_threshold:
        print(f"⚠️ Similar ticket found with score {best[1]:.2f}: {best[2]}")
        return best

    print("✅ No similar ticket found with advanced matching.")
    return None, 0.0, None