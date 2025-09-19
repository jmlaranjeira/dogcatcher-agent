"""Configuration management using Pydantic BaseSettings.

This module provides centralized configuration management with validation,
type safety, and sensible defaults for the dogcatcher-agent.
"""
from typing import Dict, List, Optional, Union
from pydantic import Field, validator
from pydantic_settings import BaseSettings
import json


class OpenAIConfig(BaseSettings):
    """OpenAI API configuration."""
    api_key: str = Field("", env="OPENAI_API_KEY", description="OpenAI API key")
    model: str = Field("gpt-4o-mini", env="OPENAI_MODEL", description="OpenAI model to use")
    temperature: float = Field(0.0, env="OPENAI_TEMPERATURE", ge=0.0, le=2.0, description="Model temperature")
    response_format: str = Field("json_object", env="OPENAI_RESPONSE_FORMAT", description="Response format")
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }
    
    @validator('response_format')
    def validate_response_format(cls, v):
        if v not in ['json_object', 'text']:
            raise ValueError('response_format must be "json_object" or "text"')
        return v


class DatadogConfig(BaseSettings):
    """Datadog API configuration."""
    api_key: str = Field("", env="DATADOG_API_KEY", description="Datadog API key")
    app_key: str = Field("", env="DATADOG_APP_KEY", description="Datadog application key")
    site: str = Field("datadoghq.eu", env="DATADOG_SITE", description="Datadog site")
    service: str = Field("dehnproject", env="DATADOG_SERVICE", description="Service name filter")
    env: str = Field("dev", env="DATADOG_ENV", description="Environment filter")
    hours_back: int = Field(24, env="DATADOG_HOURS_BACK", ge=1, le=168, description="Hours to look back")
    limit: int = Field(50, env="DATADOG_LIMIT", ge=1, le=1000, description="Logs per page")
    max_pages: int = Field(3, env="DATADOG_MAX_PAGES", ge=1, le=10, description="Max pages to fetch")
    timeout: int = Field(20, env="DATADOG_TIMEOUT", ge=5, le=60, description="Request timeout in seconds")
    statuses: str = Field("error", env="DATADOG_STATUSES", description="Comma-separated log statuses")
    query_extra: str = Field("", env="DATADOG_QUERY_EXTRA", description="Extra query terms")
    query_extra_mode: str = Field("AND", env="DATADOG_QUERY_EXTRA_MODE", description="Extra query mode")
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }
    
    @validator('query_extra_mode')
    def validate_query_extra_mode(cls, v):
        if v.upper() not in ['AND', 'OR']:
            raise ValueError('query_extra_mode must be "AND" or "OR"')
        return v.upper()
    
    @validator('statuses')
    def validate_statuses(cls, v):
        valid_statuses = ['error', 'critical', 'warning', 'info', 'debug']
        statuses = [s.strip().lower() for s in v.split(',')]
        for status in statuses:
            if status and status not in valid_statuses:
                raise ValueError(f'Invalid status: {status}. Valid options: {valid_statuses}')
        return v


class JiraConfig(BaseSettings):
    """Jira API configuration."""
    domain: str = Field("", env="JIRA_DOMAIN", description="Jira instance domain")
    user: str = Field("", env="JIRA_USER", description="Jira user email")
    api_token: str = Field("", env="JIRA_API_TOKEN", description="Jira API token")
    project_key: str = Field("", env="JIRA_PROJECT_KEY", description="Jira project key")
    
    # Jira search configuration
    search_max_results: int = Field(200, env="JIRA_SEARCH_MAX_RESULTS", ge=1, le=1000, description="Max results per search")
    search_window_days: int = Field(365, env="JIRA_SEARCH_WINDOW_DAYS", ge=1, le=365, description="Search window in days")
    
    # Similarity thresholds
    similarity_threshold: float = Field(0.82, env="JIRA_SIMILARITY_THRESHOLD", ge=0.0, le=1.0, description="Similarity threshold for duplicates")
    direct_log_threshold: float = Field(0.90, env="JIRA_DIRECT_LOG_THRESHOLD", ge=0.0, le=1.0, description="Direct log match threshold")
    partial_log_threshold: float = Field(0.70, env="JIRA_PARTIAL_LOG_THRESHOLD", ge=0.0, le=1.0, description="Partial log match threshold")
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }


class AgentConfig(BaseSettings):
    """Agent behavior configuration."""
    auto_create_ticket: bool = Field(False, env="AUTO_CREATE_TICKET", description="Create real tickets")
    persist_sim_fp: bool = Field(False, env="PERSIST_SIM_FP", description="Persist fingerprints in simulation")
    comment_on_duplicate: bool = Field(True, env="COMMENT_ON_DUPLICATE", description="Comment on duplicate tickets")
    max_tickets_per_run: int = Field(3, env="MAX_TICKETS_PER_RUN", ge=0, le=100, description="Max tickets per run (0=unlimited)")
    comment_cooldown_minutes: int = Field(120, env="COMMENT_COOLDOWN_MINUTES", ge=0, le=1440, description="Comment cooldown in minutes")
    
    # Severity rules (JSON string)
    severity_rules_json: str = Field("", env="SEVERITY_RULES_JSON", description="JSON mapping error_type to severity")
    
    # Aggregation settings
    aggregate_email_not_found: bool = Field(False, env="AGGREGATE_EMAIL_NOT_FOUND", description="Aggregate email-not-found errors")
    aggregate_kafka_consumer: bool = Field(False, env="AGGREGATE_KAFKA_CONSUMER", description="Aggregate kafka-consumer errors")
    
    # Occurrence-based escalation
    occ_escalate_enabled: bool = Field(False, env="OCC_ESCALATE_ENABLED", description="Enable occurrence-based escalation")
    occ_escalate_threshold: int = Field(10, env="OCC_ESCALATE_THRESHOLD", ge=1, le=1000, description="Escalation threshold")
    occ_escalate_to: str = Field("high", env="OCC_ESCALATE_TO", description="Target severity for escalation")
    
    @validator('occ_escalate_to')
    def validate_escalate_to(cls, v):
        if v.lower() not in ['low', 'medium', 'high']:
            raise ValueError('occ_escalate_to must be "low", "medium", or "high"')
        return v.lower()
    
    @validator('severity_rules_json')
    def validate_severity_rules(cls, v):
        if v.strip():
            try:
                rules = json.loads(v)
                if not isinstance(rules, dict):
                    raise ValueError('severity_rules_json must be a JSON object')
                for error_type, severity in rules.items():
                    if severity.lower() not in ['low', 'medium', 'high']:
                        raise ValueError(f'Invalid severity "{severity}" for error_type "{error_type}"')
            except json.JSONDecodeError as e:
                raise ValueError(f'Invalid JSON in severity_rules_json: {e}')
        return v
    
    def get_severity_rules(self) -> Dict[str, str]:
        """Parse and return severity rules as a dictionary."""
        if not self.severity_rules_json.strip():
            return {}
        return json.loads(self.severity_rules_json)


class LoggingConfig(BaseSettings):
    """Logging configuration."""
    level: str = Field("INFO", env="LOG_LEVEL", description="Logging level")
    format: str = Field("%(asctime)s - %(name)s - %(levelname)s - %(message)s", env="LOG_FORMAT", description="Log format")
    
    @validator('level')
    def validate_level(cls, v):
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f'Invalid log level: {v}. Valid options: {valid_levels}')
        return v.upper()


class UIConfig(BaseSettings):
    """UI and display configuration."""
    max_title_length: int = Field(120, env="MAX_TITLE_LENGTH", ge=10, le=255, description="Maximum ticket title length")
    max_description_preview: int = Field(160, env="MAX_DESCRIPTION_PREVIEW", ge=50, le=500, description="Max description preview length")
    max_json_output_length: int = Field(1000, env="MAX_JSON_OUTPUT_LENGTH", ge=100, le=10000, description="Max JSON output length")


class Config(BaseSettings):
    """Main configuration class combining all settings."""
    
    # OpenAI Configuration
    openai_api_key: str = Field("", env="OPENAI_API_KEY", description="OpenAI API key")
    openai_model: str = Field("gpt-4o-mini", env="OPENAI_MODEL", description="OpenAI model to use")
    openai_temperature: float = Field(0.0, env="OPENAI_TEMPERATURE", ge=0.0, le=2.0, description="Model temperature")
    openai_response_format: str = Field("json_object", env="OPENAI_RESPONSE_FORMAT", description="Response format")
    
    # Datadog Configuration
    datadog_api_key: str = Field("", env="DATADOG_API_KEY", description="Datadog API key")
    datadog_app_key: str = Field("", env="DATADOG_APP_KEY", description="Datadog application key")
    datadog_site: str = Field("datadoghq.eu", env="DATADOG_SITE", description="Datadog site")
    datadog_service: str = Field("dehnproject", env="DATADOG_SERVICE", description="Service name filter")
    datadog_env: str = Field("dev", env="DATADOG_ENV", description="Environment filter")
    datadog_hours_back: int = Field(24, env="DATADOG_HOURS_BACK", ge=1, le=168, description="Hours to look back")
    datadog_limit: int = Field(50, env="DATADOG_LIMIT", ge=1, le=1000, description="Logs per page")
    datadog_max_pages: int = Field(3, env="DATADOG_MAX_PAGES", ge=1, le=10, description="Max pages to fetch")
    datadog_timeout: int = Field(20, env="DATADOG_TIMEOUT", ge=5, le=60, description="Request timeout in seconds")
    datadog_statuses: str = Field("error", env="DATADOG_STATUSES", description="Comma-separated log statuses")
    datadog_query_extra: str = Field("", env="DATADOG_QUERY_EXTRA", description="Extra query terms")
    datadog_query_extra_mode: str = Field("AND", env="DATADOG_QUERY_EXTRA_MODE", description="Extra query mode")
    
    # Jira Configuration
    jira_domain: str = Field("", env="JIRA_DOMAIN", description="Jira instance domain")
    jira_user: str = Field("", env="JIRA_USER", description="Jira user email")
    jira_api_token: str = Field("", env="JIRA_API_TOKEN", description="Jira API token")
    jira_project_key: str = Field("", env="JIRA_PROJECT_KEY", description="Jira project key")
    jira_search_max_results: int = Field(200, env="JIRA_SEARCH_MAX_RESULTS", ge=1, le=1000, description="Max results per search")
    jira_search_window_days: int = Field(365, env="JIRA_SEARCH_WINDOW_DAYS", ge=1, le=365, description="Search window in days")
    jira_similarity_threshold: float = Field(0.82, env="JIRA_SIMILARITY_THRESHOLD", ge=0.0, le=1.0, description="Similarity threshold for duplicates")
    jira_direct_log_threshold: float = Field(0.90, env="JIRA_DIRECT_LOG_THRESHOLD", ge=0.0, le=1.0, description="Direct log match threshold")
    jira_partial_log_threshold: float = Field(0.70, env="JIRA_PARTIAL_LOG_THRESHOLD", ge=0.0, le=1.0, description="Partial log match threshold")
    
    # Agent Configuration
    auto_create_ticket: bool = Field(False, env="AUTO_CREATE_TICKET", description="Create real tickets")
    persist_sim_fp: bool = Field(False, env="PERSIST_SIM_FP", description="Persist fingerprints in simulation")
    comment_on_duplicate: bool = Field(True, env="COMMENT_ON_DUPLICATE", description="Comment on duplicate tickets")
    max_tickets_per_run: int = Field(3, env="MAX_TICKETS_PER_RUN", ge=0, le=100, description="Max tickets per run (0=unlimited)")
    comment_cooldown_minutes: int = Field(120, env="COMMENT_COOLDOWN_MINUTES", ge=0, le=1440, description="Comment cooldown in minutes")
    severity_rules_json: str = Field("", env="SEVERITY_RULES_JSON", description="JSON mapping error_type to severity")
    aggregate_email_not_found: bool = Field(False, env="AGGREGATE_EMAIL_NOT_FOUND", description="Aggregate email-not-found errors")
    aggregate_kafka_consumer: bool = Field(False, env="AGGREGATE_KAFKA_CONSUMER", description="Aggregate kafka-consumer errors")
    occ_escalate_enabled: bool = Field(False, env="OCC_ESCALATE_ENABLED", description="Enable occurrence-based escalation")
    occ_escalate_threshold: int = Field(10, env="OCC_ESCALATE_THRESHOLD", ge=1, le=1000, description="Escalation threshold")
    occ_escalate_to: str = Field("high", env="OCC_ESCALATE_TO", description="Target severity for escalation")
    
    # Logging Configuration
    log_level: str = Field("INFO", env="LOG_LEVEL", description="Logging level")
    log_format: str = Field("%(asctime)s - %(name)s - %(levelname)s - %(message)s", env="LOG_FORMAT", description="Log format")
    
    # UI Configuration
    max_title_length: int = Field(120, env="MAX_TITLE_LENGTH", ge=10, le=255, description="Maximum ticket title length")
    max_description_preview: int = Field(160, env="MAX_DESCRIPTION_PREVIEW", ge=50, le=500, description="Max description preview length")
    max_json_output_length: int = Field(1000, env="MAX_JSON_OUTPUT_LENGTH", ge=100, le=10000, description="Max JSON output length")

    # Cache Configuration (Phase 1.1 - Persistent Caching)
    cache_backend: str = Field("memory", env="CACHE_BACKEND", description="Cache backend type: redis, file, memory")
    cache_redis_url: str = Field("redis://localhost:6379", env="CACHE_REDIS_URL", description="Redis connection URL")
    cache_file_dir: str = Field(".agent_cache/persistent", env="CACHE_FILE_DIR", description="File cache directory")
    cache_ttl_seconds: int = Field(3600, env="CACHE_TTL_SECONDS", ge=60, le=86400, description="Cache TTL in seconds")
    cache_max_memory_size: int = Field(1000, env="CACHE_MAX_MEMORY_SIZE", ge=100, le=10000, description="Max memory cache entries")
    cache_similarity_ttl_seconds: int = Field(3600, env="CACHE_SIMILARITY_TTL_SECONDS", ge=300, le=86400, description="Similarity cache TTL")
    cache_max_file_size_mb: int = Field(100, env="CACHE_MAX_FILE_SIZE_MB", ge=10, le=1000, description="Max file cache size in MB")
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }
    
    def validate_configuration(self) -> List[str]:
        """Validate the complete configuration and return any issues."""
        issues = []
        
        # Check required fields
        if not self.openai_api_key:
            issues.append("OPENAI_API_KEY is required")
        if not self.datadog_api_key:
            issues.append("DATADOG_API_KEY is required")
        if not self.datadog_app_key:
            issues.append("DATADOG_APP_KEY is required")
        if not self.jira_domain:
            issues.append("JIRA_DOMAIN is required")
        if not self.jira_user:
            issues.append("JIRA_USER is required")
        if not self.jira_api_token:
            issues.append("JIRA_API_TOKEN is required")
        if not self.jira_project_key:
            issues.append("JIRA_PROJECT_KEY is required")
        
        # Check logical constraints
        if self.max_tickets_per_run == 0 and self.auto_create_ticket:
            issues.append("MAX_TICKETS_PER_RUN=0 with AUTO_CREATE_TICKET=true is dangerous")
        
        if self.datadog_limit < 2:
            issues.append("DATADOG_LIMIT is very low, may miss important logs")
        
        if self.jira_similarity_threshold < 0.5:
            issues.append("JIRA_SIMILARITY_THRESHOLD is very low, may create many false duplicates")

        # Cache configuration validation
        if self.cache_backend not in ["redis", "file", "memory"]:
            issues.append("CACHE_BACKEND must be one of: redis, file, memory")

        if self.cache_backend == "redis" and not self.cache_redis_url.startswith("redis://"):
            issues.append("CACHE_REDIS_URL must be a valid Redis URL (redis://...)")

        if self.cache_ttl_seconds < 60:
            issues.append("CACHE_TTL_SECONDS is too low, may cause frequent cache misses")

        return issues
    
    def log_configuration(self) -> None:
        """Log the current configuration (sanitized)."""
        from agent.utils.logger import log_info
        
        log_info("Configuration loaded",
                openai_model=self.openai_model,
                datadog_site=self.datadog_site,
                datadog_service=self.datadog_service,
                datadog_env=self.datadog_env,
                jira_project=self.jira_project_key,
                auto_create=self.auto_create_ticket,
                max_tickets=self.max_tickets_per_run,
                similarity_threshold=self.jira_similarity_threshold,
                cache_backend=self.cache_backend,
                cache_ttl_seconds=self.cache_ttl_seconds,
                log_level=self.log_level)


# Global configuration instance (lazy loading)
_config = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """Reload configuration from environment variables."""
    global _config
    _config = Config()
    return _config
