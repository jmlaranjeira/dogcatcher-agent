"""Unit tests for the Sleuth agent.

Tests cover query parsing, query building, log correlation,
and the overall investigation workflow.
"""

import pytest
from unittest.mock import patch, MagicMock
import json


class TestQueryBuilder:
    """Tests for the query builder utilities."""

    def test_extract_entities_emails(self):
        """Test email extraction from query."""
        from sleuth.utils.query_builder import extract_entities

        text = "errors for user john.doe@example.com in the system"
        entities = extract_entities(text)

        assert "john.doe@example.com" in entities["emails"]

    def test_extract_entities_uuids(self):
        """Test UUID extraction from query."""
        from sleuth.utils.query_builder import extract_entities

        text = "error with session 550e8400-e29b-41d4-a716-446655440000"
        entities = extract_entities(text)

        assert "550e8400-e29b-41d4-a716-446655440000" in entities["uuids"]

    def test_extract_entities_services(self):
        """Test service name extraction from query."""
        from sleuth.utils.query_builder import extract_entities

        text = "timeout in user-service and api-gateway"
        entities = extract_entities(text)

        assert "user-service" in entities["services"]
        assert "api-gateway" in entities["services"]

    def test_extract_entities_keywords(self):
        """Test keyword extraction from query."""
        from sleuth.utils.query_builder import extract_entities

        text = "authentication failure during registration process"
        entities = extract_entities(text)

        assert "authentication" in entities["keywords"]
        assert "failure" in entities["keywords"]
        assert "registration" in entities["keywords"]
        assert "process" in entities["keywords"]

    def test_extract_entities_filters_stopwords(self):
        """Test that stopwords are filtered from keywords."""
        from sleuth.utils.query_builder import extract_entities

        text = "find errors that have been happening with this service"
        entities = extract_entities(text)

        assert "that" not in entities["keywords"]
        assert "have" not in entities["keywords"]
        assert "been" not in entities["keywords"]
        assert "this" not in entities["keywords"]
        assert "errors" not in entities["keywords"]  # "errors" is filtered
        assert "find" not in entities["keywords"]  # "find" is filtered

    def test_build_query_rules_with_service(self):
        """Test rule-based query building with explicit service."""
        from sleuth.utils.query_builder import _build_query_rules

        query = _build_query_rules(
            user_query="user registration failed", service="user-api", env="prod"
        )

        assert "service:user-api" in query
        assert "env:prod" in query
        assert "status:error" in query

    def test_build_query_rules_infers_service(self):
        """Test rule-based query building infers service from query."""
        from sleuth.utils.query_builder import _build_query_rules

        query = _build_query_rules(
            user_query="timeout in payment-service", service=None, env="prod"
        )

        assert "service:payment-service" in query

    def test_get_template_query_timeout(self):
        """Test predefined template for timeout errors."""
        from sleuth.utils.query_builder import get_template_query

        query = get_template_query("timeout", service="api", env="prod")

        assert query is not None
        assert "service:api" in query
        assert "env:prod" in query
        assert "timeout" in query.lower()

    def test_get_template_query_unknown(self):
        """Test template query returns None for unknown pattern."""
        from sleuth.utils.query_builder import get_template_query

        query = get_template_query("unknown_pattern")

        assert query is None


class TestSleuthNodes:
    """Tests for Sleuth node implementations."""

    def test_parse_query_extracts_service(self):
        """Test parse_query infers service from query text."""
        from sleuth.sleuth_nodes import parse_query

        state = {"query": "errors in user-service", "service": None}
        result = parse_query(state)

        assert result.get("service") == "user-service"

    def test_parse_query_preserves_explicit_service(self):
        """Test parse_query preserves explicitly provided service."""
        from sleuth.sleuth_nodes import parse_query

        state = {"query": "errors in user-service", "service": "other-service"}
        result = parse_query(state)

        assert result.get("service") == "other-service"

    @patch("sleuth.sleuth_nodes.get_config")
    def test_build_dd_query_uses_config_env(self, mock_config):
        """Test build_dd_query uses config environment when not specified."""
        from sleuth.sleuth_nodes import build_dd_query

        mock_config.return_value = MagicMock(
            datadog_env="staging", openai_api_key=None  # Disable LLM
        )

        state = {"query": "test errors", "service": "test-svc"}
        result = build_dd_query(state)

        assert "dd_query" in result
        assert "staging" in result["dd_query"]

    @patch("sleuth.sleuth_nodes.requests.post")
    @patch("sleuth.sleuth_nodes.get_config")
    def test_search_logs_success(self, mock_config, mock_post):
        """Test successful log search from Datadog."""
        from sleuth.sleuth_nodes import search_logs

        mock_config.return_value = MagicMock(
            datadog_api_key="test-key",
            datadog_app_key="test-app-key",
            datadog_site="datadoghq.eu",
            datadog_limit=50,
            datadog_timeout=20,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "attributes": {
                        "message": "Test error message",
                        "timestamp": "2024-01-01T00:00:00Z",
                        "status": "error",
                        "service": "test-svc",
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        state = {"dd_query": "service:test status:error", "hours_back": 24}
        result = search_logs(state)

        assert len(result["logs"]) == 1
        assert result["logs"][0]["message"] == "Test error message"
        assert result.get("error") is None

    @patch("sleuth.sleuth_nodes.requests.post")
    @patch("sleuth.sleuth_nodes.get_config")
    def test_search_logs_missing_credentials(self, mock_config, mock_post):
        """Test log search fails gracefully with missing credentials."""
        from sleuth.sleuth_nodes import search_logs

        mock_config.return_value = MagicMock(datadog_api_key="", datadog_app_key="")

        state = {"dd_query": "service:test status:error", "hours_back": 24}
        result = search_logs(state)

        assert result["logs"] == []
        assert "Missing Datadog" in result.get("error", "")

    @patch("sleuth.sleuth_nodes.jira_is_configured")
    def test_correlate_jira_not_configured(self, mock_jira_configured):
        """Test Jira correlation skipped when not configured."""
        from sleuth.sleuth_nodes import correlate_jira

        mock_jira_configured.return_value = False

        state = {"logs": [{"message": "test error"}]}
        result = correlate_jira(state)

        assert result["related_tickets"] == []

    @patch("sleuth.sleuth_nodes.jira_is_configured")
    def test_correlate_jira_no_logs(self, mock_jira_configured):
        """Test Jira correlation with no logs returns empty tickets."""
        from sleuth.sleuth_nodes import correlate_jira

        mock_jira_configured.return_value = True

        state = {"logs": []}
        result = correlate_jira(state)

        assert result["related_tickets"] == []

    def test_basic_analysis_no_logs(self):
        """Test basic analysis with no logs."""
        from sleuth.sleuth_nodes import _basic_analysis

        state = {"logs": [], "related_tickets": []}
        result = _basic_analysis(state)

        assert "0 error logs" in result["summary"]
        assert result["can_auto_fix"] is False

    def test_basic_analysis_with_logs(self):
        """Test basic analysis identifies patterns in logs."""
        from sleuth.sleuth_nodes import _basic_analysis

        state = {
            "logs": [
                {"message": "NullPointerException in UserService"},
                {"message": "NullPointerException in UserService"},
                {"message": "NullPointerException in UserService"},
            ],
            "related_tickets": [],
        }
        result = _basic_analysis(state)

        assert "3 error logs" in result["summary"]
        assert "3 occurrences" in result["summary"]

    def test_suggest_action_can_fix(self):
        """Test suggest_action when auto-fix is possible."""
        from sleuth.sleuth_nodes import suggest_action

        state = {"can_auto_fix": True, "no_patchy": False}
        result = suggest_action(state)

        assert result["patchy_invoked"] is False
        assert result["can_auto_fix"] is True

    def test_suggest_action_no_patchy_flag(self):
        """Test suggest_action respects no_patchy flag."""
        from sleuth.sleuth_nodes import suggest_action

        state = {"can_auto_fix": True, "no_patchy": True}
        result = suggest_action(state)

        assert result["can_auto_fix"] is False


class TestSleuthGraph:
    """Tests for the Sleuth graph definition."""

    def test_build_graph_compiles(self):
        """Test that the graph compiles without errors."""
        from sleuth.sleuth_graph import build_graph

        graph = build_graph()
        assert graph is not None

    def test_format_output_basic(self):
        """Test output formatting with minimal result."""
        from sleuth.sleuth_graph import _format_output

        result = {
            "query": "test query",
            "dd_query": "service:test status:error",
            "logs": [],
            "summary": "No logs found.",
            "related_tickets": [],
        }

        output = _format_output(result)

        assert "test query" in output
        assert "service:test" in output
        assert "Logs found: 0" in output

    def test_format_output_with_logs(self):
        """Test output formatting with logs."""
        from sleuth.sleuth_graph import _format_output

        result = {
            "query": "test query",
            "dd_query": "service:test status:error",
            "logs": [
                {"message": "Error 1", "status": "error"},
                {"message": "Error 2", "status": "error"},
            ],
            "summary": "Found 2 errors.",
            "related_tickets": [],
        }

        output = _format_output(result)

        assert "Logs found: 2" in output
        assert "Sample logs" in output
        assert "Error 1" in output

    def test_format_output_with_tickets(self):
        """Test output formatting with related tickets."""
        from sleuth.sleuth_graph import _format_output

        result = {
            "query": "test query",
            "dd_query": "service:test status:error",
            "logs": [],
            "summary": "No logs.",
            "related_tickets": [
                {"key": "PROJ-123", "summary": "Similar issue", "score": 0.85}
            ],
        }

        output = _format_output(result)

        assert "Related tickets" in output
        assert "PROJ-123" in output
        assert "Similar issue" in output

    def test_format_output_with_auto_fix(self):
        """Test output formatting when auto-fix is available."""
        from sleuth.sleuth_graph import _format_output

        result = {
            "query": "test query",
            "dd_query": "service:test status:error",
            "logs": [],
            "summary": "Found fixable issue.",
            "related_tickets": [],
            "can_auto_fix": True,
            "no_patchy": False,
        }

        output = _format_output(result)

        assert "Patchy" in output

    def test_format_output_with_error(self):
        """Test output formatting with error message."""
        from sleuth.sleuth_graph import _format_output

        result = {
            "query": "test query",
            "dd_query": "service:test status:error",
            "logs": [],
            "summary": "",
            "related_tickets": [],
            "error": "Connection failed",
        }

        output = _format_output(result)

        assert "Error: Connection failed" in output


class TestSleuthIntegration:
    """Integration tests for the complete Sleuth workflow."""

    @patch("sleuth.sleuth_nodes.requests.post")
    @patch("sleuth.sleuth_nodes.jira_is_configured")
    @patch("sleuth.sleuth_nodes.get_config")
    def test_full_workflow_no_logs(self, mock_config, mock_jira, mock_post):
        """Test complete workflow when no logs are found."""
        from sleuth.sleuth_graph import build_graph

        mock_config.return_value = MagicMock(
            datadog_api_key="key",
            datadog_app_key="app-key",
            datadog_site="datadoghq.eu",
            datadog_env="prod",
            datadog_limit=50,
            datadog_timeout=20,
            openai_api_key=None,  # Disable LLM
        )
        mock_jira.return_value = False

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": []}
        mock_post.return_value = mock_response

        graph = build_graph()
        result = graph.invoke(
            {
                "query": "test errors",
                "hours_back": 24,
            }
        )

        assert result["logs"] == []
        assert "No logs found" in result.get("summary", "")

    @patch("sleuth.sleuth_nodes.requests.post")
    @patch("sleuth.sleuth_nodes.jira_is_configured")
    @patch("sleuth.sleuth_nodes.get_config")
    def test_full_workflow_with_logs(self, mock_config, mock_jira, mock_post):
        """Test complete workflow when logs are found."""
        from sleuth.sleuth_graph import build_graph

        mock_config.return_value = MagicMock(
            datadog_api_key="key",
            datadog_app_key="app-key",
            datadog_site="datadoghq.eu",
            datadog_env="prod",
            datadog_limit=50,
            datadog_timeout=20,
            llm_provider="openai",
            openai_api_key=None,  # Disable LLM for deterministic test
        )
        mock_jira.return_value = False

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "attributes": {
                        "message": "NullPointerException",
                        "status": "error",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        graph = build_graph()
        result = graph.invoke(
            {
                "query": "null pointer errors",
                "hours_back": 24,
            }
        )

        assert len(result["logs"]) == 1
        assert "1 error logs" in result.get("summary", "")
