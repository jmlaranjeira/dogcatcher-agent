"""Individual duplicate-detection strategies.

Each strategy implements the ``DedupStrategy`` protocol: a ``check`` method
that receives the current log data and graph state and returns a
``DuplicateCheckResult``.  Strategies are ordered from cheapest (O(1), in-memory)
to most expensive (multiple Jira API calls).
"""

from __future__ import annotations

import abc
from typing import Any, Dict, Optional, Set

from agent.dedup.result import DuplicateCheckResult
from agent.jira import client as jira_client
from agent.jira import find_similar_ticket
from agent.jira.client import search as jira_search
from agent.jira.utils import (
    normalize_log_message,
    compute_fingerprint,
    load_processed_fingerprints,
    save_processed_fingerprints,
    compute_loghash,
)
from agent.config import get_config
from agent.run_config import get_run_config
from agent.utils.logger import log_info, log_debug, log_error

# ---------------------------------------------------------------------------
# Base protocol
# ---------------------------------------------------------------------------


class DedupStrategy(abc.ABC):
    """Abstract base for duplicate-detection strategies."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short machine-readable name for audit logs."""

    @abc.abstractmethod
    def check(self, log_data: dict, state: Dict[str, Any]) -> DuplicateCheckResult:
        """Run the strategy.

        Args:
            log_data: The current log entry dict (``state["log_data"]``).
            state: Full graph state for access to ``seen_logs``, ``error_type``, etc.

        Returns:
            A ``DuplicateCheckResult``.  When ``is_duplicate`` is ``False``
            the detector moves on to the next strategy in the chain.
        """


# ---------------------------------------------------------------------------
# Strategy 1 – In-memory seen logs (per-run, O(1))
# ---------------------------------------------------------------------------


class InMemorySeenLogs(DedupStrategy):
    """Skip logs already processed *in this run*.

    Uses a normalized ``logger|message`` key (thread stripped to avoid
    duplicates across worker threads).  The set is stored in ``state["seen_logs"]``.
    """

    @property
    def name(self) -> str:
        return "in_memory_seen_logs"

    def check(self, log_data: dict, state: Dict[str, Any]) -> DuplicateCheckResult:
        seen: Set[str] = state.get("seen_logs", set())
        raw_msg = log_data.get("message", "<no message>")
        norm_msg = normalize_log_message(raw_msg)
        log_key = f"{log_data.get('logger', 'unknown')}|{norm_msg or raw_msg}"

        if log_key in seen:
            log_debug("Duplicate found in seen_logs", log_key=log_key)
            return DuplicateCheckResult(
                is_duplicate=True,
                strategy_name=self.name,
                message="Log already analyzed in this run (seen_logs)",
            )

        return DuplicateCheckResult(is_duplicate=False)


# ---------------------------------------------------------------------------
# Strategy 2 – Fingerprint cache (persisted on disk, O(1))
# ---------------------------------------------------------------------------


class FingerprintCache(DedupStrategy):
    """Check the fingerprint against both the in-run set and the on-disk cache.

    The fingerprint combines ``error_type`` + normalized message so that
    identical errors from different loggers collapse to the same key.
    """

    @property
    def name(self) -> str:
        return "fingerprint_cache"

    def check(self, log_data: dict, state: Dict[str, Any]) -> DuplicateCheckResult:
        raw_msg = log_data.get("message", "")
        error_type = state.get("error_type", "unknown")
        fingerprint = compute_fingerprint(error_type, raw_msg)

        # In-run fingerprints (tickets already created in this execution)
        created_in_run: Set[str] = state.get("created_fingerprints", set())

        # Persisted fingerprints (from previous runs)
        team_id: Optional[str] = state.get("team_id")
        processed = load_processed_fingerprints(team_id)

        if fingerprint in created_in_run or fingerprint in processed:
            log_info("Duplicate found in fingerprint cache", fingerprint=fingerprint)
            return DuplicateCheckResult(
                is_duplicate=True,
                strategy_name=self.name,
                message="Log already processed previously (fingerprint match)",
            )

        return DuplicateCheckResult(is_duplicate=False)


# ---------------------------------------------------------------------------
# Strategy 3 – Loghash label search (1 Jira API call)
# ---------------------------------------------------------------------------


class LoghashLabelSearch(DedupStrategy):
    """Fast-path: search Jira for a ticket labelled with the same loghash.

    The loghash is a 12-char SHA-1 of the normalized log message.  If a
    ticket carries a ``loghash-<hash>`` label it is an exact content match.
    """

    @property
    def name(self) -> str:
        return "loghash_label_search"

    def check(self, log_data: dict, state: Dict[str, Any]) -> DuplicateCheckResult:
        raw_msg = log_data.get("message", "")
        norm_msg = normalize_log_message(raw_msg)
        if not norm_msg:
            return DuplicateCheckResult(is_duplicate=False)

        if not jira_client.is_configured():
            return DuplicateCheckResult(is_duplicate=False)

        loghash = compute_loghash(raw_msg)
        if not loghash:
            return DuplicateCheckResult(is_duplicate=False)

        rc = get_run_config(state)
        jql = (
            f"project = {rc.jira_project_key} "
            f"AND statusCategory != Done "
            f"AND labels = loghash-{loghash} "
            f"ORDER BY created DESC"
        )

        try:
            resp = jira_client.search(
                jql,
                fields="summary,description,labels,created,status",
                max_results=10,
            )
            issues = (resp or {}).get("issues", [])
            if issues:
                first = issues[0]
                key = first.get("key")
                summary = first.get("fields", {}).get("summary", "")
                log_info(
                    "Exact duplicate found by loghash label",
                    loghash=loghash,
                    issue_key=key,
                )
                return DuplicateCheckResult(
                    is_duplicate=True,
                    strategy_name=self.name,
                    existing_ticket_key=key,
                    similarity_score=1.0,
                    message=f"Exact duplicate by loghash: {key} - {summary}",
                )
        except Exception as e:
            log_error("Error during loghash label search", error=str(e))

        return DuplicateCheckResult(is_duplicate=False)


# ---------------------------------------------------------------------------
# Strategy 4 – Error-type label search (1 Jira API call)
# ---------------------------------------------------------------------------


class ErrorTypeLabelSearch(DedupStrategy):
    """Search Jira for a recent ticket with the same ``error_type`` label.

    This catches cross-logger duplicates: two different loggers producing the
    same category of error within the last 7 days.
    """

    @property
    def name(self) -> str:
        return "error_type_label_search"

    def check(self, log_data: dict, state: Dict[str, Any]) -> DuplicateCheckResult:
        error_type = state.get("error_type", "")
        if not error_type or error_type == "unknown":
            return DuplicateCheckResult(is_duplicate=False)

        rc = get_run_config(state)

        jql = (
            f"project = {rc.jira_project_key} "
            f"AND labels = datadog-log "
            f"AND labels = {error_type} "
            f"AND created >= -7d "
            f"ORDER BY created DESC"
        )

        try:
            resp = jira_search(jql, max_results=1)
            if resp and resp.get("issues"):
                existing = resp["issues"][0]
                key = existing.get("key")
                summary = existing.get("fields", {}).get("summary", "")
                log_info(
                    "Duplicate found by error_type label",
                    error_type=error_type,
                    existing_key=key,
                )

                # Update fingerprint cache so future runs skip this log
                raw_msg = log_data.get("message", "")
                fp = compute_fingerprint(state.get("error_type", "unknown"), raw_msg)
                team_id = state.get("team_id")
                processed = load_processed_fingerprints(team_id)
                processed.add(fp)
                save_processed_fingerprints(processed, team_id)

                return DuplicateCheckResult(
                    is_duplicate=True,
                    strategy_name=self.name,
                    existing_ticket_key=key,
                    similarity_score=0.95,
                    message=f"Recent ticket with same error_type: {key} - {summary}",
                )
        except Exception as e:
            log_error("Error during error_type duplicate check", error=str(e))

        return DuplicateCheckResult(is_duplicate=False)


# ---------------------------------------------------------------------------
# Strategy 5 – Similarity search (1+ Jira API calls, composite scoring)
# ---------------------------------------------------------------------------


class SimilaritySearch(DedupStrategy):
    """Full similarity search via ``find_similar_ticket``.

    This is the most expensive strategy: it builds a JQL query with token
    filters, fetches candidate issues, and scores each one with a weighted
    composite of title, description, error_type, logger, and direct log
    similarity.
    """

    @property
    def name(self) -> str:
        return "similarity_search"

    def check(self, log_data: dict, state: Dict[str, Any]) -> DuplicateCheckResult:
        title = state.get("ticket_title", "")
        if not title:
            return DuplicateCheckResult(is_duplicate=False)

        try:
            key, score, summary = find_similar_ticket(title, state)
            if key:
                log_info(
                    "Duplicate found by similarity search",
                    issue_key=key,
                    similarity_score=score,
                )

                # Update fingerprint cache
                raw_msg = log_data.get("message", "")
                fp = compute_fingerprint(state.get("error_type", "unknown"), raw_msg)
                team_id = state.get("team_id")
                processed = load_processed_fingerprints(team_id)
                processed.add(fp)
                save_processed_fingerprints(processed, team_id)

                return DuplicateCheckResult(
                    is_duplicate=True,
                    strategy_name=self.name,
                    existing_ticket_key=key,
                    similarity_score=score,
                    message=f"Duplicate in Jira: {key} - {summary}",
                )
        except Exception as e:
            log_error("Error during similarity search", error=str(e))

        return DuplicateCheckResult(is_duplicate=False)
