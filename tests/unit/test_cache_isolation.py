"""Unit tests for per-team cache path isolation."""

import json
import pytest
from pathlib import Path

from agent.jira.utils import (
    _get_cache_dir,
    _cache_path,
    _comment_cache_path,
    load_processed_fingerprints,
    save_processed_fingerprints,
    _load_comment_cache,
    _save_comment_cache,
)


class TestCachePathIsolation:
    """Verify that cache paths are scoped by team_id."""

    def test_single_tenant_cache_dir(self):
        d = _get_cache_dir(None)
        assert d == Path(".agent_cache")

    def test_multi_tenant_cache_dir(self):
        d = _get_cache_dir("team-vega")
        assert d == Path(".agent_cache/teams/team-vega")

    def test_fingerprint_path_single(self):
        p = _cache_path(None)
        assert p == Path(".agent_cache/processed_logs.json")

    def test_fingerprint_path_multi(self):
        p = _cache_path("team-solar")
        assert p == Path(".agent_cache/teams/team-solar/processed_logs.json")

    def test_comment_cache_path_single(self):
        p = _comment_cache_path(None)
        assert p == Path(".agent_cache/jira_comments.json")

    def test_comment_cache_path_multi(self):
        p = _comment_cache_path("team-vega")
        assert p == Path(".agent_cache/teams/team-vega/jira_comments.json")


class TestFingerprintCacheIsolation:
    """Verify fingerprints are isolated per team."""

    def test_save_and_load_per_team(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.jira.utils._CACHE_DIR", tmp_path)

        save_processed_fingerprints(["fp1", "fp2"], team_id="team-a")
        save_processed_fingerprints(["fp3"], team_id="team-b")
        save_processed_fingerprints(["fp0"], team_id=None)

        assert load_processed_fingerprints("team-a") == {"fp1", "fp2"}
        assert load_processed_fingerprints("team-b") == {"fp3"}
        assert load_processed_fingerprints(None) == {"fp0"}

    def test_teams_dont_leak(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.jira.utils._CACHE_DIR", tmp_path)

        save_processed_fingerprints(["x"], team_id="team-a")
        # team-b has no cache yet
        assert load_processed_fingerprints("team-b") == set()


class TestCommentCacheIsolation:
    """Verify comment cooldown cache is isolated per team."""

    def test_save_and_load_per_team(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent.jira.utils._CACHE_DIR", tmp_path)

        _save_comment_cache({"VEGA-1": "2025-01-01T00:00:00Z"}, team_id="team-vega")
        _save_comment_cache({"SOL-1": "2025-01-01T00:00:00Z"}, team_id="team-solar")

        vega = _load_comment_cache("team-vega")
        solar = _load_comment_cache("team-solar")
        assert "VEGA-1" in vega
        assert "VEGA-1" not in solar
        assert "SOL-1" in solar
