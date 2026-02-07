"""Unit tests for the Datadog client module."""

import pytest
import json
from unittest.mock import patch, Mock
from types import SimpleNamespace
from datetime import datetime, timedelta

from agent.datadog import (
    _coerce_detail,
    _build_dd_query,
    _missing_dd_config,
    get_logs,
    MAX_LOG_DETAIL_LENGTH,
)


class TestCoerceDetail:
    """Test _coerce_detail function."""

    def test_coerce_dict_to_json(self):
        """Test converting dict to JSON string."""
        value = {"key": "value", "number": 123}
        result = _coerce_detail(value)
        assert result == '{"key": "value", "number": 123}'

    def test_coerce_list_to_json(self):
        """Test converting list to JSON string."""
        value = ["item1", "item2", 123]
        result = _coerce_detail(value)
        assert result == '["item1", "item2", 123]'

    def test_coerce_none_to_fallback(self):
        """Test converting None to fallback string."""
        result = _coerce_detail(None)
        assert result == "no detailed log"

    def test_coerce_none_with_custom_fallback(self):
        """Test converting None with custom fallback."""
        result = _coerce_detail(None, fallback="custom fallback")
        assert result == "custom fallback"

    def test_coerce_string_unchanged(self):
        """Test that strings pass through unchanged."""
        value = "test string"
        result = _coerce_detail(value)
        assert result == "test string"

    def test_coerce_number_to_string(self):
        """Test converting number to string."""
        result = _coerce_detail(42)
        assert result == "42"

    def test_coerce_dict_with_utf8(self):
        """Test dict with UTF-8 characters (ensure_ascii=False)."""
        value = {"message": "Error: café ☕"}
        result = _coerce_detail(value)
        assert "café" in result
        assert "☕" in result
        # Should not be ASCII-escaped
        assert "\\u" not in result

    def test_coerce_dict_json_error(self):
        """Test dict that cannot be JSON-encoded falls back to str()."""
        # Create a mock object that raises an exception during JSON encoding
        class UnserializableDict(dict):
            def __iter__(self):
                raise TypeError("Cannot serialize")

        value = UnserializableDict({"key": "value"})
        result = _coerce_detail(value)
        # Should fall back to str() representation
        assert isinstance(result, str)


class TestBuildDdQuery:
    """Test _build_dd_query function."""

    def test_single_status(self):
        """Test query with single status."""
        query, extra = _build_dd_query(
            service="myservice",
            env="prod",
            statuses_csv="error",
            extra_csv="",
            extra_mode="AND",
        )
        assert query == "service:myservice env:prod (status:error)"
        assert extra == ""

    def test_multiple_statuses(self):
        """Test query with multiple statuses."""
        query, extra = _build_dd_query(
            service="myservice",
            env="prod",
            statuses_csv="error,critical,warning",
            extra_csv="",
            extra_mode="AND",
        )
        assert query == "service:myservice env:prod (status:error OR status:critical OR status:warning)"
        assert extra == ""

    def test_empty_statuses_defaults_to_error(self):
        """Test that empty statuses defaults to 'status:error'."""
        query, extra = _build_dd_query(
            service="myservice", env="prod", statuses_csv="", extra_csv="", extra_mode="AND"
        )
        assert query == "service:myservice env:prod status:error"
        assert extra == ""

    def test_extra_clause_with_and_mode(self):
        """Test extra clause with AND mode."""
        query, extra = _build_dd_query(
            service="myservice",
            env="prod",
            statuses_csv="error",
            extra_csv="term1,term2",
            extra_mode="AND",
        )
        assert query == "service:myservice env:prod (status:error) (term1 AND term2)"
        assert extra == " (term1 AND term2)"

    def test_extra_clause_with_or_mode(self):
        """Test extra clause with OR mode."""
        query, extra = _build_dd_query(
            service="myservice",
            env="prod",
            statuses_csv="error",
            extra_csv="term1,term2,term3",
            extra_mode="OR",
        )
        assert query == "service:myservice env:prod (status:error) (term1 OR term2 OR term3)"
        assert extra == " (term1 OR term2 OR term3)"

    def test_extra_clause_single_term(self):
        """Test extra clause with single term."""
        query, extra = _build_dd_query(
            service="myservice",
            env="prod",
            statuses_csv="error",
            extra_csv="NullPointerException",
            extra_mode="AND",
        )
        assert query == "service:myservice env:prod (status:error) (NullPointerException)"
        assert extra == " (NullPointerException)"

    def test_whitespace_handling(self):
        """Test that whitespace is properly handled."""
        query, extra = _build_dd_query(
            service="myservice",
            env="prod",
            statuses_csv=" error , warning ",
            extra_csv=" term1 , term2 ",
            extra_mode="AND",
        )
        assert "status:error" in query
        assert "status:warning" in query
        assert "term1 AND term2" in query


class TestMissingDdConfig:
    """Test _missing_dd_config function."""

    def test_all_config_present(self):
        """Test when all required config is present."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
        )
        with patch("agent.datadog.get_config", return_value=mock_config):
            missing = _missing_dd_config()
            assert missing == []

    def test_missing_api_key(self):
        """Test when API key is missing."""
        mock_config = SimpleNamespace(
            datadog_api_key="",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
        )
        with patch("agent.datadog.get_config", return_value=mock_config):
            missing = _missing_dd_config()
            assert "DATADOG_API_KEY" in missing

    def test_missing_app_key(self):
        """Test when app key is missing."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="",
            datadog_site="datadoghq.eu",
        )
        with patch("agent.datadog.get_config", return_value=mock_config):
            missing = _missing_dd_config()
            assert "DATADOG_APP_KEY" in missing

    def test_missing_site(self):
        """Test when site is missing."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="",
        )
        with patch("agent.datadog.get_config", return_value=mock_config):
            missing = _missing_dd_config()
            assert "DATADOG_SITE" in missing

    def test_all_config_missing(self):
        """Test when all config is missing."""
        mock_config = SimpleNamespace(
            datadog_api_key="",
            datadog_app_key="",
            datadog_site="",
        )
        with patch("agent.datadog.get_config", return_value=mock_config):
            missing = _missing_dd_config()
            assert len(missing) == 3
            assert "DATADOG_API_KEY" in missing
            assert "DATADOG_APP_KEY" in missing
            assert "DATADOG_SITE" in missing


class TestGetLogs:
    """Test get_logs function."""

    def test_returns_empty_list_when_config_missing(self):
        """Test that get_logs returns empty list when config is missing."""
        mock_config = SimpleNamespace(
            datadog_api_key="",  # Missing
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.0)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.get_performance_metrics", return_value=mock_metrics
        ):
            result = get_logs()
            assert result == []

    def test_single_page_of_results(self):
        """Test fetching a single page of results."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        # Mock Datadog API response
        mock_response = {
            "data": [
                {
                    "attributes": {
                        "message": "NullPointerException in UserService",
                        "timestamp": "2025-01-01T12:00:00Z",
                        "attributes": {
                            "logger": {"name": "com.example.UserService", "thread_name": "worker-1"},
                            "properties": {"Log": "Detailed error log information"},
                        },
                    }
                }
            ],
            "meta": {"page": {}},  # No cursor means no more pages
        }

        mock_http_response = Mock()
        mock_http_response.json.return_value = mock_response
        mock_http_response.raise_for_status = Mock()

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", return_value=mock_http_response
        ), patch("agent.datadog.get_performance_metrics", return_value=mock_metrics):
            result = get_logs()

            assert len(result) == 1
            assert result[0]["logger"] == "com.example.UserService"
            assert result[0]["thread"] == "worker-1"
            assert result[0]["message"] == "NullPointerException in UserService"
            assert result[0]["timestamp"] == "2025-01-01T12:00:00Z"
            assert result[0]["detail"] == "Detailed error log information"

    def test_pagination_with_cursor(self):
        """Test pagination with cursor."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        # First page response
        first_page = {
            "data": [
                {
                    "attributes": {
                        "message": "Error 1",
                        "timestamp": "2025-01-01T12:00:00Z",
                        "attributes": {
                            "logger": {"name": "com.example.Service", "thread_name": "thread-1"},
                            "properties": {"Log": "Detail 1"},
                        },
                    }
                }
            ],
            "meta": {"page": {"after": "cursor123"}},
        }

        # Second page response
        second_page = {
            "data": [
                {
                    "attributes": {
                        "message": "Error 2",
                        "timestamp": "2025-01-01T12:01:00Z",
                        "attributes": {
                            "logger": {"name": "com.example.Service", "thread_name": "thread-2"},
                            "properties": {"Log": "Detail 2"},
                        },
                    }
                }
            ],
            "meta": {"page": {}},  # No cursor
        }

        mock_responses = [first_page, second_page]
        response_index = [0]

        def mock_post(*args, **kwargs):
            mock_resp = Mock()
            mock_resp.json.return_value = mock_responses[response_index[0]]
            mock_resp.raise_for_status = Mock()
            response_index[0] += 1
            return mock_resp

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", side_effect=mock_post
        ), patch("agent.datadog.get_performance_metrics", return_value=mock_metrics):
            result = get_logs()

            assert len(result) == 2
            assert result[0]["message"] == "Error 1"
            assert result[1]["message"] == "Error 2"

    def test_stops_at_max_pages_limit(self):
        """Test that pagination stops at max_pages limit."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=2,  # Limit to 2 pages
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        # Always return a page with a cursor
        mock_response = {
            "data": [
                {
                    "attributes": {
                        "message": "Error",
                        "timestamp": "2025-01-01T12:00:00Z",
                        "attributes": {
                            "logger": {"name": "com.example.Service", "thread_name": "thread-1"},
                            "properties": {"Log": "Detail"},
                        },
                    }
                }
            ],
            "meta": {"page": {"after": "next_cursor"}},  # Always has next page
        }

        mock_http_response = Mock()
        mock_http_response.json.return_value = mock_response
        mock_http_response.raise_for_status = Mock()

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", return_value=mock_http_response
        ) as mock_post, patch("agent.datadog.get_performance_metrics", return_value=mock_metrics):
            result = get_logs()

            # Should only fetch 2 pages
            assert mock_post.call_count == 2
            assert len(result) == 2

    def test_truncates_long_detail_fields(self):
        """Test that long detail fields are truncated."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        # Create a very long detail string
        long_detail = "x" * (MAX_LOG_DETAIL_LENGTH + 100)

        mock_response = {
            "data": [
                {
                    "attributes": {
                        "message": "Error",
                        "timestamp": "2025-01-01T12:00:00Z",
                        "attributes": {
                            "logger": {"name": "com.example.Service", "thread_name": "thread-1"},
                            "properties": {"Log": long_detail},
                        },
                    }
                }
            ],
            "meta": {"page": {}},
        }

        mock_http_response = Mock()
        mock_http_response.json.return_value = mock_response
        mock_http_response.raise_for_status = Mock()

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", return_value=mock_http_response
        ), patch("agent.datadog.get_performance_metrics", return_value=mock_metrics):
            result = get_logs()

            assert len(result) == 1
            assert len(result[0]["detail"]) == MAX_LOG_DETAIL_LENGTH + len("... [truncated]")
            assert result[0]["detail"].endswith("... [truncated]")

    def test_http_error_returns_empty_list(self):
        """Test that HTTP errors return empty list."""
        import requests

        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", side_effect=requests.RequestException("Connection error")
        ), patch("agent.datadog.get_performance_metrics", return_value=mock_metrics):
            result = get_logs()

            assert result == []

    def test_fallback_query_when_no_results_with_extra_clause(self):
        """Test fallback query when no results are found with extra_clause."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="NullPointerException",  # Has extra clause
            datadog_query_extra_mode="AND",
        )

        # First call (with extra) returns empty
        empty_response = {"data": [], "meta": {"page": {}}}

        # Fallback call (without extra) returns data
        fallback_response = {
            "data": [
                {
                    "attributes": {
                        "message": "Some other error",
                        "timestamp": "2025-01-01T12:00:00Z",
                        "attributes": {
                            "logger": {"name": "com.example.Service", "thread_name": "thread-1"},
                            "properties": {"Log": "Different error"},
                        },
                    }
                }
            ],
            "meta": {"page": {}},
        }

        mock_responses = [empty_response, fallback_response]
        response_index = [0]

        def mock_post(*args, **kwargs):
            mock_resp = Mock()
            mock_resp.json.return_value = mock_responses[response_index[0]]
            mock_resp.raise_for_status = Mock()
            response_index[0] += 1
            return mock_resp

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", side_effect=mock_post
        ) as mock_post_call, patch(
            "agent.datadog.get_performance_metrics", return_value=mock_metrics
        ):
            result = get_logs()

            # Should have made 2 calls: one with extra clause, one without
            assert mock_post_call.call_count == 2

            # Verify the second call has query without extra clause
            second_call_payload = mock_post_call.call_args_list[1][1]["json"]
            query = second_call_payload["filter"]["query"]
            # Should be service:myservice env:prod without extra terms
            assert query == "service:myservice env:prod"

            # Results should be empty (fallback is just diagnostic)
            assert result == []

    def test_handles_missing_nested_attributes(self):
        """Test handling of missing nested attributes in API response."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="myservice",
            datadog_env="prod",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        # Response with missing nested attributes
        mock_response = {
            "data": [
                {
                    "attributes": {
                        "message": "Error message",
                        "timestamp": "2025-01-01T12:00:00Z",
                        # Missing attributes.logger, attributes.properties
                    }
                }
            ],
            "meta": {"page": {}},
        }

        mock_http_response = Mock()
        mock_http_response.json.return_value = mock_response
        mock_http_response.raise_for_status = Mock()

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", return_value=mock_http_response
        ), patch("agent.datadog.get_performance_metrics", return_value=mock_metrics):
            result = get_logs()

            assert len(result) == 1
            # Should use default values
            assert result[0]["logger"] == "unknown.logger"
            assert result[0]["thread"] == "unknown.thread"
            assert result[0]["message"] == "Error message"
            assert result[0]["detail"] == "no detailed log"

    def test_uses_cli_parameters_over_config(self):
        """Test that CLI parameters override config defaults."""
        mock_config = SimpleNamespace(
            datadog_api_key="test-api-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_service="default-service",
            datadog_env="default-env",
            datadog_hours_back=24,
            datadog_limit=50,
            datadog_max_pages=3,
            datadog_timeout=20,
            datadog_statuses="error",
            datadog_query_extra="",
            datadog_query_extra_mode="AND",
        )

        mock_response = {"data": [], "meta": {"page": {}}}

        mock_http_response = Mock()
        mock_http_response.json.return_value = mock_response
        mock_http_response.raise_for_status = Mock()

        mock_metrics = Mock()
        mock_metrics.start_timer = Mock()
        mock_metrics.end_timer = Mock(return_value=0.123)

        with patch("agent.datadog.get_config", return_value=mock_config), patch(
            "agent.datadog.requests.post", return_value=mock_http_response
        ) as mock_post, patch("agent.datadog.get_performance_metrics", return_value=mock_metrics):
            # Call with override parameters
            get_logs(service="override-service", env="override-env", hours_back=48, limit=100)

            # Verify the request used the override values
            call_payload = mock_post.call_args[1]["json"]
            query = call_payload["filter"]["query"]
            assert "service:override-service" in query
            assert "env:override-env" in query
            assert call_payload["page"]["limit"] == 100
