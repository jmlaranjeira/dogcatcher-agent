"""Shared state types for the Datadog → LLM → Jira pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Set, TypedDict

from agent.run_config import RunConfig  # noqa: F401 – needed at runtime for TypedDict


class GraphState(TypedDict, total=False):
    # Per-run configuration (immutable, injected at graph invocation)
    run_config: RunConfig

    # Core traversal
    logs: List[dict]
    log_index: int
    seen_logs: Set[str]
    finished: bool
    skipped_duplicate: bool

    # Produced by analyze_log → consumed by create_ticket
    create_ticket: bool
    error_type: str
    ticket_title: str
    ticket_description: str
    severity: str

    # Context passed around
    log_message: str
    log_data: Dict[str, Any]
    message: str

    # Outputs / side info
    ticket_created: bool
    jira_payload: Dict[str, Any]

    # Multi-tenancy (absent in single-tenant mode)
    team_id: str
    team_service: str


# Alias kept for compatibility if any module imported it previously
state_schema = GraphState
