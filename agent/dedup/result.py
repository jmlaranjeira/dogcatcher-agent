"""Data classes for duplicate detection results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DuplicateCheckResult:
    """Result of a duplicate detection check.

    Attributes:
        is_duplicate: Whether the log was identified as a duplicate.
        strategy_name: Name of the strategy that identified the duplicate
            (e.g. ``"in_memory_seen_logs"``, ``"fingerprint_cache"``).
            ``None`` when no duplicate was found.
        existing_ticket_key: Jira issue key of the existing duplicate ticket,
            if one was found in Jira.
        similarity_score: Numeric similarity score when applicable (0.0-1.0).
        message: Human-readable explanation of the result.
    """

    is_duplicate: bool
    strategy_name: Optional[str] = None
    existing_ticket_key: Optional[str] = None
    similarity_score: Optional[float] = None
    message: Optional[str] = None
