"""Unit tests for the unified duplicate detection system.

Tests each strategy in isolation plus the DuplicateDetector orchestrator.
"""

import pytest
from unittest.mock import patch, MagicMock

from agent.dedup.result import DuplicateCheckResult
from agent.dedup.detector import DuplicateDetector, build_default_strategies
from agent.dedup.strategies import (
    DedupStrategy,
    InMemorySeenLogs,
    FingerprintCache,
    LoghashLabelSearch,
    ErrorTypeLabelSearch,
    SimilaritySearch,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_log_data():
    return {
        "logger": "com.example.service",
        "thread": "main",
        "message": "Database connection failed: Connection timeout",
        "timestamp": "2025-12-09T10:30:00Z",
        "detail": "Failed to connect to database after 30 seconds timeout",
    }


@pytest.fixture
def sample_state(sample_log_data):
    return {
        "log_data": sample_log_data,
        "error_type": "database-connection",
        "ticket_title": "Database Connection Error",
        "ticket_description": "The app failed to connect to the database.",
        "seen_logs": set(),
        "created_fingerprints": set(),
    }


# ---------------------------------------------------------------------------
# DuplicateCheckResult
# ---------------------------------------------------------------------------


class TestDuplicateCheckResult:
    def test_not_duplicate_default(self):
        r = DuplicateCheckResult(is_duplicate=False)
        assert r.is_duplicate is False
        assert r.strategy_name is None
        assert r.existing_ticket_key is None
        assert r.similarity_score is None
        assert r.message is None

    def test_duplicate_with_all_fields(self):
        r = DuplicateCheckResult(
            is_duplicate=True,
            strategy_name="fingerprint_cache",
            existing_ticket_key="PROJ-123",
            similarity_score=0.95,
            message="Fingerprint match",
        )
        assert r.is_duplicate is True
        assert r.strategy_name == "fingerprint_cache"
        assert r.existing_ticket_key == "PROJ-123"
        assert r.similarity_score == 0.95


# ---------------------------------------------------------------------------
# Strategy 1 – InMemorySeenLogs
# ---------------------------------------------------------------------------


class TestInMemorySeenLogs:
    def test_name(self):
        assert InMemorySeenLogs().name == "in_memory_seen_logs"

    def test_new_log_not_duplicate(self, sample_log_data, sample_state):
        strategy = InMemorySeenLogs()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False

    def test_seen_log_is_duplicate(self, sample_log_data, sample_state):
        """After adding the log_key to seen_logs, the same log is a dup."""
        strategy = InMemorySeenLogs()
        # Simulate the log having been seen
        from agent.jira.utils import normalize_log_message

        raw_msg = sample_log_data["message"]
        norm_msg = normalize_log_message(raw_msg)
        log_key = f"{sample_log_data['logger']}|{norm_msg or raw_msg}"
        sample_state["seen_logs"].add(log_key)

        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is True
        assert result.strategy_name == "in_memory_seen_logs"

    def test_different_logger_not_duplicate(self, sample_log_data, sample_state):
        """Same message but different logger is NOT a dup."""
        strategy = InMemorySeenLogs()
        from agent.jira.utils import normalize_log_message

        raw_msg = sample_log_data["message"]
        norm_msg = normalize_log_message(raw_msg)
        log_key = f"other.logger|{norm_msg or raw_msg}"
        sample_state["seen_logs"].add(log_key)

        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False

    def test_empty_seen_logs(self, sample_log_data):
        """State without seen_logs key should not crash."""
        strategy = InMemorySeenLogs()
        state = {"log_data": sample_log_data}
        result = strategy.check(sample_log_data, state)
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# Strategy 2 – FingerprintCache
# ---------------------------------------------------------------------------


class TestFingerprintCache:
    def test_name(self):
        assert FingerprintCache().name == "fingerprint_cache"

    @patch("agent.dedup.strategies.load_processed_fingerprints", return_value=set())
    def test_new_fingerprint_not_duplicate(
        self, mock_load, sample_log_data, sample_state
    ):
        strategy = FingerprintCache()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False

    @patch("agent.dedup.strategies.load_processed_fingerprints")
    def test_fingerprint_in_persisted_cache(
        self, mock_load, sample_log_data, sample_state
    ):
        """If the fingerprint is already in the persisted cache, it's a dup."""
        from agent.jira.utils import compute_fingerprint

        fp = compute_fingerprint(
            sample_state["error_type"],
            sample_log_data["message"],
        )
        mock_load.return_value = {fp}

        strategy = FingerprintCache()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is True
        assert result.strategy_name == "fingerprint_cache"

    @patch("agent.dedup.strategies.load_processed_fingerprints", return_value=set())
    def test_fingerprint_in_run_set(self, mock_load, sample_log_data, sample_state):
        """If the fingerprint was created in this run, it's a dup."""
        from agent.jira.utils import compute_fingerprint

        fp = compute_fingerprint(
            sample_state["error_type"],
            sample_log_data["message"],
        )
        sample_state["created_fingerprints"] = {fp}

        strategy = FingerprintCache()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is True


# ---------------------------------------------------------------------------
# Strategy 3 – LoghashLabelSearch
# ---------------------------------------------------------------------------


class TestLoghashLabelSearch:
    def test_name(self):
        assert LoghashLabelSearch().name == "loghash_label_search"

    @patch("agent.dedup.strategies.jira_client")
    @patch("agent.dedup.strategies.get_config")
    def test_loghash_match_found(
        self, mock_get_config, mock_jira_client, sample_log_data, sample_state
    ):
        """When Jira returns an issue with matching loghash label."""
        config = MagicMock()
        config.jira_project_key = "TEST"
        mock_get_config.return_value = config
        mock_jira_client.is_configured.return_value = True
        mock_jira_client.search.return_value = {
            "issues": [
                {
                    "key": "TEST-100",
                    "fields": {"summary": "DB timeout error"},
                }
            ]
        }

        strategy = LoghashLabelSearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is True
        assert result.strategy_name == "loghash_label_search"
        assert result.existing_ticket_key == "TEST-100"
        assert result.similarity_score == 1.0

    @patch("agent.dedup.strategies.jira_client")
    @patch("agent.dedup.strategies.get_config")
    def test_loghash_no_match(
        self, mock_get_config, mock_jira_client, sample_log_data, sample_state
    ):
        """When Jira returns no matching issues."""
        config = MagicMock()
        config.jira_project_key = "TEST"
        mock_get_config.return_value = config
        mock_jira_client.is_configured.return_value = True
        mock_jira_client.search.return_value = {"issues": []}

        strategy = LoghashLabelSearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False

    @patch("agent.dedup.strategies.jira_client")
    def test_jira_not_configured(self, mock_jira_client, sample_log_data, sample_state):
        """When Jira client is not configured, skip gracefully."""
        mock_jira_client.is_configured.return_value = False

        strategy = LoghashLabelSearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False

    def test_empty_message_skipped(self, sample_state):
        """Empty message should not trigger a Jira search."""
        log_data = {"logger": "test", "message": ""}
        strategy = LoghashLabelSearch()
        result = strategy.check(log_data, sample_state)
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# Strategy 4 – ErrorTypeLabelSearch
# ---------------------------------------------------------------------------


class TestErrorTypeLabelSearch:
    def test_name(self):
        assert ErrorTypeLabelSearch().name == "error_type_label_search"

    def test_unknown_error_type_skipped(self, sample_log_data):
        """If error_type is 'unknown' or empty, skip the search."""
        strategy = ErrorTypeLabelSearch()
        state = {"error_type": "unknown", "log_data": sample_log_data}
        result = strategy.check(sample_log_data, state)
        assert result.is_duplicate is False

        state2 = {"error_type": "", "log_data": sample_log_data}
        result2 = strategy.check(sample_log_data, state2)
        assert result2.is_duplicate is False

    @patch("agent.dedup.strategies.save_processed_fingerprints")
    @patch("agent.dedup.strategies.load_processed_fingerprints", return_value=set())
    @patch("agent.dedup.strategies.jira_search")
    @patch("agent.dedup.strategies.get_config")
    def test_error_type_match_found(
        self,
        mock_get_config,
        mock_search,
        mock_load_fp,
        mock_save_fp,
        sample_log_data,
        sample_state,
    ):
        config = MagicMock()
        config.jira_project_key = "TEST"
        mock_get_config.return_value = config
        mock_search.return_value = {
            "issues": [
                {
                    "key": "TEST-200",
                    "fields": {"summary": "Database connection error (aggregated)"},
                }
            ]
        }

        strategy = ErrorTypeLabelSearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is True
        assert result.strategy_name == "error_type_label_search"
        assert result.existing_ticket_key == "TEST-200"
        assert result.similarity_score == 0.95
        # Should have updated the fingerprint cache
        mock_save_fp.assert_called_once()

    @patch("agent.dedup.strategies.jira_search")
    @patch("agent.dedup.strategies.get_config")
    def test_error_type_no_match(
        self, mock_get_config, mock_search, sample_log_data, sample_state
    ):
        config = MagicMock()
        config.jira_project_key = "TEST"
        mock_get_config.return_value = config
        mock_search.return_value = {"issues": []}

        strategy = ErrorTypeLabelSearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# Strategy 5 – SimilaritySearch
# ---------------------------------------------------------------------------


class TestSimilaritySearch:
    def test_name(self):
        assert SimilaritySearch().name == "similarity_search"

    def test_no_title_skipped(self, sample_log_data):
        """If ticket_title is missing, skip search."""
        strategy = SimilaritySearch()
        state = {"log_data": sample_log_data}
        result = strategy.check(sample_log_data, state)
        assert result.is_duplicate is False

    @patch("agent.dedup.strategies.save_processed_fingerprints")
    @patch("agent.dedup.strategies.load_processed_fingerprints", return_value=set())
    @patch("agent.dedup.strategies.find_similar_ticket")
    def test_similar_ticket_found(
        self, mock_find, mock_load_fp, mock_save_fp, sample_log_data, sample_state
    ):
        mock_find.return_value = ("TEST-300", 0.88, "DB Timeout Error")

        strategy = SimilaritySearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is True
        assert result.strategy_name == "similarity_search"
        assert result.existing_ticket_key == "TEST-300"
        assert result.similarity_score == 0.88
        mock_save_fp.assert_called_once()

    @patch("agent.dedup.strategies.find_similar_ticket")
    def test_no_similar_ticket(self, mock_find, sample_log_data, sample_state):
        mock_find.return_value = (None, 0.0, None)

        strategy = SimilaritySearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False

    @patch(
        "agent.dedup.strategies.find_similar_ticket", side_effect=Exception("API error")
    )
    def test_exception_returns_not_duplicate(
        self, mock_find, sample_log_data, sample_state
    ):
        """Exceptions in similarity search should not crash; return not-dup."""
        strategy = SimilaritySearch()
        result = strategy.check(sample_log_data, sample_state)
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# DuplicateDetector (orchestrator)
# ---------------------------------------------------------------------------


class TestDuplicateDetector:
    def test_default_strategies_count(self):
        strategies = build_default_strategies()
        assert len(strategies) == 5
        assert strategies[0].name == "in_memory_seen_logs"
        assert strategies[1].name == "fingerprint_cache"
        assert strategies[2].name == "loghash_label_search"
        assert strategies[3].name == "error_type_label_search"
        assert strategies[4].name == "similarity_search"

    def test_short_circuits_on_first_hit(self, sample_log_data, sample_state):
        """Detector should stop at the first strategy that finds a duplicate."""
        # Create mock strategies
        s1 = MagicMock(spec=DedupStrategy)
        s1.check.return_value = DuplicateCheckResult(is_duplicate=False)

        s2 = MagicMock(spec=DedupStrategy)
        s2.check.return_value = DuplicateCheckResult(
            is_duplicate=True,
            strategy_name="mock_s2",
            message="Found by s2",
        )

        s3 = MagicMock(spec=DedupStrategy)  # should never be called

        detector = DuplicateDetector(strategies=[s1, s2, s3])
        result = detector.check(sample_log_data, sample_state)

        assert result.is_duplicate is True
        assert result.strategy_name == "mock_s2"
        s1.check.assert_called_once()
        s2.check.assert_called_once()
        s3.check.assert_not_called()

    def test_all_pass_returns_not_duplicate(self, sample_log_data, sample_state):
        """If no strategy finds a duplicate, return not-duplicate."""
        s1 = MagicMock(spec=DedupStrategy)
        s1.check.return_value = DuplicateCheckResult(is_duplicate=False)

        s2 = MagicMock(spec=DedupStrategy)
        s2.check.return_value = DuplicateCheckResult(is_duplicate=False)

        detector = DuplicateDetector(strategies=[s1, s2])
        result = detector.check(sample_log_data, sample_state)

        assert result.is_duplicate is False
        s1.check.assert_called_once()
        s2.check.assert_called_once()

    def test_empty_strategies_returns_not_duplicate(
        self, sample_log_data, sample_state
    ):
        """Detector with no strategies should return not-duplicate."""
        detector = DuplicateDetector(strategies=[])
        result = detector.check(sample_log_data, sample_state)
        assert result.is_duplicate is False

    def test_custom_strategies(self, sample_log_data, sample_state):
        """Detector accepts custom strategy lists."""
        detector = DuplicateDetector(strategies=[InMemorySeenLogs()])
        assert len(detector.strategies) == 1
        result = detector.check(sample_log_data, sample_state)
        assert result.is_duplicate is False
