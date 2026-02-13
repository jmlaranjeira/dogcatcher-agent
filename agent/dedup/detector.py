"""Orchestrator for the duplicate-detection chain.

The ``DuplicateDetector`` runs strategies in order from cheapest to most
expensive, short-circuiting on the first positive match.  This replaces the
scattered dedup logic that previously lived in ``graph.py``,
``nodes/ticket.py``, and ``jira/match.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.dedup.result import DuplicateCheckResult
from agent.dedup.strategies import (
    DedupStrategy,
    InMemorySeenLogs,
    FingerprintCache,
    LoghashLabelSearch,
    ErrorTypeLabelSearch,
    SimilaritySearch,
)
from agent.utils.logger import log_info, log_debug


def build_default_strategies() -> List[DedupStrategy]:
    """Build the default ordered chain of dedup strategies.

    The order matters — cheapest first:
      1. InMemorySeenLogs   – O(1), in-memory set
      2. FingerprintCache   – O(1), disk read
      3. LoghashLabelSearch – 1 Jira API call
      4. ErrorTypeLabelSearch – 1 Jira API call
      5. SimilaritySearch   – 1+ Jira API calls
    """
    return [
        InMemorySeenLogs(),
        FingerprintCache(),
        LoghashLabelSearch(),
        ErrorTypeLabelSearch(),
        SimilaritySearch(),
    ]


class DuplicateDetector:
    """Orchestrate duplicate detection through a chain of strategies.

    Args:
        strategies: Ordered list of strategies to run.  Defaults to
            ``build_default_strategies()`` if *None*.

    Usage::

        detector = DuplicateDetector()
        result = detector.check(log_data, state)
        if result.is_duplicate:
            # skip ticket creation
            ...
    """

    def __init__(self, strategies: Optional[List[DedupStrategy]] = None):
        self.strategies = (
            strategies if strategies is not None else build_default_strategies()
        )

    def check(self, log_data: dict, state: Dict[str, Any]) -> DuplicateCheckResult:
        """Run each strategy in order; return on the first duplicate hit.

        Args:
            log_data: The current log entry dict (usually ``state["log_data"]``).
            state: Full graph state.

        Returns:
            ``DuplicateCheckResult`` — either a positive match from the first
            strategy that identified a duplicate, or a negative result
            indicating the log is unique.
        """
        log_debug(
            "Starting duplicate detection chain",
            strategy_count=len(self.strategies),
        )

        for strategy in self.strategies:
            result = strategy.check(log_data, state)
            if result.is_duplicate:
                log_info(
                    "Duplicate detected",
                    strategy=result.strategy_name,
                    existing_key=result.existing_ticket_key,
                    score=result.similarity_score,
                )
                return result

        log_debug("No duplicates found across all strategies")
        return DuplicateCheckResult(is_duplicate=False)
