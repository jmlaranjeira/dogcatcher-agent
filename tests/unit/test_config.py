"""Unit tests for configuration schema."""

import pytest
import os
import json
from unittest.mock import patch
from pydantic import ValidationError

from agent.config import (
    OpenAIConfig,
    DatadogConfig,
    JiraConfig,
    AgentConfig,
    LoggingConfig,
    UIConfig,
    Config,
    get_config,
    reload_config,
)


class TestOpenAIConfig:
    """Test OpenAI configuration validation."""

    def test_openai_config_valid(self):
        """Test valid OpenAI configuration."""
        config = OpenAIConfig(
            api_key="test-key",
            model="gpt-4.1-nano",
            temperature=0.0,
            response_format="json_object",
        )

        assert config.api_key == "test-key"
        assert config.model == "gpt-4.1-nano"
        assert config.temperature == 0.0
        assert config.response_format == "json_object"

    def test_openai_config_defaults(self):
        """Test OpenAI configuration defaults."""
        config = OpenAIConfig(api_key="test-key")

        assert config.model == "gpt-4.1-nano"
        assert config.temperature == 0.0
        assert config.response_format == "json_object"

    def test_openai_config_temperature_validation(self):
        """Test temperature validation."""
        # Valid temperature
        config = OpenAIConfig(api_key="test-key", temperature=1.5)
        assert config.temperature == 1.5

        # Invalid temperature (too high)
        with pytest.raises(ValidationError):
            OpenAIConfig(api_key="test-key", temperature=3.0)

        # Invalid temperature (negative)
        with pytest.raises(ValidationError):
            OpenAIConfig(api_key="test-key", temperature=-0.1)

    def test_openai_config_response_format_validation(self):
        """Test response format validation."""
        # Valid formats
        config1 = OpenAIConfig(api_key="test-key", response_format="json_object")
        assert config1.response_format == "json_object"

        config2 = OpenAIConfig(api_key="test-key", response_format="text")
        assert config2.response_format == "text"

        # Invalid format
        with pytest.raises(ValidationError):
            OpenAIConfig(api_key="test-key", response_format="invalid")


class TestDatadogConfig:
    """Test Datadog configuration validation."""

    def test_datadog_config_valid(self):
        """Test valid Datadog configuration."""
        config = DatadogConfig(
            api_key="test-api-key",
            app_key="test-app-key",
            site="datadoghq.eu",
            service="test-service",
            env="test",
            hours_back=24,
            limit=50,
            max_pages=3,
            timeout=20,
            statuses="error",
            query_extra="",
            query_extra_mode="AND",
        )

        assert config.api_key == "test-api-key"
        assert config.app_key == "test-app-key"
        assert config.site == "datadoghq.eu"
        assert config.service == "test-service"
        assert config.env == "test"
        assert config.hours_back == 24
        assert config.limit == 50
        assert config.max_pages == 3
        assert config.timeout == 20
        assert config.statuses == "error"
        assert config.query_extra == ""
        assert config.query_extra_mode == "AND"

    def test_datadog_config_defaults(self):
        """Test Datadog configuration defaults."""
        config = DatadogConfig(api_key="test-api-key", app_key="test-app-key")

        assert config.site == "datadoghq.eu"
        assert config.service == "myservice"
        assert config.env == "dev"
        assert config.hours_back == 24
        assert config.limit == 50
        assert config.max_pages == 3
        assert config.timeout == 20
        assert config.statuses == "error"
        assert config.query_extra == ""
        assert config.query_extra_mode == "AND"

    def test_datadog_config_hours_back_validation(self):
        """Test hours_back validation."""
        # Valid range
        config = DatadogConfig(api_key="test-key", app_key="test-key", hours_back=168)
        assert config.hours_back == 168

        # Too high
        with pytest.raises(ValidationError):
            DatadogConfig(api_key="test-key", app_key="test-key", hours_back=200)

        # Too low
        with pytest.raises(ValidationError):
            DatadogConfig(api_key="test-key", app_key="test-key", hours_back=0)

    def test_datadog_config_limit_validation(self):
        """Test limit validation."""
        # Valid range
        config = DatadogConfig(api_key="test-key", app_key="test-key", limit=1000)
        assert config.limit == 1000

        # Too high
        with pytest.raises(ValidationError):
            DatadogConfig(api_key="test-key", app_key="test-key", limit=2000)

        # Too low
        with pytest.raises(ValidationError):
            DatadogConfig(api_key="test-key", app_key="test-key", limit=0)

    def test_datadog_config_query_extra_mode_validation(self):
        """Test query_extra_mode validation."""
        # Valid modes
        config1 = DatadogConfig(
            api_key="test-key", app_key="test-key", query_extra_mode="AND"
        )
        assert config1.query_extra_mode == "AND"

        config2 = DatadogConfig(
            api_key="test-key", app_key="test-key", query_extra_mode="OR"
        )
        assert config2.query_extra_mode == "OR"

        # Case insensitive
        config3 = DatadogConfig(
            api_key="test-key", app_key="test-key", query_extra_mode="and"
        )
        assert config3.query_extra_mode == "AND"

        # Invalid mode
        with pytest.raises(ValidationError):
            DatadogConfig(
                api_key="test-key", app_key="test-key", query_extra_mode="INVALID"
            )


class TestJiraConfig:
    """Test Jira configuration validation."""

    def test_jira_config_valid(self):
        """Test valid Jira configuration."""
        config = JiraConfig(
            domain="test.atlassian.net",
            user="test@example.com",
            api_token="test-token",
            project_key="TEST",
            search_max_results=200,
            search_window_days=365,
            similarity_threshold=0.82,
            direct_log_threshold=0.90,
            partial_log_threshold=0.70,
        )

        assert config.domain == "test.atlassian.net"
        assert config.user == "test@example.com"
        assert config.api_token == "test-token"
        assert config.project_key == "TEST"
        assert config.search_max_results == 200
        assert config.search_window_days == 365
        assert config.similarity_threshold == 0.82
        assert config.direct_log_threshold == 0.90
        assert config.partial_log_threshold == 0.70

    def test_jira_config_defaults(self):
        """Test Jira configuration defaults."""
        config = JiraConfig(
            domain="test.atlassian.net",
            user="test@example.com",
            api_token="test-token",
            project_key="TEST",
        )

        assert config.search_max_results == 200
        assert config.search_window_days == 365
        assert config.similarity_threshold == 0.82
        assert config.direct_log_threshold == 0.90
        assert config.partial_log_threshold == 0.70

    def test_jira_config_threshold_validation(self):
        """Test threshold validation."""
        # Valid thresholds
        config = JiraConfig(
            domain="test.atlassian.net",
            user="test@example.com",
            api_token="test-token",
            project_key="TEST",
            similarity_threshold=0.5,
            direct_log_threshold=0.8,
            partial_log_threshold=0.6,
        )

        assert config.similarity_threshold == 0.5
        assert config.direct_log_threshold == 0.8
        assert config.partial_log_threshold == 0.6

        # Invalid thresholds (too high)
        with pytest.raises(ValidationError):
            JiraConfig(
                domain="test.atlassian.net",
                user="test@example.com",
                api_token="test-token",
                project_key="TEST",
                similarity_threshold=1.5,
            )

        # Invalid thresholds (negative)
        with pytest.raises(ValidationError):
            JiraConfig(
                domain="test.atlassian.net",
                user="test@example.com",
                api_token="test-token",
                project_key="TEST",
                similarity_threshold=-0.1,
            )


class TestAgentConfig:
    """Test Agent configuration validation."""

    def test_agent_config_valid(self):
        """Test valid Agent configuration."""
        config = AgentConfig(
            auto_create_ticket=False,
            persist_sim_fp=False,
            comment_on_duplicate=True,
            max_tickets_per_run=3,
            comment_cooldown_minutes=120,
            severity_rules_json='{"database-connection": "high"}',
            aggregate_email_not_found=False,
            aggregate_kafka_consumer=False,
            occ_escalate_enabled=False,
            occ_escalate_threshold=10,
            occ_escalate_to="high",
        )

        assert config.auto_create_ticket is False
        assert config.persist_sim_fp is False
        assert config.comment_on_duplicate is True
        assert config.max_tickets_per_run == 3
        assert config.comment_cooldown_minutes == 120
        assert config.severity_rules_json == '{"database-connection": "high"}'
        assert config.aggregate_email_not_found is False
        assert config.aggregate_kafka_consumer is False
        assert config.occ_escalate_enabled is False
        assert config.occ_escalate_threshold == 10
        assert config.occ_escalate_to == "high"

    def test_agent_config_defaults(self, monkeypatch):
        """Test Agent configuration defaults."""
        # Clear env vars that override defaults (e.g. from .env)
        for key in (
            "SEVERITY_RULES_JSON",
            "AUTO_CREATE_TICKET",
            "COMMENT_ON_DUPLICATE",
            "MAX_TICKETS_PER_RUN",
            "PERSIST_SIM_FP",
            "COMMENT_COOLDOWN_MINUTES",
            "AGGREGATE_EMAIL_NOT_FOUND",
            "AGGREGATE_KAFKA_CONSUMER",
            "OCC_ESCALATE_ENABLED",
            "OCC_ESCALATE_THRESHOLD",
            "OCC_ESCALATE_TO",
        ):
            monkeypatch.delenv(key, raising=False)

        config = AgentConfig()

        assert config.auto_create_ticket is False
        assert config.persist_sim_fp is False
        assert config.comment_on_duplicate is True
        assert config.max_tickets_per_run == 3
        assert config.comment_cooldown_minutes == 120
        assert config.severity_rules_json == ""
        assert config.aggregate_email_not_found is False
        assert config.aggregate_kafka_consumer is False
        assert config.occ_escalate_enabled is False
        assert config.occ_escalate_threshold == 10
        assert config.occ_escalate_to == "high"

    def test_agent_config_severity_rules_validation(self):
        """Test severity rules JSON validation."""
        # Valid JSON
        config = AgentConfig(
            severity_rules_json='{"database-connection": "high", "auth-error": "medium"}'
        )
        assert (
            config.severity_rules_json
            == '{"database-connection": "high", "auth-error": "medium"}'
        )

        # Invalid JSON
        with pytest.raises(ValidationError):
            AgentConfig(severity_rules_json='{"invalid": json}')

        # Invalid severity values
        with pytest.raises(ValidationError):
            AgentConfig(severity_rules_json='{"database-connection": "invalid"}')

        # Empty JSON (valid)
        config = AgentConfig(severity_rules_json="")
        assert config.severity_rules_json == ""

    def test_agent_config_escalate_to_validation(self):
        """Test escalation target validation."""
        # Valid targets
        for target in ["low", "medium", "high"]:
            config = AgentConfig(occ_escalate_to=target)
            assert config.occ_escalate_to == target

        # Case insensitive
        config = AgentConfig(occ_escalate_to="HIGH")
        assert config.occ_escalate_to == "high"

        # Invalid target
        with pytest.raises(ValidationError):
            AgentConfig(occ_escalate_to="invalid")

    def test_agent_config_get_severity_rules(self):
        """Test severity rules parsing."""
        # Valid rules
        config = AgentConfig(
            severity_rules_json='{"database-connection": "high", "auth-error": "medium"}'
        )
        rules = config.get_severity_rules()

        assert rules == {"database-connection": "high", "auth-error": "medium"}

        # Empty rules
        config = AgentConfig(severity_rules_json="")
        rules = config.get_severity_rules()

        assert rules == {}


class TestLoggingConfig:
    """Test Logging configuration validation."""

    def test_logging_config_valid(self):
        """Test valid Logging configuration."""
        config = LoggingConfig(level="INFO", format="%(asctime)s - %(message)s")

        assert config.level == "INFO"
        assert config.format == "%(asctime)s - %(message)s"

    def test_logging_config_defaults(self):
        """Test Logging configuration defaults."""
        config = LoggingConfig()

        assert config.level == "INFO"
        assert config.format == "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    def test_logging_config_level_validation(self):
        """Test log level validation."""
        # Valid levels
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = LoggingConfig(level=level)
            assert config.level == level

        # Case insensitive
        config = LoggingConfig(level="info")
        assert config.level == "INFO"

        # Invalid level
        with pytest.raises(ValidationError):
            LoggingConfig(level="INVALID")


class TestUIConfig:
    """Test UI configuration validation."""

    def test_ui_config_valid(self):
        """Test valid UI configuration."""
        config = UIConfig(
            max_title_length=120,
            max_description_preview=160,
            max_json_output_length=1000,
        )

        assert config.max_title_length == 120
        assert config.max_description_preview == 160
        assert config.max_json_output_length == 1000

    def test_ui_config_defaults(self):
        """Test UI configuration defaults."""
        config = UIConfig()

        assert config.max_title_length == 120
        assert config.max_description_preview == 160
        assert config.max_json_output_length == 1000

    def test_ui_config_length_validation(self):
        """Test length validation."""
        # Valid lengths
        config = UIConfig(max_title_length=255)
        assert config.max_title_length == 255

        # Too high
        with pytest.raises(ValidationError):
            UIConfig(max_title_length=300)

        # Too low
        with pytest.raises(ValidationError):
            UIConfig(max_title_length=5)


class TestConfigIntegration:
    """Test complete configuration integration."""

    def test_config_validation_success(self):
        """Test successful configuration validation."""
        config = Config(
            openai_api_key="test-openai-key",
            datadog_api_key="test-datadog-api-key",
            datadog_app_key="test-datadog-app-key",
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-jira-token",
            jira_project_key="TEST",
        )

        issues = config.validate_configuration()
        assert len(issues) == 0

    def test_config_validation_missing_fields(self):
        """Test configuration validation with missing fields."""
        # Note: Config() will have empty string defaults (""), which are falsy
        # So validation will detect them as missing
        config = Config(
            openai_api_key="",  # Explicitly set empty values to test validation
            datadog_api_key="",
            datadog_app_key="",
            jira_domain="",
            jira_user="",
            jira_api_token="",
            jira_project_key="",
        )

        issues = config.validate_configuration()
        assert len(issues) > 0
        assert any("OPENAI_API_KEY is required" in issue for issue in issues)
        assert any("DATADOG_API_KEY is required" in issue for issue in issues)
        assert any("JIRA_DOMAIN is required" in issue for issue in issues)

    def test_config_validation_dangerous_settings(self):
        """Test configuration validation for dangerous settings."""
        config = Config(
            openai_api_key="test-openai-key",
            datadog_api_key="test-datadog-api-key",
            datadog_app_key="test-datadog-app-key",
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-jira-token",
            jira_project_key="TEST",
            auto_create_ticket=True,
            max_tickets_per_run=0,
        )

        issues = config.validate_configuration()
        assert any(
            "MAX_TICKETS_PER_RUN=0 with AUTO_CREATE_TICKET=true is dangerous" in issue
            for issue in issues
        )

    def test_config_validation_low_limits(self):
        """Test configuration validation for low limits."""
        # Use limits that trigger validation warnings:
        # - datadog_limit < 2 triggers "DATADOG_LIMIT is very low"
        # - jira_similarity_threshold < 0.5 triggers "JIRA_SIMILARITY_THRESHOLD is very low"
        config = Config(
            openai_api_key="test-openai-key",
            datadog_api_key="test-datadog-api-key",
            datadog_app_key="test-datadog-app-key",
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-jira-token",
            jira_project_key="TEST",
            datadog_limit=1,  # Changed from 5 to 1 to trigger validation
            jira_similarity_threshold=0.3,
        )

        issues = config.validate_configuration()
        assert any("DATADOG_LIMIT is very low" in issue for issue in issues)
        assert any("JIRA_SIMILARITY_THRESHOLD is very low" in issue for issue in issues)

    def test_config_logging(self):
        """Test configuration logging."""
        config = Config(
            openai_api_key="test-openai-key",
            datadog_api_key="test-datadog-api-key",
            datadog_app_key="test-datadog-app-key",
            jira_domain="test.atlassian.net",
            jira_user="test@example.com",
            jira_api_token="test-jira-token",
            jira_project_key="TEST",
        )

        # Should not raise an exception
        config.log_configuration()


class TestConfigEnvironment:
    """Test configuration loading from environment variables."""

    def test_config_from_env(self, temp_env):
        """Test configuration loading from environment variables."""
        # Set additional environment variables
        os.environ.update(
            {
                "DATADOG_LIMIT": "100",
                "JIRA_SIMILARITY_THRESHOLD": "0.75",
                "MAX_TITLE_LENGTH": "150",
                "LOG_LEVEL": "DEBUG",
            }
        )

        # Reload configuration
        config = reload_config()

        assert config.datadog_limit == 100
        assert config.jira_similarity_threshold == 0.75
        assert config.max_title_length == 150
        assert config.log_level == "DEBUG"

    def test_config_type_conversion(self, temp_env):
        """Test automatic type conversion from environment variables."""
        # Set string values that should be converted
        os.environ.update(
            {
                "DATADOG_LIMIT": "50",  # String to int
                "JIRA_SIMILARITY_THRESHOLD": "0.82",  # String to float
                "AUTO_CREATE_TICKET": "true",  # String to bool
                "MAX_TICKETS_PER_RUN": "5",  # String to int
            }
        )

        config = reload_config()

        assert isinstance(config.datadog_limit, int)
        assert config.datadog_limit == 50
        assert isinstance(config.jira_similarity_threshold, float)
        assert config.jira_similarity_threshold == 0.82
        assert isinstance(config.auto_create_ticket, bool)
        assert config.auto_create_ticket is True
        assert isinstance(config.max_tickets_per_run, int)
        assert config.max_tickets_per_run == 5


class TestConfigThreadSafety:
    """Test that the config singleton is thread-safe."""

    def test_get_config_returns_same_instance(self):
        """get_config() should always return the same instance."""
        import agent.config as cfg_module

        cfg_module._config = None  # Reset
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_get_config_concurrent_access(self):
        """Multiple threads calling get_config() should all get the same instance."""
        import threading
        import agent.config as cfg_module

        cfg_module._config = None  # Reset

        results: list = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()  # All threads start at the same time
            results.append(get_config())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 10 threads must have received the exact same object
        assert len(results) == 10
        assert all(r is results[0] for r in results)

    def test_reload_config_replaces_instance(self):
        """reload_config() should create a new instance."""
        import agent.config as cfg_module

        cfg_module._config = None  # Reset
        c1 = get_config()
        c2 = reload_config()
        assert c1 is not c2
        assert get_config() is c2
