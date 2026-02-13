"""Unit tests for RunConfig dataclass and get_run_config helper."""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import FrozenInstanceError

from agent.run_config import RunConfig, get_run_config

# ---------------------------------------------------------------------------
# Fixture: mock Config (single-tenant)
# ---------------------------------------------------------------------------


def _make_mock_config(**overrides):
    """Return a mock Config with sensible defaults."""
    defaults = dict(
        jira_project_key="TEST",
        jira_similarity_threshold=0.82,
        jira_direct_log_threshold=0.90,
        jira_partial_log_threshold=0.70,
        jira_search_max_results=200,
        jira_search_window_days=365,
        datadog_service="my-svc",
        datadog_env="prod",
        datadog_hours_back=24,
        datadog_limit=50,
        datadog_max_pages=3,
        datadog_timeout=20,
        datadog_statuses="error",
        datadog_query_extra="",
        datadog_query_extra_mode="AND",
        auto_create_ticket=True,
        persist_sim_fp=False,
        comment_on_duplicate=True,
        max_tickets_per_run=5,
        comment_cooldown_minutes=120,
        datadog_logs_url="https://app.datadoghq.eu/logs",
        aggregate_email_not_found=False,
        aggregate_kafka_consumer=False,
        max_title_length=120,
        circuit_breaker_enabled=True,
        fallback_analysis_enabled=True,
    )
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_mock_team(**overrides):
    """Return a mock TeamConfig."""
    defaults = dict(
        team_id="team-alpha",
        jira_project_key="ALPHA",
        datadog_env="staging",
        max_tickets_per_run=None,
        severity_rules=None,
    )
    defaults.update(overrides)
    team = MagicMock()
    for k, v in defaults.items():
        setattr(team, k, v)
    return team


# ---------------------------------------------------------------------------
# RunConfig construction
# ---------------------------------------------------------------------------


class TestRunConfigConstruction:
    """RunConfig factory methods produce correct snapshots."""

    def test_from_config_copies_all_fields(self):
        cfg = _make_mock_config()
        rc = RunConfig.from_config(cfg)

        assert rc.jira_project_key == "TEST"
        assert rc.datadog_service == "my-svc"
        assert rc.datadog_env == "prod"
        assert rc.auto_create_ticket is True
        assert rc.max_tickets_per_run == 5
        assert rc.team_id is None
        assert rc.team_service is None

    def test_from_team_uses_team_overrides(self):
        cfg = _make_mock_config(max_tickets_per_run=10)
        team = _make_mock_team(
            jira_project_key="BETA",
            datadog_env="staging",
            max_tickets_per_run=2,
        )

        rc = RunConfig.from_team(team, "beta-api", cfg)

        assert rc.jira_project_key == "BETA"
        assert rc.datadog_service == "beta-api"
        assert rc.datadog_env == "staging"
        assert rc.max_tickets_per_run == 2  # team override
        assert rc.team_id == "team-alpha"
        assert rc.team_service == "beta-api"

    def test_from_team_inherits_base_when_team_has_no_override(self):
        cfg = _make_mock_config(max_tickets_per_run=7)
        team = _make_mock_team(max_tickets_per_run=None)

        rc = RunConfig.from_team(team, "svc", cfg)

        assert rc.max_tickets_per_run == 7  # falls back to base

    def test_defaults(self):
        rc = RunConfig()

        assert rc.jira_project_key == ""
        assert rc.auto_create_ticket is False
        assert rc.max_tickets_per_run == 3
        assert rc.team_id is None
        assert rc.extras == {}


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestRunConfigImmutability:
    """RunConfig is frozen — no attribute mutation after construction."""

    def test_cannot_mutate_simple_field(self):
        rc = RunConfig(jira_project_key="X")
        with pytest.raises(FrozenInstanceError):
            rc.jira_project_key = "Y"  # type: ignore[misc]

    def test_cannot_mutate_extras(self):
        rc = RunConfig(extras={"a": 1})
        # The dict itself is mutable (frozen only protects the attribute), but
        # the attribute binding cannot be replaced.
        with pytest.raises(FrozenInstanceError):
            rc.extras = {}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# get_run_config helper
# ---------------------------------------------------------------------------


class TestGetRunConfig:
    """get_run_config reads from state or falls back to global Config."""

    def test_returns_state_run_config_when_present(self):
        rc = RunConfig(jira_project_key="PROJ")
        state = {"run_config": rc}

        result = get_run_config(state)

        assert result is rc
        assert result.jira_project_key == "PROJ"

    @patch("agent.config.get_config")
    def test_fallback_builds_from_global_config(self, mock_get_config):
        mock_get_config.return_value = _make_mock_config(jira_project_key="FALLBACK")

        result = get_run_config({})

        assert result.jira_project_key == "FALLBACK"
        mock_get_config.assert_called_once()

    def test_does_not_fallback_when_run_config_present(self):
        rc = RunConfig(jira_project_key="EXPLICIT")
        state = {"run_config": rc}

        with patch("agent.config.get_config") as mock_gc:
            result = get_run_config(state)

        mock_gc.assert_not_called()
        assert result.jira_project_key == "EXPLICIT"


# ---------------------------------------------------------------------------
# Isolation between runs
# ---------------------------------------------------------------------------


class TestRunConfigIsolation:
    """Two RunConfig instances for different teams do not interfere."""

    def test_two_teams_are_independent(self):
        cfg = _make_mock_config(max_tickets_per_run=10)
        team_a = _make_mock_team(
            team_id="team-a",
            jira_project_key="A",
            datadog_env="prod",
            max_tickets_per_run=2,
        )
        team_b = _make_mock_team(
            team_id="team-b",
            jira_project_key="B",
            datadog_env="staging",
            max_tickets_per_run=None,
        )

        rc_a = RunConfig.from_team(team_a, "svc-a", cfg)
        rc_b = RunConfig.from_team(team_b, "svc-b", cfg)

        # A has its own overrides
        assert rc_a.jira_project_key == "A"
        assert rc_a.datadog_env == "prod"
        assert rc_a.max_tickets_per_run == 2

        # B has its own overrides, max_tickets falls back to base
        assert rc_b.jira_project_key == "B"
        assert rc_b.datadog_env == "staging"
        assert rc_b.max_tickets_per_run == 10

        # They are completely independent objects
        assert rc_a is not rc_b
        assert rc_a.team_id != rc_b.team_id
