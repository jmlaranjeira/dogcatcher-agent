"""Unit tests for agent.metrics module."""

import pytest
from unittest.mock import MagicMock, patch

import agent.metrics as metrics_mod
from agent.metrics import _NoOpStatsd, _tags, incr, gauge, timing


class TestNoOpStatsd:
    """Verify _NoOpStatsd is a valid drop-in."""

    def test_all_methods_are_noop(self):
        client = _NoOpStatsd()
        # Should not raise
        client.increment("x", value=1)
        client.gauge("x", value=1.0)
        client.histogram("x", value=1.0)
        client.timing("x", value=1.0)
        client.close()


class TestTags:
    def test_empty_returns_empty_list(self):
        assert _tags(None) == []

    def test_with_values(self):
        tags = _tags({"team_id": "vega", "env": "prod"})
        assert "team_id:vega" in tags
        assert "env:prod" in tags

    def test_none_values_filtered(self):
        tags = _tags({"team_id": None, "env": "prod"})
        assert len(tags) == 1
        assert "env:prod" in tags

    def test_all_none_returns_empty(self):
        tags = _tags({"team_id": None, "env": None})
        assert tags == []

    def test_empty_dict_returns_empty(self):
        assert _tags({}) == []


class TestMetricsDisabled:
    """When DATADOG_METRICS_ENABLED=false (default), calls are no-ops."""

    def test_incr_noop_when_disabled(self):
        """incr should not raise even when metrics are disabled."""
        # Force re-init with a NoOp client
        old_client = metrics_mod._client
        metrics_mod._client = _NoOpStatsd()
        try:
            incr("test.metric")  # should not raise
            incr("test.metric", value=5, team_id="team-x")
        finally:
            metrics_mod._client = old_client

    def test_gauge_noop_when_disabled(self):
        old_client = metrics_mod._client
        metrics_mod._client = _NoOpStatsd()
        try:
            gauge("test.gauge", 42.0)
        finally:
            metrics_mod._client = old_client

    def test_timing_noop_when_disabled(self):
        old_client = metrics_mod._client
        metrics_mod._client = _NoOpStatsd()
        try:
            timing("test.timing", 150.0)
        finally:
            metrics_mod._client = old_client


class TestMetricsDelegation:
    """When a client is available, calls delegate correctly."""

    def test_incr_delegates_to_client(self):
        mock_statsd = MagicMock()
        old_client = metrics_mod._client
        metrics_mod._client = mock_statsd
        try:
            incr("tickets.created", team_id="team-vega")
            mock_statsd.increment.assert_called_once()
            call_args = mock_statsd.increment.call_args
            assert call_args[0][0] == "tickets.created"
            assert call_args[1]["value"] == 1
        finally:
            metrics_mod._client = old_client

    def test_gauge_delegates_to_client(self):
        mock_statsd = MagicMock()
        old_client = metrics_mod._client
        metrics_mod._client = mock_statsd
        try:
            gauge("run.duration", 12.5, team_id="team-vega")
            mock_statsd.gauge.assert_called_once()
            call_args = mock_statsd.gauge.call_args
            assert call_args[0][0] == "run.duration"
            assert call_args[1]["value"] == 12.5
        finally:
            metrics_mod._client = old_client

    def test_timing_delegates_to_client(self):
        mock_statsd = MagicMock()
        old_client = metrics_mod._client
        metrics_mod._client = mock_statsd
        try:
            timing("api.duration", 150.0)
            mock_statsd.timing.assert_called_once()
            call_args = mock_statsd.timing.call_args
            assert call_args[0][0] == "api.duration"
            assert call_args[1]["value"] == 150.0
        finally:
            metrics_mod._client = old_client

    def test_incr_with_extra_tags(self):
        mock_statsd = MagicMock()
        old_client = metrics_mod._client
        metrics_mod._client = mock_statsd
        try:
            incr("tickets.created", value=2, team_id="vega")
            call_args = mock_statsd.increment.call_args
            assert call_args[1]["value"] == 2
            tags = call_args[1]["tags"]
            assert "team_id:vega" in tags
        finally:
            metrics_mod._client = old_client

    def test_incr_without_team_id(self):
        mock_statsd = MagicMock()
        old_client = metrics_mod._client
        metrics_mod._client = mock_statsd
        try:
            incr("logs.fetched", value=50)
            call_args = mock_statsd.increment.call_args
            assert call_args[1]["tags"] is None
        finally:
            metrics_mod._client = old_client
