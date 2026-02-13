"""Unit tests for agent.nodes.prompt_context.build_contextual_log."""

import pytest
from types import SimpleNamespace

from agent.nodes.prompt_context import build_contextual_log


def _make_config(service="vega-api", env="prod"):
    """Create a minimal config-like object."""
    return SimpleNamespace(datadog_service=service, datadog_env=env)


def _make_log_data(
    message="NullPointerException at line 42",
    logger="com.app.service.UserService",
    thread="http-nio-8080-exec-3",
    detail="User not found after registration",
):
    return {
        "message": message,
        "logger": logger,
        "thread": thread,
        "detail": detail,
    }


class TestBuildContextualLogBasic:
    """Test basic prompt construction with service, env, and log fields."""

    def test_includes_service_and_env(self):
        config = _make_config(service="payment-api", env="staging")
        result = build_contextual_log(_make_log_data(), {}, config)

        assert "[Service]: payment-api" in result
        assert "[Environment]: staging" in result

    def test_includes_logger_thread_message_detail(self):
        log_data = _make_log_data(
            logger="com.example.FooService",
            thread="worker-1",
            message="Something broke",
            detail="StackTrace here",
        )
        result = build_contextual_log(log_data, {}, _make_config())

        assert "[Logger]: com.example.FooService" in result
        assert "[Thread]: worker-1" in result
        assert "[Message]: Something broke" in result
        assert "[Detail]: StackTrace here" in result

    def test_field_order(self):
        result = build_contextual_log(_make_log_data(), {}, _make_config())
        lines = result.split("\n")

        assert lines[0].startswith("[Service]:")
        assert lines[1].startswith("[Environment]:")
        assert lines[2].startswith("[Logger]:")
        assert lines[3].startswith("[Thread]:")
        assert lines[4].startswith("[Message]:")
        assert lines[5].startswith("[Detail]:")

    def test_no_occurrences_line_without_fp_counts(self):
        result = build_contextual_log(_make_log_data(), {}, _make_config())

        assert "[Occurrences" not in result

    def test_no_severity_hints_without_rules(self):
        result = build_contextual_log(_make_log_data(), {}, _make_config())

        assert "[Severity hints]" not in result


class TestBuildContextualLogDefaults:
    """Test default values for missing or empty fields."""

    def test_missing_logger_gets_default(self):
        log_data = _make_log_data(logger=None)
        result = build_contextual_log(log_data, {}, _make_config())

        assert "[Logger]: unknown.logger" in result

    def test_empty_logger_gets_default(self):
        log_data = _make_log_data(logger="")
        result = build_contextual_log(log_data, {}, _make_config())

        assert "[Logger]: unknown.logger" in result

    def test_missing_thread_gets_default(self):
        log_data = _make_log_data(thread=None)
        result = build_contextual_log(log_data, {}, _make_config())

        assert "[Thread]: unknown.thread" in result

    def test_missing_message_gets_default(self):
        log_data = _make_log_data(message=None)
        result = build_contextual_log(log_data, {}, _make_config())

        assert "[Message]: <no message>" in result

    def test_missing_detail_gets_default(self):
        log_data = _make_log_data(detail=None)
        result = build_contextual_log(log_data, {}, _make_config())

        assert "[Detail]: <no detail>" in result

    def test_missing_service_gets_default(self):
        config = SimpleNamespace(datadog_service=None, datadog_env="prod")
        result = build_contextual_log(_make_log_data(), {}, config)

        assert "[Service]: unknown" in result

    def test_missing_env_gets_default(self):
        config = SimpleNamespace(datadog_service="api", datadog_env=None)
        result = build_contextual_log(_make_log_data(), {}, config)

        assert "[Environment]: unknown" in result

    def test_empty_log_data_dict(self):
        result = build_contextual_log({}, {}, _make_config())

        assert "[Logger]: unknown.logger" in result
        assert "[Thread]: unknown.thread" in result
        assert "[Message]: <no message>" in result
        assert "[Detail]: <no detail>" in result


class TestBuildContextualLogOccurrences:
    """Test occurrence count enrichment from fp_counts."""

    def test_includes_occurrences_when_fp_counts_present(self):
        log_data = _make_log_data(
            logger="com.app.UserService", message="User not found"
        )
        state = {
            "fp_counts": {"com.app.UserService|User not found": 47},
            "window_hours": 24,
        }
        result = build_contextual_log(log_data, state, _make_config())

        assert "[Occurrences in last 24h]: 47" in result

    def test_occurrences_default_to_1_when_key_missing(self):
        log_data = _make_log_data(
            logger="com.app.UserService", message="User not found"
        )
        state = {
            "fp_counts": {"other.logger|other message": 10},
            "window_hours": 48,
        }
        result = build_contextual_log(log_data, state, _make_config())

        assert "[Occurrences in last 48h]: 1" in result

    def test_occurrences_uses_question_mark_for_missing_window_hours(self):
        log_data = _make_log_data(
            logger="com.app.UserService", message="User not found"
        )
        state = {"fp_counts": {"com.app.UserService|User not found": 5}}
        result = build_contextual_log(log_data, state, _make_config())

        assert "[Occurrences in last ?h]: 5" in result

    def test_no_occurrences_when_fp_counts_is_none(self):
        state = {"fp_counts": None}
        result = build_contextual_log(_make_log_data(), state, _make_config())

        assert "[Occurrences" not in result


class TestBuildContextualLogSeverityHints:
    """Test team-specific severity hints enrichment."""

    def test_includes_severity_hints_when_rules_provided(self):
        rules = {"email-not-found": "low", "db-constraint": "high"}
        result = build_contextual_log(
            _make_log_data(), {}, _make_config(), team_severity_rules=rules
        )

        assert "[Severity hints]:" in result
        assert "db-constraint=high" in result
        assert "email-not-found=low" in result

    def test_severity_hints_sorted_alphabetically(self):
        rules = {"z-error": "low", "a-error": "high", "m-error": "medium"}
        result = build_contextual_log(
            _make_log_data(), {}, _make_config(), team_severity_rules=rules
        )

        hints_line = [
            l for l in result.split("\n") if l.startswith("[Severity hints]")
        ][0]
        assert "a-error=high, m-error=medium, z-error=low" in hints_line

    def test_no_severity_hints_when_rules_empty(self):
        result = build_contextual_log(
            _make_log_data(), {}, _make_config(), team_severity_rules={}
        )

        assert "[Severity hints]" not in result

    def test_no_severity_hints_when_rules_none(self):
        result = build_contextual_log(
            _make_log_data(), {}, _make_config(), team_severity_rules=None
        )

        assert "[Severity hints]" not in result


class TestBuildContextualLogFullEnrichment:
    """Test with all enrichment fields present (realistic multi-tenant scenario)."""

    def test_full_enrichment(self):
        config = _make_config(service="vega-api", env="prod")
        log_data = _make_log_data(
            logger="com.app.service.UserService",
            thread="http-nio-8080-exec-3",
            message="User not found after registration",
            detail="NullPointerException at line 42",
        )
        state = {
            "fp_counts": {
                "com.app.service.UserService|User not found after registration": 47
            },
            "window_hours": 24,
            "team_id": "team-vega",
        }
        rules = {"email-not-found": "low"}

        result = build_contextual_log(
            log_data, state, config, team_severity_rules=rules
        )

        lines = result.split("\n")
        assert len(lines) == 8  # 6 base + occurrences + severity hints
        assert lines[0] == "[Service]: vega-api"
        assert lines[1] == "[Environment]: prod"
        assert lines[6] == "[Occurrences in last 24h]: 47"
        assert lines[7] == "[Severity hints]: email-not-found=low"
