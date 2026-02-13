"""Unit tests for the JiraPayloadBuilder (agent/jira/payload.py).

These tests exercise the pure formatting logic in isolation — no Jira client,
no duplicate detection, no audit logging.  Only a lightweight mock config is
required.
"""

import pytest
from types import SimpleNamespace

from agent.jira.payload import JiraPayloadBuilder, TicketPayload


def _make_config(**overrides):
    """Build a minimal mock config for the payload builder."""
    defaults = {
        "jira_project_key": "TEST",
        "datadog_logs_url": "https://app.datadoghq.eu/logs",
        "datadog_service": "test-service",
        "aggregate_email_not_found": False,
        "aggregate_kafka_consumer": False,
        "max_title_length": 120,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_state(**overrides):
    """Build a minimal agent state dict."""
    defaults = {
        "log_data": {
            "logger": "com.example.service",
            "thread": "main",
            "message": "Database connection failed: Connection timeout",
            "timestamp": "2025-12-09T10:30:00Z",
            "detail": "Failed to connect to database after 30 seconds timeout",
        },
        "error_type": "database-connection",
        "severity": "medium",
        "window_hours": 48,
    }
    defaults.update(overrides)
    return defaults


# -----------------------------------------------------------------------
# TestBuildEnhancedDescription
# -----------------------------------------------------------------------


class TestBuildEnhancedDescription:
    """Tests for build_enhanced_description."""

    def test_basic_description_includes_log_context(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        result = builder.build_enhanced_description(state, "Some LLM description.")

        assert result.startswith("Some LLM description.")
        assert "Timestamp: 2025-12-09T10:30:00Z" in result
        assert "Logger: com.example.service" in result
        assert "Thread: main" in result
        assert "Occurrences in last 48h:" in result

    def test_description_sanitizes_messages(self):
        """Ensure PII in log messages is masked via sanitize_for_jira."""
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()
        state["log_data"]["message"] = "User john@example.com not found"

        result = builder.build_enhanced_description(state, "desc")

        assert "john@example.com" not in result
        assert "<email>" in result

    def test_description_with_mdc_fields(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()
        state["log_data"]["attributes"] = {
            "requestId": "req-123",
            "userId": "user-456",
            "errorType": "NullPointerException",
        }

        result = builder.build_enhanced_description(state, "desc")

        assert "Request ID: req-123" in result
        assert "User ID: user-456" in result
        assert "Error Type: NullPointerException" in result

    def test_description_without_mdc_fields(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        result = builder.build_enhanced_description(state, "desc")

        assert "Request ID:" not in result
        assert "User ID:" not in result

    def test_description_includes_datadog_links(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()
        state["log_data"]["attributes"] = {"requestId": "req-abc"}

        result = builder.build_enhanced_description(state, "desc")

        assert "Datadog Links:" in result
        assert "Request Trace:" in result
        assert "Similar Errors:" in result

    def test_occurrences_from_fp_counts(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()
        raw_msg = state["log_data"]["message"]
        logger = state["log_data"]["logger"]
        fp_source = f"{logger}|{raw_msg}"
        state["fp_counts"] = {fp_source: 42}

        result = builder.build_enhanced_description(state, "desc")

        assert "42" in result


# -----------------------------------------------------------------------
# TestBuildDatadogLinks
# -----------------------------------------------------------------------


class TestBuildDatadogLinks:
    """Tests for build_datadog_links."""

    def test_link_with_request_id(self):
        builder = JiraPayloadBuilder(_make_config())
        log_data = {"logger": "com.foo"}

        result = builder.build_datadog_links(log_data, "req-123", "")

        assert "Request Trace:" in result
        assert "requestId" in result

    def test_link_with_user_id(self):
        builder = JiraPayloadBuilder(_make_config())
        log_data = {"logger": "com.foo"}

        result = builder.build_datadog_links(log_data, "", "user-456")

        assert "User Activity:" in result
        assert "userId" in result

    def test_link_with_logger(self):
        builder = JiraPayloadBuilder(_make_config())
        log_data = {"logger": "com.example.MyService"}

        result = builder.build_datadog_links(log_data, "", "")

        assert "Similar Errors:" in result
        assert "logger_name" in result

    def test_no_links_when_empty(self):
        builder = JiraPayloadBuilder(_make_config())

        result = builder.build_datadog_links({}, "", "")

        assert result == ""

    def test_multiple_links(self):
        builder = JiraPayloadBuilder(_make_config())
        log_data = {"logger": "com.foo"}

        result = builder.build_datadog_links(log_data, "req-1", "user-2")

        lines = result.strip().split("\n")
        assert len(lines) == 3  # request trace + user activity + similar errors


# -----------------------------------------------------------------------
# TestBuildLabels
# -----------------------------------------------------------------------


class TestBuildLabels:
    """Tests for build_labels."""

    def test_base_labels(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        labels = builder.build_labels(state, "fp-abc")

        assert "datadog-log" in labels

    def test_includes_loghash(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        labels = builder.build_labels(state, "fp-abc")

        loghash_labels = [l for l in labels if l.startswith("loghash-")]
        assert len(loghash_labels) == 1

    def test_includes_error_type(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state(error_type="database-connection")

        labels = builder.build_labels(state, "fp-abc")

        assert "database-connection" in labels

    def test_excludes_unknown_error_type(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state(error_type="unknown")

        labels = builder.build_labels(state, "fp-abc")

        assert "unknown" not in labels

    def test_aggregation_email_not_found(self):
        builder = JiraPayloadBuilder(_make_config(aggregate_email_not_found=True))
        state = _make_state(error_type="email-not-found")

        labels = builder.build_labels(state, "fp-abc")

        assert "aggregate-email-not-found" in labels

    def test_aggregation_kafka_consumer(self):
        builder = JiraPayloadBuilder(_make_config(aggregate_kafka_consumer=True))
        state = _make_state(error_type="kafka-consumer")

        labels = builder.build_labels(state, "fp-abc")

        assert "aggregate-kafka-consumer" in labels

    def test_extra_labels(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        labels = builder.build_labels(state, "fp-abc", extra_labels=["async-created"])

        assert "async-created" in labels
        assert "datadog-log" in labels

    def test_no_aggregation_when_disabled(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state(error_type="email-not-found")

        labels = builder.build_labels(state, "fp-abc")

        assert "aggregate-email-not-found" not in labels


# -----------------------------------------------------------------------
# TestCleanTitle
# -----------------------------------------------------------------------


class TestCleanTitle:
    """Tests for clean_title."""

    def test_basic_prefix(self):
        builder = JiraPayloadBuilder(_make_config())

        result = builder.clean_title("My Error Title", "db-error")

        assert result == "[Datadog][db-error] My Error Title"

    def test_prefix_without_error_type(self):
        builder = JiraPayloadBuilder(_make_config())

        result = builder.clean_title("My Error Title", None)

        assert result == "[Datadog] My Error Title"

    def test_strips_markdown_bold(self):
        builder = JiraPayloadBuilder(_make_config())

        result = builder.clean_title("**Bold Title**", "err")

        assert "**" not in result
        assert "Bold Title" in result

    def test_truncation(self):
        builder = JiraPayloadBuilder(_make_config(max_title_length=30))
        long_title = "A" * 50

        result = builder.clean_title(long_title, None)

        # prefix is "[Datadog] ", so the base_title part gets truncated
        assert len(result) <= 30 + len("[Datadog] ")
        assert result.endswith("\u2026")

    def test_aggregation_email_not_found(self):
        builder = JiraPayloadBuilder(_make_config(aggregate_email_not_found=True))

        result = builder.clean_title("Original Title", "email-not-found")

        assert "aggregated" in result.lower()

    def test_aggregation_kafka_consumer(self):
        builder = JiraPayloadBuilder(_make_config(aggregate_kafka_consumer=True))

        result = builder.clean_title("Original Title", "kafka-consumer")

        assert "aggregated" in result.lower()


# -----------------------------------------------------------------------
# TestComputeFingerprint
# -----------------------------------------------------------------------


class TestComputeFingerprint:
    """Tests for JiraPayloadBuilder.compute_fingerprint (static method)."""

    def test_returns_12_char_hex(self):
        state = _make_state()

        fp = JiraPayloadBuilder.compute_fingerprint(state)

        assert len(fp) == 12
        assert all(c in "0123456789abcdef" for c in fp)

    def test_same_input_same_fingerprint(self):
        state = _make_state()

        fp1 = JiraPayloadBuilder.compute_fingerprint(state)
        fp2 = JiraPayloadBuilder.compute_fingerprint(state)

        assert fp1 == fp2

    def test_different_error_type_different_fingerprint(self):
        state1 = _make_state(error_type="db-error")
        state2 = _make_state(error_type="auth-error")

        fp1 = JiraPayloadBuilder.compute_fingerprint(state1)
        fp2 = JiraPayloadBuilder.compute_fingerprint(state2)

        assert fp1 != fp2

    def test_defaults_error_type_to_unknown(self):
        state = _make_state()
        state.pop("error_type", None)

        fp = JiraPayloadBuilder.compute_fingerprint(state)

        assert len(fp) == 12


# -----------------------------------------------------------------------
# TestBuild (full payload assembly)
# -----------------------------------------------------------------------


class TestBuild:
    """Tests for JiraPayloadBuilder.build (full payload)."""

    def test_payload_structure(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        result = builder.build(state, "Test Title", "Test Desc")

        assert isinstance(result, TicketPayload)
        fields = result.payload["fields"]
        assert fields["project"]["key"] == "TEST"
        assert fields["issuetype"]["name"] == "Bug"
        assert "datadog-log" in fields["labels"]
        assert result.title.startswith("[Datadog]")
        assert len(result.fingerprint) == 12

    def test_priority_mapping_medium(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state(severity="medium")

        result = builder.build(state, "Title", "Desc")

        assert result.payload["fields"]["priority"]["name"] == "Medium"

    def test_priority_mapping_high(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state(severity="high")

        result = builder.build(state, "Title", "Desc")

        assert result.payload["fields"]["priority"]["name"] == "High"

    def test_priority_mapping_low(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state(severity="low")

        result = builder.build(state, "Title", "Desc")

        assert result.payload["fields"]["priority"]["name"] == "Low"

    def test_extra_labels_passed_through(self):
        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        result = builder.build(state, "Title", "Desc", extra_labels=["async-created"])

        assert "async-created" in result.labels
        assert "async-created" in result.payload["fields"]["labels"]

    def test_team_field_injection_single_tenant(self):
        """Test team field from env vars (single-tenant)."""
        import os

        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()  # no team_id

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("JIRA_TEAM_FIELD_ID", "customfield_10100")
            mp.setenv("JIRA_TEAM_VALUE", "Platform")

            result = builder.build(state, "Title", "Desc")

        assert result.payload["fields"]["customfield_10100"] == [{"value": "Platform"}]

    def test_no_team_field_when_env_missing(self):
        """No team field injected when env vars are absent."""
        import os

        builder = JiraPayloadBuilder(_make_config())
        state = _make_state()

        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("JIRA_TEAM_FIELD_ID", raising=False)
            mp.delenv("JIRA_TEAM_VALUE", raising=False)

            result = builder.build(state, "Title", "Desc")

        # Only standard fields should be present
        for key in result.payload["fields"]:
            assert not key.startswith("customfield_")
