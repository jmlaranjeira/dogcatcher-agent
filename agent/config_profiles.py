"""
Profile-based configuration loader for Dogcatcher Agent.

Supports YAML profiles with the following precedence:
1. Base config (.env)
2. Profile YAML overrides
3. Environment variable overrides
4. CLI argument overrides
"""

from pathlib import Path
from typing import Any, Dict, Optional
import yaml

from agent.utils.logger import log_info, log_warning, log_error

PROFILES_DIR = Path(__file__).parent.parent / "config" / "profiles"
VALID_PROFILES = {"development", "staging", "production", "testing"}


def load_profile(profile_name: str) -> Dict[str, Any]:
    """
    Load configuration profile from YAML file.

    Args:
        profile_name: Name of the profile (development, staging, production, testing)

    Returns:
        Dictionary with profile configuration

    Raises:
        ValueError: If profile name is invalid
        FileNotFoundError: If profile file doesn't exist
    """
    if profile_name not in VALID_PROFILES:
        raise ValueError(
            f"Invalid profile '{profile_name}'. "
            f"Valid profiles: {', '.join(sorted(VALID_PROFILES))}"
        )

    profile_path = PROFILES_DIR / f"{profile_name}.yaml"

    if not profile_path.exists():
        raise FileNotFoundError(f"Profile file not found: {profile_path}")

    with open(profile_path, "r") as f:
        config = yaml.safe_load(f) or {}

    log_info(
        f"Loaded configuration profile", profile=profile_name, path=str(profile_path)
    )
    return config


def get_available_profiles() -> list[str]:
    """Return list of available profile names."""
    profiles = []
    if PROFILES_DIR.exists():
        for path in PROFILES_DIR.glob("*.yaml"):
            profiles.append(path.stem)
    return sorted(profiles)


def apply_profile_to_config(config: "Config", profile_config: Dict[str, Any]) -> None:
    """
    Apply profile configuration overrides to Config object.

    Args:
        config: The Config object to modify
        profile_config: Dictionary from profile YAML
    """
    # Apply each section
    if "datadog" in profile_config:
        _apply_datadog_overrides(config, profile_config["datadog"])
    if "jira" in profile_config:
        _apply_jira_overrides(config, profile_config["jira"])
    if "agent" in profile_config:
        _apply_agent_overrides(config, profile_config["agent"])
    if "cache" in profile_config:
        _apply_cache_overrides(config, profile_config["cache"])
    if "circuit_breaker" in profile_config:
        _apply_circuit_breaker_overrides(config, profile_config["circuit_breaker"])
    if "logging" in profile_config:
        _apply_logging_overrides(config, profile_config["logging"])


def _apply_datadog_overrides(config, overrides: Dict[str, Any]) -> None:
    """Apply Datadog configuration overrides."""
    field_mapping = {
        "limit": "datadog_limit",
        "hours_back": "datadog_hours_back",
        "timeout": "datadog_timeout",
    }
    for yaml_key, attr in field_mapping.items():
        if yaml_key in overrides and hasattr(config, attr):
            setattr(config, attr, overrides[yaml_key])


def _apply_jira_overrides(config, overrides: Dict[str, Any]) -> None:
    """Apply Jira configuration overrides."""
    field_mapping = {
        "similarity_threshold": "jira_similarity_threshold",
        "search_window_days": "jira_search_window_days",
        "search_max_results": "jira_search_max_results",
    }
    for yaml_key, attr in field_mapping.items():
        if yaml_key in overrides and hasattr(config, attr):
            setattr(config, attr, overrides[yaml_key])


def _apply_agent_overrides(config, overrides: Dict[str, Any]) -> None:
    """Apply Agent configuration overrides."""
    field_mapping = {
        "max_tickets_per_run": "max_tickets_per_run",
        "auto_create_ticket": "auto_create_ticket",
    }
    for yaml_key, attr in field_mapping.items():
        if yaml_key in overrides and hasattr(config, attr):
            setattr(config, attr, overrides[yaml_key])


def _apply_cache_overrides(config, overrides: Dict[str, Any]) -> None:
    """Apply cache configuration overrides."""
    if "backend" in overrides:
        config.cache_backend = overrides["backend"]
    if "ttl_seconds" in overrides:
        config.cache_ttl_seconds = overrides["ttl_seconds"]


def _apply_circuit_breaker_overrides(config, overrides: Dict[str, Any]) -> None:
    """Apply circuit breaker configuration overrides."""
    field_mapping = {
        "enabled": "circuit_breaker_enabled",
        "failure_threshold": "circuit_breaker_failure_threshold",
        "timeout_seconds": "circuit_breaker_timeout_seconds",
    }
    for yaml_key, attr in field_mapping.items():
        if yaml_key in overrides and hasattr(config, attr):
            setattr(config, attr, overrides[yaml_key])


def _apply_logging_overrides(config, overrides: Dict[str, Any]) -> None:
    """Apply logging configuration overrides."""
    if "level" in overrides and hasattr(config, "log_level"):
        config.log_level = overrides["level"]
