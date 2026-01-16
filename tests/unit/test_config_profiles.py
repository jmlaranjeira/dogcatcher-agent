"""Tests for configuration profiles system."""
import pytest
from pathlib import Path
import tempfile
import yaml

from agent.config_profiles import (
    load_profile,
    get_available_profiles,
    apply_profile_to_config,
    VALID_PROFILES,
)
from agent.config import Config


class TestLoadProfile:
    """Tests for load_profile function."""

    def test_load_development_profile(self):
        """Test loading development profile."""
        config = load_profile("development")
        assert isinstance(config, dict)
        assert "datadog" in config
        assert config["agent"]["auto_create_ticket"] is False

    def test_load_production_profile(self):
        """Test loading production profile."""
        config = load_profile("production")
        assert config["agent"]["auto_create_ticket"] is True
        assert config["cache"]["backend"] == "redis"

    def test_load_staging_profile(self):
        """Test loading staging profile."""
        config = load_profile("staging")
        assert isinstance(config, dict)
        assert config["agent"]["max_tickets_per_run"] == 3
        assert config["jira"]["similarity_threshold"] == 0.80

    def test_load_testing_profile(self):
        """Test loading testing profile."""
        config = load_profile("testing")
        assert isinstance(config, dict)
        assert config["cache"]["backend"] == "memory"
        assert config["circuit_breaker"]["enabled"] is False

    def test_invalid_profile_raises_error(self):
        """Test that invalid profile name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid profile"):
            load_profile("nonexistent")

    def test_all_valid_profiles_load(self):
        """Test that all valid profiles can be loaded."""
        for profile_name in VALID_PROFILES:
            config = load_profile(profile_name)
            assert isinstance(config, dict)


class TestGetAvailableProfiles:
    """Tests for get_available_profiles function."""

    def test_returns_list(self):
        """Test that function returns a list."""
        profiles = get_available_profiles()
        assert isinstance(profiles, list)

    def test_includes_standard_profiles(self):
        """Test that standard profiles are available."""
        profiles = get_available_profiles()
        assert "development" in profiles
        assert "production" in profiles
        assert "staging" in profiles
        assert "testing" in profiles


class TestApplyProfileToConfig:
    """Tests for apply_profile_to_config function."""

    def test_applies_datadog_overrides(self):
        """Test that Datadog settings are applied."""
        config = Config()
        profile_config = {
            "datadog": {
                "limit": 999,
                "hours_back": 99,
            }
        }
        apply_profile_to_config(config, profile_config)
        assert config.datadog_limit == 999
        assert config.datadog_hours_back == 99

    def test_applies_jira_overrides(self):
        """Test that Jira settings are applied."""
        config = Config()
        profile_config = {
            "jira": {
                "similarity_threshold": 0.95,
                "search_window_days": 180,
            }
        }
        apply_profile_to_config(config, profile_config)
        assert config.jira_similarity_threshold == 0.95
        assert config.jira_search_window_days == 180

    def test_applies_agent_overrides(self):
        """Test that Agent settings are applied."""
        config = Config()
        profile_config = {
            "agent": {
                "auto_create_ticket": True,
                "max_tickets_per_run": 10,
            }
        }
        apply_profile_to_config(config, profile_config)
        assert config.auto_create_ticket is True
        assert config.max_tickets_per_run == 10

    def test_applies_cache_overrides(self):
        """Test that cache settings are applied."""
        config = Config()
        profile_config = {
            "cache": {
                "backend": "redis",
                "ttl_seconds": 7200,
            }
        }
        apply_profile_to_config(config, profile_config)
        assert config.cache_backend == "redis"
        assert config.cache_ttl_seconds == 7200

    def test_applies_circuit_breaker_overrides(self):
        """Test that circuit breaker settings are applied."""
        config = Config()
        profile_config = {
            "circuit_breaker": {
                "enabled": False,
                "failure_threshold": 10,
                "timeout_seconds": 120,
            }
        }
        apply_profile_to_config(config, profile_config)
        assert config.circuit_breaker_enabled is False
        assert config.circuit_breaker_failure_threshold == 10
        assert config.circuit_breaker_timeout_seconds == 120

    def test_applies_logging_overrides(self):
        """Test that logging settings are applied."""
        config = Config()
        profile_config = {
            "logging": {
                "level": "WARNING",
            }
        }
        apply_profile_to_config(config, profile_config)
        assert config.log_level == "WARNING"

    def test_empty_profile_config_does_not_crash(self):
        """Test that empty profile config doesn't cause errors."""
        config = Config()
        original_limit = config.datadog_limit
        apply_profile_to_config(config, {})
        assert config.datadog_limit == original_limit

    def test_partial_profile_config(self):
        """Test that partial profile config works correctly."""
        config = Config()
        profile_config = {
            "datadog": {
                "limit": 25,
            }
            # Missing other sections
        }
        apply_profile_to_config(config, profile_config)
        assert config.datadog_limit == 25


class TestConfigLoadProfileOverrides:
    """Tests for Config.load_profile_overrides method."""

    def test_load_profile_overrides_sets_profile_field(self):
        """Test that loading profile sets the profile field."""
        config = Config()
        assert config.profile is None
        config.load_profile_overrides("development")
        assert config.profile == "development"

    def test_load_profile_overrides_applies_settings(self):
        """Test that profile overrides are applied."""
        config = Config()
        config.load_profile_overrides("production")
        assert config.auto_create_ticket is True
        assert config.cache_backend == "redis"

    def test_load_profile_overrides_invalid_profile(self):
        """Test that invalid profile raises ValueError."""
        config = Config()
        with pytest.raises(ValueError):
            config.load_profile_overrides("invalid_profile")


class TestProfileYAMLStructure:
    """Tests for YAML profile file structure."""

    def test_all_profiles_have_required_sections(self):
        """Test that all profiles have required configuration sections."""
        required_sections = ["datadog", "jira", "agent", "cache", "circuit_breaker", "logging"]

        for profile_name in VALID_PROFILES:
            profile_config = load_profile(profile_name)

            for section in required_sections:
                assert section in profile_config, \
                    f"Profile '{profile_name}' missing required section: {section}"

    def test_development_profile_is_safe(self):
        """Test that development profile has safe defaults."""
        config = load_profile("development")
        assert config["agent"]["auto_create_ticket"] is False
        assert config["agent"]["max_tickets_per_run"] <= 1
        assert config["logging"]["level"] == "DEBUG"

    def test_production_profile_is_optimized(self):
        """Test that production profile has production-ready settings."""
        config = load_profile("production")
        assert config["agent"]["auto_create_ticket"] is True
        assert config["cache"]["backend"] == "redis"
        assert config["jira"]["similarity_threshold"] >= 0.8


@pytest.mark.integration
class TestEndToEndProfileLoading:
    """End-to-end tests for profile loading."""

    def test_config_with_development_profile(self):
        """Test loading Config with development profile."""
        config = Config()
        config.load_profile_overrides("development")

        assert config.profile == "development"
        assert config.auto_create_ticket is False
        assert config.datadog_limit == 10
        assert config.datadog_hours_back == 2

    def test_config_with_production_profile(self):
        """Test loading Config with production profile."""
        config = Config()
        config.load_profile_overrides("production")

        assert config.profile == "production"
        assert config.auto_create_ticket is True
        assert config.datadog_limit == 100
        assert config.cache_backend == "redis"
