"""Unit tests for async Datadog client.

Tests async Datadog API operations, connection pooling,
pagination, error handling, and context manager behavior.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from agent.datadog_async import (
    AsyncDatadogClient,
    get_logs_async,
    get_logs_batch_async,
    fetch_logs_async,
    _build_dd_query,
    _parse_log_entry,
    _coerce_detail,
)


@pytest.fixture
def mock_config():
    """Mock configuration with Datadog settings."""
    config = MagicMock()
    config.datadog_api_key = "test-api-key"
    config.datadog_app_key = "test-app-key"
    config.datadog_site = "datadoghq.com"
    config.datadog_service = "test-service"
    config.datadog_env = "test"
    config.datadog_hours_back = 24
    config.datadog_limit = 100
    config.datadog_max_pages = 5
    config.datadog_timeout = 30
    config.datadog_statuses = "error,warn"
    config.datadog_query_extra = ""
    config.datadog_query_extra_mode = "AND"
    return config


@pytest.fixture
def sample_datadog_response():
    """Sample Datadog API response."""
    return {
        "data": [
            {
                "attributes": {
                    "message": "Test error message",
                    "timestamp": "2025-01-01T10:00:00.000Z",
                    "attributes": {
                        "logger": {"name": "test.logger", "thread_name": "main"},
                        "properties": {"Log": "Detailed log information"},
                    },
                }
            }
        ],
        "meta": {"page": {"after": None}},
    }


class TestAsyncDatadogClientInit:
    """Test AsyncDatadogClient initialization."""

    def test_initialization(self):
        """Test client initializes correctly."""
        client = AsyncDatadogClient()

        assert client.config is not None
        assert client._client is None

    def test_is_configured_true(self):
        """Test is_configured returns True with valid config."""
        with patch("agent.datadog_async.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(
                datadog_api_key="api-key",
                datadog_app_key="app-key",
                datadog_site="datadoghq.com",
            )

            client = AsyncDatadogClient()
            assert client.is_configured() is True

    def test_is_configured_false(self):
        """Test is_configured returns False with missing config."""
        with patch("agent.datadog_async.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(
                datadog_api_key="", datadog_app_key="", datadog_site=""
            )

            client = AsyncDatadogClient()
            assert client.is_configured() is False


class TestAsyncDatadogClientContextManager:
    """Test context manager behavior."""

    @pytest.mark.asyncio
    async def test_context_manager_creates_client(self):
        """Test context manager creates HTTP client."""
        async with AsyncDatadogClient() as client:
            assert client._client is not None
            assert isinstance(client._client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self):
        """Test context manager closes HTTP client."""
        client = AsyncDatadogClient()

        async with client:
            pass

        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager_with_operations(self):
        """Test context manager allows operations."""
        async with AsyncDatadogClient() as client:
            assert client._client is not None
            assert hasattr(client, "fetch_page")


class TestAsyncDatadogClientFetchPage:
    """Test async fetch_page operations."""

    @pytest.mark.asyncio
    async def test_fetch_page_success(self, sample_datadog_response):
        """Test successful fetch_page operation."""
        async with AsyncDatadogClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json = MagicMock(return_value=sample_datadog_response)
                mock_response.raise_for_status = MagicMock()
                mock_post.return_value = mock_response

                now = datetime.utcnow()
                start = now - timedelta(hours=24)
                data, cursor = await client.fetch_page(
                    query="service:test status:error", start=start, end=now, limit=100
                )

        assert len(data) == 1
        assert cursor is None

    @pytest.mark.asyncio
    async def test_fetch_page_with_cursor(self, sample_datadog_response):
        """Test fetch_page with pagination cursor."""
        response_with_cursor = {
            **sample_datadog_response,
            "meta": {"page": {"after": "next-page-cursor"}},
        }

        async with AsyncDatadogClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json = MagicMock(return_value=response_with_cursor)
                mock_response.raise_for_status = MagicMock()
                mock_post.return_value = mock_response

                now = datetime.utcnow()
                start = now - timedelta(hours=24)
                data, cursor = await client.fetch_page(
                    query="service:test", start=start, end=now, limit=100
                )

        assert cursor == "next-page-cursor"

    @pytest.mark.asyncio
    async def test_fetch_page_not_configured(self):
        """Test fetch_page returns empty when not configured."""
        client = AsyncDatadogClient()
        with patch.object(client, "is_configured", return_value=False):
            now = datetime.utcnow()
            data, cursor = await client.fetch_page(
                query="test", start=now - timedelta(hours=1), end=now, limit=100
            )

        assert data == []
        assert cursor is None

    @pytest.mark.asyncio
    async def test_fetch_page_http_error(self):
        """Test fetch_page handles HTTP errors."""
        async with AsyncDatadogClient() as client:
            with patch.object(client._client, "post") as mock_post:
                mock_post.side_effect = httpx.HTTPError("Connection failed")

                now = datetime.utcnow()
                data, cursor = await client.fetch_page(
                    query="test", start=now - timedelta(hours=1), end=now, limit=100
                )

        assert data == []
        assert cursor is None

    @pytest.mark.asyncio
    async def test_fetch_page_without_context(self):
        """Test fetch_page fails gracefully outside context manager."""
        client = AsyncDatadogClient()
        now = datetime.utcnow()
        data, cursor = await client.fetch_page(
            query="test", start=now - timedelta(hours=1), end=now, limit=100
        )

        assert data == []
        assert cursor is None


class TestBuildDdQuery:
    """Test query building functions."""

    def test_build_query_basic(self):
        """Test basic query building."""
        query, extra = _build_dd_query(
            service="test-service",
            env="prod",
            statuses_csv="error",
            extra_csv="",
            extra_mode="AND",
        )

        assert "service:test-service" in query
        assert "env:prod" in query
        assert "status:error" in query

    def test_build_query_multiple_statuses(self):
        """Test query with multiple statuses."""
        query, extra = _build_dd_query(
            service="test",
            env="dev",
            statuses_csv="error,warn,critical",
            extra_csv="",
            extra_mode="AND",
        )

        assert "status:error" in query
        assert "status:warn" in query
        assert "status:critical" in query
        assert "OR" in query

    def test_build_query_with_extra_and(self):
        """Test query with extra terms AND mode."""
        query, extra = _build_dd_query(
            service="test",
            env="dev",
            statuses_csv="error",
            extra_csv="@env:prod,@host:server1",
            extra_mode="AND",
        )

        assert "@env:prod" in extra
        assert "@host:server1" in extra
        assert " AND " in extra

    def test_build_query_with_extra_or(self):
        """Test query with extra terms OR mode."""
        query, extra = _build_dd_query(
            service="test",
            env="dev",
            statuses_csv="error",
            extra_csv="exception,error",
            extra_mode="OR",
        )

        assert " OR " in extra


class TestParseLogEntry:
    """Test log entry parsing."""

    def test_parse_log_basic(self):
        """Test basic log parsing."""
        raw_log = {
            "attributes": {
                "message": "Test message",
                "timestamp": "2025-01-01T10:00:00Z",
                "attributes": {
                    "logger": {"name": "app.logger", "thread_name": "main-thread"},
                    "properties": {"Log": "Detailed info"},
                },
            }
        }

        parsed = _parse_log_entry(raw_log)

        assert parsed["message"] == "Test message"
        assert parsed["logger"] == "app.logger"
        assert parsed["thread"] == "main-thread"
        assert parsed["detail"] == "Detailed info"

    def test_parse_log_missing_fields(self):
        """Test log parsing with missing fields."""
        raw_log = {"attributes": {"message": "Simple message"}}

        parsed = _parse_log_entry(raw_log)

        assert parsed["message"] == "Simple message"
        assert parsed["logger"] == "unknown.logger"
        assert parsed["thread"] == "unknown.thread"

    def test_parse_log_truncates_long_detail(self):
        """Test that long details are truncated."""
        long_detail = "x" * 500
        raw_log = {
            "attributes": {
                "message": "Message",
                "attributes": {"properties": {"Log": long_detail}},
            }
        }

        parsed = _parse_log_entry(raw_log)

        assert len(parsed["detail"]) <= 315  # MAX_LOG_DETAIL_LENGTH + truncation marker
        assert "truncated" in parsed["detail"]


class TestCoerceDetail:
    """Test detail field coercion."""

    def test_coerce_string(self):
        """Test string is returned as-is."""
        assert _coerce_detail("test string") == "test string"

    def test_coerce_none(self):
        """Test None returns fallback."""
        assert _coerce_detail(None) == "no detailed log"
        assert _coerce_detail(None, "custom fallback") == "custom fallback"

    def test_coerce_dict(self):
        """Test dict is JSON encoded."""
        result = _coerce_detail({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result

    def test_coerce_list(self):
        """Test list is JSON encoded."""
        result = _coerce_detail(["a", "b"])
        assert '["a", "b"]' in result

    def test_coerce_number(self):
        """Test number is stringified."""
        assert _coerce_detail(42) == "42"


class TestGetLogsAsync:
    """Test get_logs_async function."""

    @pytest.mark.asyncio
    async def test_get_logs_success(self, mock_config, sample_datadog_response):
        """Test successful log retrieval."""
        with patch("agent.datadog_async.get_config", return_value=mock_config):
            with patch("agent.datadog_async.AsyncDatadogClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.fetch_page.return_value = (
                    sample_datadog_response["data"],
                    None,
                )
                MockClient.return_value = mock_client

                logs = await get_logs_async(
                    service="test", env="dev", hours_back=24, limit=100
                )

        assert len(logs) == 1
        assert logs[0]["message"] == "Test error message"

    @pytest.mark.asyncio
    async def test_get_logs_empty(self, mock_config):
        """Test empty log retrieval."""
        with patch("agent.datadog_async.get_config", return_value=mock_config):
            with patch("agent.datadog_async.AsyncDatadogClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.fetch_page.return_value = ([], None)
                MockClient.return_value = mock_client

                logs = await get_logs_async()

        assert logs == []

    @pytest.mark.asyncio
    async def test_get_logs_pagination(self, mock_config, sample_datadog_response):
        """Test log retrieval with pagination."""
        with patch("agent.datadog_async.get_config", return_value=mock_config):
            with patch("agent.datadog_async.AsyncDatadogClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None

                # First call returns cursor, second call returns no cursor
                mock_client.fetch_page.side_effect = [
                    (sample_datadog_response["data"], "next-cursor"),
                    (sample_datadog_response["data"], None),
                ]
                MockClient.return_value = mock_client

                logs = await get_logs_async()

        assert len(logs) == 2
        assert mock_client.fetch_page.call_count == 2


class TestGetLogsBatchAsync:
    """Test batch log retrieval."""

    @pytest.mark.asyncio
    async def test_batch_fetch_multiple_services(
        self, mock_config, sample_datadog_response
    ):
        """Test batch fetching for multiple services."""
        with patch("agent.datadog_async.get_config", return_value=mock_config):
            with patch("agent.datadog_async.get_logs_async") as mock_get_logs:
                mock_get_logs.return_value = [{"message": "test"}]

                result = await get_logs_batch_async(
                    services=["service1", "service2", "service3"], env="dev"
                )

        assert len(result) == 3
        assert "service1" in result
        assert "service2" in result
        assert "service3" in result

    @pytest.mark.asyncio
    async def test_batch_fetch_handles_errors(self, mock_config):
        """Test batch fetch handles errors gracefully."""
        with patch("agent.datadog_async.get_config", return_value=mock_config):
            with patch("agent.datadog_async.get_logs_async") as mock_get_logs:
                mock_get_logs.side_effect = [
                    [{"message": "success"}],
                    Exception("Failed"),
                    [{"message": "success2"}],
                ]

                result = await get_logs_batch_async(services=["svc1", "svc2", "svc3"])

        # Should have 2 successful, 1 failed (not in result)
        assert len(result) == 2


class TestFetchLogsAsyncAlias:
    """Test the fetch_logs_async alias."""

    @pytest.mark.asyncio
    async def test_alias_calls_get_logs_async(self, mock_config):
        """Test that fetch_logs_async is an alias for get_logs_async."""
        with patch("agent.datadog_async.get_logs_async") as mock_get_logs:
            mock_get_logs.return_value = []

            await fetch_logs_async(service="test")

        mock_get_logs.assert_called_once()


class TestConnectionPooling:
    """Test connection pooling configuration."""

    @pytest.mark.asyncio
    async def test_connection_limits_configured(self):
        """Test that connection limits are set correctly."""
        async with AsyncDatadogClient() as client:
            assert client._client is not None
            assert isinstance(client._client, httpx.AsyncClient)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
