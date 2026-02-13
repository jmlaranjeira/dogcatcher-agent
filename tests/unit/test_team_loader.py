"""Unit tests for team configuration loader."""

import pytest
from pathlib import Path
from unittest.mock import patch

from agent.team_loader import (
    load_teams_config,
    reset_cache,
    is_multi_tenant,
    get_team,
    list_team_ids,
)

SAMPLE_YAML = """\
jira_team_field_id: customfield_10100
teams:
  team-vega:
    team_name: Vega
    jira_project_key: VEGA
    jira_team_field_value: Vega Team
    datadog_services:
      - vega-api
      - vega-worker
    datadog_env: prod
  team-solar:
    team_name: Solar
    jira_project_key: SOL
    datadog_services:
      - solar-api
"""


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset module-level cache before each test."""
    reset_cache()
    yield
    reset_cache()


class TestLoadTeamsConfig:
    """Test YAML loading."""

    def test_load_valid_yaml(self, tmp_path):
        f = tmp_path / "teams.yaml"
        f.write_text(SAMPLE_YAML)
        cfg = load_teams_config(path=f)
        assert cfg is not None
        assert len(cfg.teams) == 2
        assert cfg.jira_team_field_id == "customfield_10100"

    def test_load_injects_team_id(self, tmp_path):
        f = tmp_path / "teams.yaml"
        f.write_text(SAMPLE_YAML)
        cfg = load_teams_config(path=f)
        assert cfg.get_team("team-vega").team_id == "team-vega"
        assert cfg.get_team("team-solar").team_id == "team-solar"

    def test_returns_none_when_missing(self, tmp_path):
        f = tmp_path / "nonexistent.yaml"
        cfg = load_teams_config(path=f)
        assert cfg is None

    def test_caches_after_first_load(self, tmp_path):
        f = tmp_path / "teams.yaml"
        f.write_text(SAMPLE_YAML)
        cfg1 = load_teams_config(path=f)
        cfg2 = load_teams_config(path=f)
        assert cfg1 is cfg2

    def test_invalid_yaml_raises(self, tmp_path):
        f = tmp_path / "teams.yaml"
        f.write_text("teams:\n  bad-team:\n    team_name: Bad\n")
        with pytest.raises(Exception):
            load_teams_config(path=f)


class TestIsMultiTenant:
    """Test multi-tenant detection."""

    def test_true_when_file_exists(self, tmp_path):
        f = tmp_path / "teams.yaml"
        f.write_text(SAMPLE_YAML)
        assert is_multi_tenant(path=f) is True

    def test_false_when_file_missing(self, tmp_path):
        assert is_multi_tenant(path=tmp_path / "nope.yaml") is False


class TestGetTeam:
    """Test get_team helper."""

    def test_returns_team(self, tmp_path):
        f = tmp_path / "teams.yaml"
        f.write_text(SAMPLE_YAML)
        # Pre-load into cache via explicit path
        load_teams_config(path=f)
        team = get_team("team-vega")
        assert team is not None
        assert team.jira_project_key == "VEGA"

    def test_returns_none_single_tenant(self, tmp_path):
        # No file loaded → single-tenant
        fake = tmp_path / "nonexistent" / "teams.yaml"
        with patch("agent.team_loader._TEAMS_FILE", fake):
            assert get_team("team-vega") is None


class TestListTeamIds:
    """Test list_team_ids helper."""

    def test_lists_sorted_ids(self, tmp_path):
        f = tmp_path / "teams.yaml"
        f.write_text(SAMPLE_YAML)
        load_teams_config(path=f)
        assert list_team_ids() == ["team-solar", "team-vega"]

    def test_empty_single_tenant(self, tmp_path):
        fake = tmp_path / "nonexistent" / "teams.yaml"
        with patch("agent.team_loader._TEAMS_FILE", fake):
            assert list_team_ids() == []
