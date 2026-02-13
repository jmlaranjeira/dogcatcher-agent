"""Unit tests for team_env_override context manager."""

import os
import pytest
from unittest.mock import patch, call

from agent.team_config import TeamConfig
from agent.utils.env_context import team_env_override, _TEAM_SCOPED_VARS


def _make_team(**overrides) -> TeamConfig:
    """Helper to build a TeamConfig with sensible defaults."""
    defaults = {
        "team_id": "team-test",
        "team_name": "Test Team",
        "jira_project_key": "TEST",
        "datadog_services": ["test-api"],
        "datadog_env": "prod",
        "max_tickets_per_run": None,
    }
    defaults.update(overrides)
    return TeamConfig(**defaults)


class TestTeamEnvOverride:
    """Core context-manager behaviour."""

    @patch("agent.utils.env_context.reload_config")
    def test_sets_env_vars_inside_context(self, mock_reload):
        team = _make_team(
            jira_project_key="VEGA",
            datadog_env="staging",
        )

        with team_env_override(team, "vega-api"):
            assert os.environ["JIRA_PROJECT_KEY"] == "VEGA"
            assert os.environ["DATADOG_SERVICE"] == "vega-api"
            assert os.environ["DATADOG_ENV"] == "staging"

    @patch("agent.utils.env_context.reload_config")
    def test_restores_original_values(self, mock_reload):
        os.environ["JIRA_PROJECT_KEY"] = "ORIGINAL"
        os.environ["DATADOG_SERVICE"] = "original-svc"
        os.environ["DATADOG_ENV"] = "dev"

        team = _make_team(jira_project_key="OVERRIDE", datadog_env="prod")

        with team_env_override(team, "override-svc"):
            assert os.environ["JIRA_PROJECT_KEY"] == "OVERRIDE"

        assert os.environ["JIRA_PROJECT_KEY"] == "ORIGINAL"
        assert os.environ["DATADOG_SERVICE"] == "original-svc"
        assert os.environ["DATADOG_ENV"] == "dev"

    @patch("agent.utils.env_context.reload_config")
    def test_restores_on_exception(self, mock_reload):
        os.environ["JIRA_PROJECT_KEY"] = "SAFE"

        team = _make_team(jira_project_key="BOOM")

        with pytest.raises(RuntimeError):
            with team_env_override(team, "svc"):
                assert os.environ["JIRA_PROJECT_KEY"] == "BOOM"
                raise RuntimeError("simulated failure")

        assert os.environ["JIRA_PROJECT_KEY"] == "SAFE"

    @patch("agent.utils.env_context.reload_config")
    def test_removes_vars_that_were_not_set_originally(self, mock_reload):
        # Ensure MAX_TICKETS_PER_RUN is not set
        os.environ.pop("MAX_TICKETS_PER_RUN", None)

        team = _make_team(max_tickets_per_run=10)

        with team_env_override(team, "svc"):
            assert os.environ["MAX_TICKETS_PER_RUN"] == "10"

        assert "MAX_TICKETS_PER_RUN" not in os.environ


class TestMaxTicketsOptional:
    """MAX_TICKETS_PER_RUN handling when team doesn't override it."""

    @patch("agent.utils.env_context.reload_config")
    def test_removes_max_tickets_when_team_has_none(self, mock_reload):
        os.environ["MAX_TICKETS_PER_RUN"] = "99"

        team = _make_team(max_tickets_per_run=None)

        with team_env_override(team, "svc"):
            # Should be removed so reload_config picks up the base default
            assert "MAX_TICKETS_PER_RUN" not in os.environ

        # Restored after context exit
        assert os.environ["MAX_TICKETS_PER_RUN"] == "99"

    @patch("agent.utils.env_context.reload_config")
    def test_sets_max_tickets_when_team_specifies_it(self, mock_reload):
        os.environ.pop("MAX_TICKETS_PER_RUN", None)

        team = _make_team(max_tickets_per_run=5)

        with team_env_override(team, "svc"):
            assert os.environ["MAX_TICKETS_PER_RUN"] == "5"

        assert "MAX_TICKETS_PER_RUN" not in os.environ


class TestLeakagePrevention:
    """Simulate sequential team runs and verify no leakage."""

    @patch("agent.utils.env_context.reload_config")
    def test_no_leakage_between_teams(self, mock_reload):
        """Team A sets MAX_TICKETS_PER_RUN=5; Team B does not.
        Without the context manager, team B would inherit 5.
        """
        os.environ.pop("MAX_TICKETS_PER_RUN", None)
        original_project = os.environ.get("JIRA_PROJECT_KEY")

        team_a = _make_team(
            team_id="team-a",
            jira_project_key="PROJ_A",
            datadog_env="prod",
            max_tickets_per_run=5,
        )
        team_b = _make_team(
            team_id="team-b",
            jira_project_key="PROJ_B",
            datadog_env="staging",
            max_tickets_per_run=None,
        )

        # Run team A
        with team_env_override(team_a, "svc-a"):
            assert os.environ["JIRA_PROJECT_KEY"] == "PROJ_A"
            assert os.environ["MAX_TICKETS_PER_RUN"] == "5"

        # Between teams: MAX_TICKETS_PER_RUN should not persist
        assert "MAX_TICKETS_PER_RUN" not in os.environ

        # Run team B
        with team_env_override(team_b, "svc-b"):
            assert os.environ["JIRA_PROJECT_KEY"] == "PROJ_B"
            assert os.environ["DATADOG_ENV"] == "staging"
            # Team B didn't set MAX_TICKETS_PER_RUN, so it should NOT be present
            assert "MAX_TICKETS_PER_RUN" not in os.environ

        # After both: nothing leaked
        assert "MAX_TICKETS_PER_RUN" not in os.environ


class TestReloadConfigCalls:
    """Verify reload_config() is called at the right times."""

    @patch("agent.utils.env_context.reload_config")
    def test_reload_called_on_enter_and_exit(self, mock_reload):
        team = _make_team()

        with team_env_override(team, "svc"):
            assert mock_reload.call_count == 1  # called on enter

        assert mock_reload.call_count == 2  # called again on exit

    @patch("agent.utils.env_context.reload_config")
    def test_reload_called_on_exit_after_exception(self, mock_reload):
        team = _make_team()

        with pytest.raises(ValueError):
            with team_env_override(team, "svc"):
                raise ValueError("boom")

        assert mock_reload.call_count == 2  # enter + exit (finally)
