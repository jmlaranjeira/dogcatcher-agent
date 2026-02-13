"""Unified duplicate detection for the Dogcatcher agent.

This package consolidates all dedup logic into a chain-of-responsibility
pattern, ordered from cheapest to most expensive strategy.
"""

from agent.dedup.result import DuplicateCheckResult
from agent.dedup.detector import DuplicateDetector

__all__ = ["DuplicateCheckResult", "DuplicateDetector"]
