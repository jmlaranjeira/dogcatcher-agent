"""Unit tests for tools.validate_teams."""

import json
from pathlib import Path

import pytest

from tools.validate_teams import validate_file, generate_schema


class TestValidateFile:
    def test_valid_example_file(self, tmp_path):
        content = """\
jira_team_field_id: "customfield_10100"
teams:
  team-vega:
    team_name: "Team Vega"
    jira_project_key: "VEGA"
    datadog_services:
      - vega-api
    datadog_env: prod
"""
        f = tmp_path / "teams.yaml"
        f.write_text(content)
        ok, msgs = validate_file(f)
        assert ok, msgs
        assert "1 team(s)" in msgs[0]

    def test_multiple_teams(self, tmp_path):
        content = """\
teams:
  team-alpha:
    team_name: "Alpha"
    jira_project_key: "ALPHA"
    datadog_services: [alpha-api]
  team-beta:
    team_name: "Beta"
    jira_project_key: "BETA"
    datadog_services: [beta-api]
"""
        f = tmp_path / "teams.yaml"
        f.write_text(content)
        ok, msgs = validate_file(f)
        assert ok, msgs
        assert "2 team(s)" in msgs[0]

    def test_missing_file(self, tmp_path):
        ok, msgs = validate_file(tmp_path / "nonexistent.yaml")
        assert not ok
        assert "not found" in msgs[0].lower()

    def test_invalid_yaml(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("teams:\n  - [invalid: yaml: :\n")
        ok, msgs = validate_file(f)
        assert not ok

    def test_invalid_team_id(self, tmp_path):
        content = """\
teams:
  "team@bad":
    team_name: "Bad"
    jira_project_key: "BAD"
    datadog_services: [svc]
"""
        f = tmp_path / "teams.yaml"
        f.write_text(content)
        ok, msgs = validate_file(f)
        assert not ok

    def test_empty_services_rejected(self, tmp_path):
        content = """\
teams:
  team-x:
    team_name: "X"
    jira_project_key: "X"
    datadog_services: []
"""
        f = tmp_path / "teams.yaml"
        f.write_text(content)
        ok, msgs = validate_file(f)
        assert not ok

    def test_duplicate_jira_project_key(self, tmp_path):
        content = """\
teams:
  team-a:
    team_name: "A"
    jira_project_key: "SAME"
    datadog_services: [a-api]
  team-b:
    team_name: "B"
    jira_project_key: "SAME"
    datadog_services: [b-api]
"""
        f = tmp_path / "teams.yaml"
        f.write_text(content)
        ok, msgs = validate_file(f)
        assert not ok
        assert any("duplicate" in m.lower() for m in msgs)

    def test_empty_teams(self, tmp_path):
        content = "teams: {}\n"
        f = tmp_path / "teams.yaml"
        f.write_text(content)
        ok, msgs = validate_file(f)
        assert ok
        assert "0 team(s)" in msgs[0]


class TestGenerateSchema:
    def test_schema_is_valid_json_schema(self):
        schema = generate_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "teams" in schema["properties"]

    def test_schema_contains_team_fields(self):
        schema = generate_schema()
        schema_str = json.dumps(schema)
        assert "team_id" in schema_str
        assert "jira_project_key" in schema_str
        assert "datadog_services" in schema_str

    def test_schema_is_valid_json(self):
        """Ensure the schema round-trips through JSON correctly."""
        schema = generate_schema()
        serialized = json.dumps(schema, indent=2)
        deserialized = json.loads(serialized)
        assert deserialized == schema
