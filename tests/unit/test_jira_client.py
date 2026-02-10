"""Unit tests for synchronous Jira client.

Tests Jira API operations, authentication, error handling,
and configuration validation.
"""

import pytest
import base64
import requests
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from agent.jira.client import (
    get_jira_project_key,
    get_jira_domain,
    is_configured,
    _headers,
    search,
    create_issue,
    add_comment,
    add_labels,
    get_transitions,
    transition_issue,
    link_issues,
)


class TestConfigAccessors:
    """Test configuration accessor functions."""

    def test_get_jira_project_key(self):
        """Test get_jira_project_key returns config value."""
        mock_config = SimpleNamespace(jira_project_key="TEST")

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = get_jira_project_key()

        assert result == "TEST"

    def test_get_jira_domain(self):
        """Test get_jira_domain returns config value."""
        mock_config = SimpleNamespace(jira_domain="test.atlassian.net")

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = get_jira_domain()

        assert result == "test.atlassian.net"


class TestIsConfigured:
    """Test configuration validation."""

    def test_is_configured_all_set(self):
        """Test is_configured returns True when all fields are set."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = is_configured()

        assert result is True

    def test_is_configured_missing_domain(self):
        """Test is_configured returns False when domain is missing."""
        mock_config = SimpleNamespace(
            jira_domain="",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = is_configured()

        assert result is False

    def test_is_configured_missing_user(self):
        """Test is_configured returns False when user is missing."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = is_configured()

        assert result is False

    def test_is_configured_missing_token(self):
        """Test is_configured returns False when token is missing."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = is_configured()

        assert result is False

    def test_is_configured_missing_project_key(self):
        """Test is_configured returns False when project_key is missing."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = is_configured()

        assert result is False


class TestHeaders:
    """Test authentication header generation."""

    def test_headers_basic_auth_encoding(self):
        """Test headers returns correct Basic auth encoding."""
        mock_config = SimpleNamespace(
            jira_user="test@example.com", jira_api_token="test-token-123"
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            result = _headers()

        # Verify structure
        assert "Authorization" in result
        assert "Content-Type" in result
        assert result["Content-Type"] == "application/json"

        # Verify Basic auth encoding
        auth_header = result["Authorization"]
        assert auth_header.startswith("Basic ")

        # Decode and verify credentials
        encoded_part = auth_header.replace("Basic ", "")
        decoded = base64.b64decode(encoded_part).decode()
        assert decoded == "test@example.com:test-token-123"


class TestSearch:
    """Test Jira search operations."""

    def test_search_success(self):
        """Test successful search operation."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
            jira_search_max_results=200,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "issues": [{"key": "TEST-123", "fields": {"summary": "Test"}}],
            "total": 1,
        }

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = search("project = TEST")

        assert result is not None
        assert result["total"] == 1
        assert len(result["issues"]) == 1

        # Verify POST was called with correct parameters
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "https://test.atlassian.net/rest/api/3/search/jql" in call_args[0]
        assert call_args[1]["json"]["jql"] == "project = TEST"
        assert call_args[1]["json"]["maxResults"] == 200
        assert call_args[1]["json"]["fields"] == ["summary", "description"]

    def test_search_not_configured(self):
        """Test search returns None when not configured."""
        with patch("agent.jira.client.is_configured", return_value=False):
            result = search("project = TEST")

        assert result is None

    def test_search_http_error(self):
        """Test search returns None on HTTP error."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
            jira_search_max_results=200,
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.side_effect = requests.RequestException(
                        "Connection failed"
                    )

                    result = search("project = TEST")

        assert result is None

    def test_search_custom_fields(self):
        """Test search with custom fields parameter."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
            jira_search_max_results=200,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"issues": [], "total": 0}

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = search("project = TEST", fields="summary,status,priority")

        assert result is not None
        # Verify fields were passed correctly
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["fields"] == ["summary", "status", "priority"]

    def test_search_custom_max_results(self):
        """Test search with custom max_results parameter."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
            jira_search_max_results=200,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"issues": [], "total": 0}

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = search("project = TEST", max_results=50)

        assert result is not None
        # Verify max_results was passed correctly
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["maxResults"] == 50


class TestCreateIssue:
    """Test Jira issue creation."""

    def test_create_issue_success(self):
        """Test successful issue creation."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        payload = {
            "fields": {
                "project": {"key": "TEST"},
                "summary": "Test Issue",
                "issuetype": {"name": "Bug"},
            }
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"key": "TEST-123", "id": "10001"}

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = create_issue(payload)

        assert result is not None
        assert result["key"] == "TEST-123"
        assert result["id"] == "10001"

        # Verify POST was called correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "https://test.atlassian.net/rest/api/3/issue" in call_args[0]
        assert call_args[1]["json"] == payload

    def test_create_issue_not_configured(self):
        """Test create_issue returns None when not configured."""
        with patch("agent.jira.client.is_configured", return_value=False):
            result = create_issue({})

        assert result is None

    def test_create_issue_http_error_with_response_body(self):
        """Test create_issue returns None on HTTP error and logs response body."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        payload = {"fields": {}}

        mock_response = MagicMock()
        mock_response.text = '{"errorMessages":["Field \'summary\' is required"]}'

        mock_exception = requests.RequestException("Bad Request")
        mock_exception.response = mock_response

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.side_effect = mock_exception

                    result = create_issue(payload)

        assert result is None

    def test_create_issue_http_error_without_response(self):
        """Test create_issue returns None on HTTP error without response body."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.side_effect = requests.RequestException(
                        "Connection timeout"
                    )

                    result = create_issue({})

        assert result is None


class TestAddComment:
    """Test Jira comment addition."""

    def test_add_comment_success(self):
        """Test successful comment addition."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = add_comment("TEST-123", "This is a test comment")

        assert result is True

        # Verify POST was called correctly with ADF body
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert (
            "https://test.atlassian.net/rest/api/3/issue/TEST-123/comment"
            in call_args[0]
        )
        json_body = call_args[1]["json"]
        assert "body" in json_body
        assert json_body["body"]["type"] == "doc"
        assert json_body["body"]["version"] == 1

    def test_add_comment_success_status_200(self):
        """Test successful comment addition with status 200."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = add_comment("TEST-123", "Comment")

        assert result is True

    def test_add_comment_http_error(self):
        """Test add_comment returns False on HTTP error."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.side_effect = requests.RequestException(
                        "Connection failed"
                    )

                    result = add_comment("TEST-123", "Comment")

        assert result is False

    def test_add_comment_not_configured(self):
        """Test add_comment returns False when not configured."""
        with patch("agent.jira.client.is_configured", return_value=False):
            result = add_comment("TEST-123", "Comment")

        assert result is False


class TestAddLabels:
    """Test Jira label addition."""

    def test_add_labels_success(self):
        """Test successful label addition."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.put") as mock_put:
                    mock_put.return_value = mock_response

                    result = add_labels("TEST-123", ["bug", "critical"])

        assert result is True

        # Verify PUT was called correctly
        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert "https://test.atlassian.net/rest/api/3/issue/TEST-123" in call_args[0]
        json_body = call_args[1]["json"]
        assert "update" in json_body
        assert "labels" in json_body["update"]
        assert json_body["update"]["labels"] == [{"add": "bug"}, {"add": "critical"}]

    def test_add_labels_success_status_200(self):
        """Test successful label addition with status 200."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.put") as mock_put:
                    mock_put.return_value = mock_response

                    result = add_labels("TEST-123", ["bug"])

        assert result is True

    def test_add_labels_not_configured(self):
        """Test add_labels returns False when not configured."""
        with patch("agent.jira.client.is_configured", return_value=False):
            result = add_labels("TEST-123", ["bug"])

        assert result is False

    def test_add_labels_empty_list(self):
        """Test add_labels returns True with empty labels list."""
        with patch("agent.jira.client.is_configured", return_value=True):
            result = add_labels("TEST-123", [])

        assert result is True


class TestGetTransitions:
    """Test Jira transition retrieval."""

    def test_get_transitions_success(self):
        """Test successful transitions retrieval."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "transitions": [
                {"id": "11", "name": "In Progress", "to": {"name": "In Progress"}},
                {"id": "21", "name": "Done", "to": {"name": "Done"}},
            ]
        }

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.get") as mock_get:
                    mock_get.return_value = mock_response

                    result = get_transitions("TEST-123")

        assert result is not None
        assert len(result) == 2
        assert result[0]["id"] == "11"
        assert result[0]["name"] == "In Progress"
        assert result[0]["to_status"] == "In Progress"
        assert result[1]["id"] == "21"
        assert result[1]["name"] == "Done"
        assert result[1]["to_status"] == "Done"

        # Verify GET was called correctly
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert (
            "https://test.atlassian.net/rest/api/3/issue/TEST-123/transitions"
            in call_args[0]
        )

    def test_get_transitions_http_error(self):
        """Test get_transitions returns None on HTTP error."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.get") as mock_get:
                    mock_get.side_effect = requests.RequestException(
                        "Connection failed"
                    )

                    result = get_transitions("TEST-123")

        assert result is None

    def test_get_transitions_not_configured(self):
        """Test get_transitions returns None when not configured."""
        with patch("agent.jira.client.is_configured", return_value=False):
            result = get_transitions("TEST-123")

        assert result is None


class TestTransitionIssue:
    """Test Jira issue transitions."""

    def test_transition_issue_success(self):
        """Test successful issue transition."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = transition_issue("TEST-123", "21")

        assert result is True

        # Verify POST was called correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert (
            "https://test.atlassian.net/rest/api/3/issue/TEST-123/transitions"
            in call_args[0]
        )
        json_body = call_args[1]["json"]
        assert json_body["transition"]["id"] == "21"
        assert "fields" not in json_body

    def test_transition_issue_with_resolution(self):
        """Test issue transition with resolution field."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = transition_issue("TEST-123", "21", resolution="Done")

        assert result is True

        # Verify resolution was included
        call_kwargs = mock_post.call_args[1]
        json_body = call_kwargs["json"]
        assert "fields" in json_body
        assert json_body["fields"]["resolution"]["name"] == "Done"

    def test_transition_issue_success_status_200(self):
        """Test successful issue transition with status 200."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = transition_issue("TEST-123", "21")

        assert result is True

    def test_transition_issue_http_error(self):
        """Test transition_issue returns False on HTTP error."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.side_effect = requests.RequestException(
                        "Connection failed"
                    )

                    result = transition_issue("TEST-123", "21")

        assert result is False

    def test_transition_issue_not_configured(self):
        """Test transition_issue returns False when not configured."""
        with patch("agent.jira.client.is_configured", return_value=False):
            result = transition_issue("TEST-123", "21")

        assert result is False


class TestLinkIssues:
    """Test Jira issue linking."""

    def test_link_issues_success(self):
        """Test successful issue linking."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = link_issues("TEST-123", "TEST-124", "Duplicate")

        assert result is True

        # Verify POST was called correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "https://test.atlassian.net/rest/api/3/issueLink" in call_args[0]
        json_body = call_args[1]["json"]
        assert json_body["type"]["name"] == "Duplicate"
        assert json_body["inwardIssue"]["key"] == "TEST-123"
        assert json_body["outwardIssue"]["key"] == "TEST-124"

    def test_link_issues_success_status_200(self):
        """Test successful issue linking with status 200."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.return_value = mock_response

                    result = link_issues("TEST-123", "TEST-124")

        assert result is True

    def test_link_issues_http_error(self):
        """Test link_issues returns False on HTTP error."""
        mock_config = SimpleNamespace(
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-token",
            jira_project_key="TEST",
        )

        with patch("agent.jira.client.get_config", return_value=mock_config):
            with patch("agent.jira.client.is_configured", return_value=True):
                with patch("agent.jira.client.requests.post") as mock_post:
                    mock_post.side_effect = requests.RequestException(
                        "Connection failed"
                    )

                    result = link_issues("TEST-123", "TEST-124")

        assert result is False

    def test_link_issues_not_configured(self):
        """Test link_issues returns False when not configured."""
        with patch("agent.jira.client.is_configured", return_value=False):
            result = link_issues("TEST-123", "TEST-124")

        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
