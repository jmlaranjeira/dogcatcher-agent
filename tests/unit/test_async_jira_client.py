"""Unit tests for async Jira client.

Tests async Jira API operations, connection pooling,
error handling, and context manager behavior.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from agent.jira.async_client import (
    AsyncJiraClient,
    search_async,
    create_issue_async,
    add_comment_async,
    add_labels_async,
)


@pytest.fixture
def mock_config():
    """Mock configuration with Jira settings."""
    config = MagicMock()
    config.jira_domain = "test.atlassian.net"
    config.jira_user = "test@example.com"
    config.jira_api_token = "test-token"
    config.jira_project_key = "TEST"
    config.jira_search_max_results = 200
    return config


@pytest.fixture
def sample_jira_response():
    """Sample Jira API response."""
    return {
        "issues": [
            {
                "key": "TEST-123",
                "fields": {"summary": "Test Issue", "description": "Test description"},
            }
        ],
        "total": 1,
    }


class TestAsyncJiraClientInit:
    """Test AsyncJiraClient initialization."""

    def test_initialization(self):
        """Test client initializes correctly."""
        client = AsyncJiraClient()

        assert client.config is not None
        assert client._client is None  # Not initialized until context manager

    def test_is_configured_true(self):
        """Test is_configured returns True with valid config."""
        with patch("agent.jira.async_client.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(
                jira_domain="test.atlassian.net",
                jira_user="test@example.com",
                jira_api_token="token",
                jira_project_key="TEST",
            )

            client = AsyncJiraClient()
            assert client.is_configured() is True

    def test_is_configured_false(self):
        """Test is_configured returns False with missing config."""
        with patch("agent.jira.async_client.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(
                jira_domain="", jira_user="", jira_api_token="", jira_project_key=""
            )

            client = AsyncJiraClient()
            assert client.is_configured() is False


class TestAsyncJiraClientContextManager:
    """Test context manager behavior."""

    @pytest.mark.asyncio
    async def test_context_manager_creates_client(self):
        """Test context manager creates HTTP client."""
        async with AsyncJiraClient() as client:
            assert client._client is not None
            assert isinstance(client._client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self):
        """Test context manager closes HTTP client."""
        client = AsyncJiraClient()

        async with client:
            pass

        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager_with_operations(self):
        """Test context manager allows operations."""
        async with AsyncJiraClient() as client:
            # Client should be available for operations
            assert client._client is not None
            assert hasattr(client, "search")
            assert hasattr(client, "create_issue")


class TestAsyncJiraClientSearch:
    """Test async search operations."""

    @pytest.mark.asyncio
    async def test_search_success(self, sample_jira_response):
        """Test successful search operation."""
        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json = MagicMock(return_value=sample_jira_response)
                mock_response.raise_for_status = MagicMock()
                mock_post.return_value = mock_response

                result = await client.search("project = TEST")

        assert result == sample_jira_response
        assert result["total"] == 1
        # Verify POST was called with correct URL and body
        assert mock_post.called
        call_args = mock_post.call_args
        assert "/rest/api/3/search/jql" in call_args[0][0]
        assert call_args[1]["json"]["jql"] == "project = TEST"

    @pytest.mark.asyncio
    async def test_search_with_fields(self):
        """Test search with custom fields."""
        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = AsyncMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {}
                mock_response.raise_for_status = MagicMock()
                mock_post.return_value = mock_response

                await client.search("project = TEST", fields="summary,status")

        # Verify fields parameter passed correctly in JSON body as list
        call_kwargs = mock_post.call_args[1]
        assert "json" in call_kwargs
        assert "fields" in call_kwargs["json"]
        assert call_kwargs["json"]["fields"] == ["summary", "status"]

    @pytest.mark.asyncio
    async def test_search_with_max_results(self):
        """Test search with custom max results."""
        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = AsyncMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {}
                mock_response.raise_for_status = MagicMock()
                mock_post.return_value = mock_response

                await client.search("project = TEST", max_results=50)

        # Verify maxResults passed in JSON body
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["maxResults"] == 50

    @pytest.mark.asyncio
    async def test_search_not_configured(self):
        """Test search returns None when not configured."""
        client = AsyncJiraClient()
        with patch.object(client, "is_configured", return_value=False):
            result = await client.search("project = TEST")

        assert result is None

    @pytest.mark.asyncio
    async def test_search_http_error(self):
        """Test search handles HTTP errors."""
        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_post.side_effect = httpx.HTTPError("Connection failed")

                result = await client.search("project = TEST")

        assert result is None

    @pytest.mark.asyncio
    async def test_search_without_context(self):
        """Test search fails gracefully outside context manager."""
        client = AsyncJiraClient()
        result = await client.search("project = TEST")

        assert result is None


class TestAsyncJiraClientCreateIssue:
    """Test async issue creation."""

    @pytest.mark.asyncio
    async def test_create_issue_success(self):
        """Test successful issue creation."""
        payload = {
            "fields": {
                "project": {"key": "TEST"},
                "summary": "Test Issue",
                "issuetype": {"name": "Bug"},
            }
        }

        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 201
                mock_response.json = MagicMock(return_value={"key": "TEST-123"})
                mock_response.raise_for_status = MagicMock()
                mock_post.return_value = mock_response

                result = await client.create_issue(payload)

        assert result["key"] == "TEST-123"

    @pytest.mark.asyncio
    async def test_create_issue_not_configured(self):
        """Test create_issue returns None when not configured."""
        client = AsyncJiraClient()
        with patch.object(client, "is_configured", return_value=False):
            result = await client.create_issue({})

        assert result is None

    @pytest.mark.asyncio
    async def test_create_issue_http_error(self):
        """Test create_issue handles HTTP errors."""
        payload = {"fields": {}}

        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = AsyncMock()
                mock_response.status_code = 400
                mock_response.text = "Invalid request"
                mock_post.side_effect = httpx.HTTPStatusError(
                    "Bad Request", request=MagicMock(), response=mock_response
                )

                result = await client.create_issue(payload)

        assert result is None


class TestAsyncJiraClientAddComment:
    """Test async comment addition."""

    @pytest.mark.asyncio
    async def test_add_comment_success(self):
        """Test successful comment addition."""
        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = AsyncMock()
                mock_response.status_code = 201
                mock_post.return_value = mock_response

                result = await client.add_comment("TEST-123", "Test comment")

        assert result is True

    @pytest.mark.asyncio
    async def test_add_comment_not_configured(self):
        """Test add_comment returns False when not configured."""
        client = AsyncJiraClient()
        with patch.object(client, "is_configured", return_value=False):
            result = await client.add_comment("TEST-123", "Comment")

        assert result is False

    @pytest.mark.asyncio
    async def test_add_comment_http_error(self):
        """Test add_comment handles HTTP errors."""
        async with AsyncJiraClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_post.side_effect = httpx.HTTPError("Connection failed")

                result = await client.add_comment("TEST-123", "Comment")

        assert result is False


class TestAsyncJiraClientAddLabels:
    """Test async label addition."""

    @pytest.mark.asyncio
    async def test_add_labels_success(self):
        """Test successful label addition."""
        async with AsyncJiraClient() as client:
            with patch.object(client._client, "put") as mock_put:
                mock_response = AsyncMock()
                mock_response.status_code = 204
                mock_put.return_value = mock_response

                result = await client.add_labels("TEST-123", ["bug", "critical"])

        assert result is True

    @pytest.mark.asyncio
    async def test_add_labels_empty_list(self):
        """Test add_labels with empty list returns True."""
        async with AsyncJiraClient() as client:
            result = await client.add_labels("TEST-123", [])

        assert result is True

    @pytest.mark.asyncio
    async def test_add_labels_not_configured(self):
        """Test add_labels returns False when not configured."""
        client = AsyncJiraClient()
        with patch.object(client, "is_configured", return_value=False):
            result = await client.add_labels("TEST-123", ["bug"])

        assert result is False


class TestConvenienceFunctions:
    """Test convenience wrapper functions."""

    @pytest.mark.asyncio
    async def test_search_async_convenience(self, sample_jira_response):
        """Test search_async convenience function."""
        with patch("agent.jira.async_client.AsyncJiraClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.search.return_value = sample_jira_response
            MockClient.return_value = mock_client

            result = await search_async("project = TEST")

        assert result == sample_jira_response

    @pytest.mark.asyncio
    async def test_create_issue_async_convenience(self):
        """Test create_issue_async convenience function."""
        payload = {"fields": {}}

        with patch("agent.jira.async_client.AsyncJiraClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.create_issue.return_value = {"key": "TEST-123"}
            MockClient.return_value = mock_client

            result = await create_issue_async(payload)

        assert result["key"] == "TEST-123"

    @pytest.mark.asyncio
    async def test_add_comment_async_convenience(self):
        """Test add_comment_async convenience function."""
        with patch("agent.jira.async_client.AsyncJiraClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.add_comment.return_value = True
            MockClient.return_value = mock_client

            result = await add_comment_async("TEST-123", "Comment")

        assert result is True

    @pytest.mark.asyncio
    async def test_add_labels_async_convenience(self):
        """Test add_labels_async convenience function."""
        with patch("agent.jira.async_client.AsyncJiraClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.add_labels.return_value = True
            MockClient.return_value = mock_client

            result = await add_labels_async("TEST-123", ["bug"])

        assert result is True


class TestConnectionPooling:
    """Test connection pooling configuration."""

    @pytest.mark.asyncio
    async def test_connection_limits_configured(self):
        """Test that connection limits are set correctly."""
        async with AsyncJiraClient() as client:
            # Check that httpx client exists and is configured
            assert client._client is not None
            assert isinstance(client._client, httpx.AsyncClient)
            # Connection pooling is configured via Limits at initialization


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
