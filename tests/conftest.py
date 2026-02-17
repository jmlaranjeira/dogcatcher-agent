"""Pytest configuration and fixtures for dogcatcher-agent tests."""

import os
import pytest
from unittest.mock import Mock, patch
from typing import Dict, Any

# Add the project root to the Python path
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def mock_config():
    """Mock configuration for testing."""
    with (
        patch("agent.config.get_config") as mock_get_config,
        patch("agent.nodes.ticket.get_config") as mock_ticket_config,
        patch("agent.performance.get_config") as mock_perf_config,
    ):
        config = Mock()

        # OpenAI config
        config.openai.api_key = "test-openai-key"
        config.openai.model = "gpt-4.1-nano"
        config.openai.temperature = 0.0
        config.openai.response_format = "json_object"

        # Datadog config
        config.datadog.api_key = "test-datadog-api-key"
        config.datadog.app_key = "test-datadog-app-key"
        config.datadog.site = "datadoghq.eu"
        config.datadog.service = "test-service"
        config.datadog.env = "test"
        config.datadog.hours_back = 24
        config.datadog.limit = 50
        config.datadog.max_pages = 3
        config.datadog.timeout = 20
        config.datadog.statuses = "error"
        config.datadog.query_extra = ""
        config.datadog.query_extra_mode = "AND"

        # Jira config
        config.jira.domain = "test.atlassian.net"
        config.jira.user = "test@example.com"
        config.jira.api_token = "test-jira-token"
        config.jira.project_key = "TEST"
        config.jira.search_max_results = 200
        config.jira.search_window_days = 365
        config.jira.similarity_threshold = 0.82
        config.jira.direct_log_threshold = 0.90
        config.jira.partial_log_threshold = 0.70

        # Agent config
        config.agent.auto_create_ticket = False
        config.agent.persist_sim_fp = False
        config.agent.comment_on_duplicate = True
        config.agent.max_tickets_per_run = 3
        config.agent.comment_cooldown_minutes = 120
        config.agent.severity_rules_json = ""
        config.agent.aggregate_email_not_found = False
        config.agent.aggregate_kafka_consumer = False
        config.agent.occ_escalate_enabled = False
        config.agent.occ_escalate_threshold = 10
        config.agent.occ_escalate_to = "high"
        config.agent.get_severity_rules.return_value = {}

        # Logging config
        config.logging.level = "INFO"
        config.logging.format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        # UI config
        config.ui.max_title_length = 120
        config.ui.max_description_preview = 160
        config.ui.max_json_output_length = 1000

        # Validation
        config.validate_configuration.return_value = []
        config.log_configuration.return_value = None

        # Flat attributes (used by production code via Pydantic config)
        config.llm_provider = "openai"
        config.openai_api_key = "test-openai-key"
        config.openai_model = "gpt-4.1-nano"
        config.aws_region = "eu-west-1"
        config.bedrock_model_id = "anthropic.claude-3-haiku-20240307-v1:0"
        config.bedrock_temperature = 0.0
        config.bedrock_max_tokens = 4096
        config.jira_project_key = "TEST"
        config.jira_search_max_results = 200
        config.jira_search_window_days = 365
        config.jira_similarity_threshold = 0.82
        config.jira_direct_log_threshold = 0.90
        config.jira_partial_log_threshold = 0.70
        config.auto_create_ticket = False
        config.persist_sim_fp = False
        config.comment_on_duplicate = True
        config.max_tickets_per_run = 3
        config.comment_cooldown_minutes = 120
        config.aggregate_email_not_found = False
        config.aggregate_kafka_consumer = False
        config.max_title_length = 120
        config.datadog_service = "test-service"
        config.datadog_limit = 50
        config.datadog_max_pages = 3
        config.datadog_timeout = 20
        config.datadog_logs_url = "https://app.datadoghq.eu/logs"

        mock_get_config.return_value = config
        mock_ticket_config.return_value = config
        mock_perf_config.return_value = config
        yield config


@pytest.fixture
def sample_log_data():
    """Sample log data for testing."""
    return {
        "logger": "com.example.service",
        "thread": "main",
        "message": "Database connection failed: Connection timeout",
        "timestamp": "2025-12-09T10:30:00Z",
        "detail": "Failed to connect to database after 30 seconds timeout",
    }


@pytest.fixture
def sample_state():
    """Sample state for testing ticket creation."""
    return {
        "ticket_title": "Database Connection Error",
        "ticket_description": "The application failed to connect to the database due to a timeout.",
        "error_type": "database-connection",
        "log_data": {
            "logger": "com.example.service",
            "thread": "main",
            "message": "Database connection failed: Connection timeout",
            "timestamp": "2025-12-09T10:30:00Z",
            "detail": "Failed to connect to database after 30 seconds timeout",
        },
        "window_hours": 48,
        "_tickets_created_in_run": 0,
        "seen_logs": set(),
    }


@pytest.fixture
def sample_jira_response():
    """Sample Jira API response for testing."""
    return {
        "id": "12345",
        "key": "TEST-123",
        "self": "https://test.atlassian.net/rest/api/3/issue/12345",
        "fields": {
            "summary": "Database Connection Error",
            "description": "The application failed to connect to the database due to a timeout.",
            "status": {"name": "To Do"},
            "priority": {"name": "Medium"},
            "labels": ["datadog-log", "database-connection"],
        },
    }


@pytest.fixture
def sample_duplicate_ticket():
    """Sample duplicate ticket for testing."""
    return {
        "key": "TEST-456",
        "fields": {
            "summary": "Database Connection Error",
            "description": "The application failed to connect to the database due to a timeout.",
            "labels": ["datadog-log", "database-connection"],
            "created": "2025-12-08T10:30:00Z",
            "status": {"name": "In Progress"},
        },
    }


@pytest.fixture
def mock_jira_client():
    """Mock Jira client for testing."""
    with patch("agent.jira.client") as mock_client:
        mock_client.is_configured.return_value = True
        mock_client.search.return_value = {"issues": []}
        mock_client.create_issue.return_value = {
            "id": "12345",
            "key": "TEST-123",
            "self": "https://test.atlassian.net/rest/api/3/issue/12345",
        }
        mock_client.add_comment.return_value = True
        mock_client.add_labels.return_value = True
        yield mock_client


@pytest.fixture
def mock_datadog_client():
    """Mock Datadog client for testing."""
    with patch("agent.datadog.get_logs") as mock_get_logs:
        mock_get_logs.return_value = [
            {
                "logger": "com.example.service",
                "thread": "main",
                "message": "Database connection failed: Connection timeout",
                "timestamp": "2025-12-09T10:30:00Z",
                "detail": "Failed to connect to database after 30 seconds timeout",
            }
        ]
        yield mock_get_logs


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for testing."""
    with patch("openai.OpenAI") as mock_openai:
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = (
            '{"error_type": "database-connection", "ticket_title": "Database Connection Error", "ticket_description": "The application failed to connect to the database due to a timeout.", "severity": "medium"}'
        )

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        yield mock_client


@pytest.fixture
def temp_env():
    """Temporary environment variables for testing."""
    original_env = os.environ.copy()

    # Set test environment variables
    test_env = {
        "OPENAI_API_KEY": "test-openai-key",
        "DATADOG_API_KEY": "test-datadog-api-key",
        "DATADOG_APP_KEY": "test-datadog-app-key",
        "JIRA_DOMAIN": "test.atlassian.net",
        "JIRA_USER": "test@example.com",
        "JIRA_API_TOKEN": "test-jira-token",
        "JIRA_PROJECT_KEY": "TEST",
        "AUTO_CREATE_TICKET": "false",
        "MAX_TICKETS_PER_RUN": "3",
    }

    os.environ.update(test_env)

    yield test_env

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)
