"""Unit tests for TeamConfig and TeamsConfig Pydantic models."""

import pytest
from pydantic import ValidationError

from agent.team_config import TeamConfig, TeamsConfig


class TestTeamConfig:
    """Test TeamConfig model validation."""

    def test_valid_team_config(self):
        tc = TeamConfig(
            team_id="team-vega",
            team_name="Vega",
            jira_project_key="VEGA",
            datadog_services=["vega-api"],
        )
        assert tc.team_id == "team-vega"
        assert tc.datadog_env == "prod"  # default

    def test_all_fields(self):
        tc = TeamConfig(
            team_id="team_solar",
            team_name="Solar",
            jira_project_key="SOL",
            jira_team_field_value="Solar Team",
            datadog_services=["solar-api", "solar-worker"],
            datadog_env="staging",
            max_tickets_per_run=5,
            severity_rules={"npe": "high"},
        )
        assert tc.jira_team_field_value == "Solar Team"
        assert len(tc.datadog_services) == 2
        assert tc.severity_rules == {"npe": "high"}

    def test_invalid_team_id_special_chars(self):
        with pytest.raises(ValidationError, match="team_id"):
            TeamConfig(
                team_id="team@invalid!",
                team_name="Bad",
                jira_project_key="BAD",
                datadog_services=["svc"],
            )

    def test_invalid_team_id_empty(self):
        with pytest.raises(ValidationError, match="team_id"):
            TeamConfig(
                team_id="",
                team_name="Empty",
                jira_project_key="EMP",
                datadog_services=["svc"],
            )

    def test_empty_services_rejected(self):
        with pytest.raises(ValidationError, match="datadog_services"):
            TeamConfig(
                team_id="team-x",
                team_name="X",
                jira_project_key="X",
                datadog_services=[],
            )

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            TeamConfig(team_id="team-x")


class TestTeamsConfig:
    """Test TeamsConfig container model."""

    def _make_teams_config(self):
        teams = {
            "team-vega": TeamConfig(
                team_id="team-vega",
                team_name="Vega",
                jira_project_key="VEGA",
                datadog_services=["vega-api"],
            ),
            "team-solar": TeamConfig(
                team_id="team-solar",
                team_name="Solar",
                jira_project_key="SOL",
                datadog_services=["solar-api"],
            ),
        }
        return TeamsConfig(jira_team_field_id="customfield_10100", teams=teams)

    def test_get_team_found(self):
        cfg = self._make_teams_config()
        team = cfg.get_team("team-vega")
        assert team is not None
        assert team.jira_project_key == "VEGA"

    def test_get_team_not_found(self):
        cfg = self._make_teams_config()
        assert cfg.get_team("nonexistent") is None

    def test_list_team_ids(self):
        cfg = self._make_teams_config()
        ids = cfg.list_team_ids()
        assert ids == ["team-solar", "team-vega"]  # sorted

    def test_empty_teams(self):
        cfg = TeamsConfig(teams={})
        assert cfg.list_team_ids() == []
        assert cfg.get_team("any") is None
