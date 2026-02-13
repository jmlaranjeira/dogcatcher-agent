"""Immutable per-run configuration.

``RunConfig`` captures all settings that may vary between runs (e.g. across
teams/services in multi-tenant mode).  It is built once per graph invocation
and injected into ``GraphState["run_config"]`` so that every node can read
its values without touching the global singleton or ``os.environ``.

Settings that are truly global and never change between runs (API keys,
model name, cache backend, logging format, …) stay in the ``Config``
singleton accessed via ``get_config()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.config import Config
    from agent.team_config import TeamConfig


@dataclass(frozen=True)
class RunConfig:
    """Immutable, per-run configuration.

    Constructed once before each graph invocation.  Nodes read from
    ``state["run_config"]`` instead of calling ``get_config()`` for
    run-scoped values.
    """

    # --- Jira (run-scoped) --------------------------------------------------
    jira_project_key: str = ""
    jira_similarity_threshold: float = 0.82
    jira_direct_log_threshold: float = 0.90
    jira_partial_log_threshold: float = 0.70
    jira_search_max_results: int = 200
    jira_search_window_days: int = 365

    # --- Datadog (run-scoped) -----------------------------------------------
    datadog_service: str = "myservice"
    datadog_env: str = "dev"
    datadog_hours_back: int = 24
    datadog_limit: int = 50
    datadog_max_pages: int = 3
    datadog_timeout: int = 20
    datadog_statuses: str = "error"
    datadog_query_extra: str = ""
    datadog_query_extra_mode: str = "AND"

    # --- Agent behaviour (run-scoped) ---------------------------------------
    auto_create_ticket: bool = False
    persist_sim_fp: bool = False
    comment_on_duplicate: bool = True
    max_tickets_per_run: int = 3
    comment_cooldown_minutes: int = 120

    # --- Ticket payload helpers -----------------------------------------------
    datadog_logs_url: str = "https://app.datadoghq.eu/logs"
    aggregate_email_not_found: bool = False
    aggregate_kafka_consumer: bool = False
    max_title_length: int = 120

    # --- Resilience (run-scoped so profiles can tweak per-env) --------------
    circuit_breaker_enabled: bool = True
    fallback_analysis_enabled: bool = True

    # --- Multi-tenancy ------------------------------------------------------
    team_id: Optional[str] = None
    team_service: Optional[str] = None

    # --- Extra overrides (dict for forward-compat) --------------------------
    extras: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Config) -> RunConfig:
        """Build a ``RunConfig`` from the current global ``Config``.

        Use this for single-tenant mode: it copies the run-scoped fields
        from the global config into an immutable snapshot.
        """
        return cls(
            jira_project_key=config.jira_project_key,
            jira_similarity_threshold=config.jira_similarity_threshold,
            jira_direct_log_threshold=config.jira_direct_log_threshold,
            jira_partial_log_threshold=config.jira_partial_log_threshold,
            jira_search_max_results=config.jira_search_max_results,
            jira_search_window_days=config.jira_search_window_days,
            datadog_service=config.datadog_service,
            datadog_env=config.datadog_env,
            datadog_hours_back=config.datadog_hours_back,
            datadog_limit=config.datadog_limit,
            datadog_max_pages=config.datadog_max_pages,
            datadog_timeout=config.datadog_timeout,
            datadog_statuses=config.datadog_statuses,
            datadog_query_extra=config.datadog_query_extra,
            datadog_query_extra_mode=config.datadog_query_extra_mode,
            auto_create_ticket=config.auto_create_ticket,
            persist_sim_fp=config.persist_sim_fp,
            comment_on_duplicate=config.comment_on_duplicate,
            max_tickets_per_run=config.max_tickets_per_run,
            comment_cooldown_minutes=config.comment_cooldown_minutes,
            datadog_logs_url=config.datadog_logs_url,
            aggregate_email_not_found=config.aggregate_email_not_found,
            aggregate_kafka_consumer=config.aggregate_kafka_consumer,
            max_title_length=config.max_title_length,
            circuit_breaker_enabled=config.circuit_breaker_enabled,
            fallback_analysis_enabled=config.fallback_analysis_enabled,
        )

    @classmethod
    def from_team(
        cls,
        team: TeamConfig,
        service: str,
        base: Config,
    ) -> RunConfig:
        """Build a ``RunConfig`` for a specific team/service run.

        Team-level overrides (e.g. ``max_tickets_per_run``) are applied on
        top of the base ``Config``.
        """
        return cls(
            jira_project_key=team.jira_project_key,
            jira_similarity_threshold=base.jira_similarity_threshold,
            jira_direct_log_threshold=base.jira_direct_log_threshold,
            jira_partial_log_threshold=base.jira_partial_log_threshold,
            jira_search_max_results=base.jira_search_max_results,
            jira_search_window_days=base.jira_search_window_days,
            datadog_service=service,
            datadog_env=team.datadog_env,
            datadog_hours_back=base.datadog_hours_back,
            datadog_limit=base.datadog_limit,
            datadog_max_pages=base.datadog_max_pages,
            datadog_timeout=base.datadog_timeout,
            datadog_statuses=base.datadog_statuses,
            datadog_query_extra=base.datadog_query_extra,
            datadog_query_extra_mode=base.datadog_query_extra_mode,
            auto_create_ticket=base.auto_create_ticket,
            persist_sim_fp=base.persist_sim_fp,
            comment_on_duplicate=base.comment_on_duplicate,
            max_tickets_per_run=(
                team.max_tickets_per_run
                if team.max_tickets_per_run is not None
                else base.max_tickets_per_run
            ),
            comment_cooldown_minutes=base.comment_cooldown_minutes,
            datadog_logs_url=base.datadog_logs_url,
            aggregate_email_not_found=base.aggregate_email_not_found,
            aggregate_kafka_consumer=base.aggregate_kafka_consumer,
            max_title_length=base.max_title_length,
            circuit_breaker_enabled=base.circuit_breaker_enabled,
            fallback_analysis_enabled=base.fallback_analysis_enabled,
            team_id=team.team_id,
            team_service=service,
        )


def get_run_config(state: Dict[str, Any]) -> RunConfig:
    """Retrieve ``RunConfig`` from graph state, with fallback.

    If the state does not yet contain a ``run_config`` key (e.g. in
    tests or during the migration period), a ``RunConfig`` is built
    from the current global ``Config`` singleton so that callers always
    receive a valid object.
    """
    rc = state.get("run_config")
    if rc is not None:
        return rc

    # Fallback: build from global config (migration compatibility)
    from agent.config import get_config

    return RunConfig.from_config(get_config())
