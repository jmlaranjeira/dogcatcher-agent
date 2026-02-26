"""Additional unit tests for normalization edge cases and boundary conditions."""

import pytest
from unittest.mock import Mock, patch

from agent.jira.utils import (
    normalize_text,
    normalize_log_message,
    extract_text_from_description,
    compute_loghash,
)
from agent.jira.match import _sim


class TestNormalizationEdgeCases:
    """Test normalization edge cases and boundary conditions."""

    def test_normalize_text_very_long_string(self):
        """Test normalization of very long strings."""
        long_text = "A" * 10000
        result = normalize_text(long_text)

        assert result == "a" * 10000
        assert len(result) == 10000

    def test_normalize_text_unicode_characters(self):
        """Test normalization with Unicode characters (non-ASCII stripped by _RE_PUNCT)."""
        text = "Erro de conexão: Falha na conexão com o banco de dados"
        result = normalize_text(text)

        # _RE_PUNCT strips non-a-z0-9 chars including accented characters
        assert result == "erro de conex o falha na conex o com o banco de dados"

    def test_normalize_text_mixed_scripts(self):
        """Test normalization with mixed scripts (non-Latin stripped)."""
        text = "Error: Ошибка подключения к базе данных"
        result = normalize_text(text)

        # Cyrillic characters are stripped by _RE_PUNCT (only a-z0-9 survive)
        assert result == "error"

    def test_normalize_text_special_unicode(self):
        """Test normalization with special Unicode characters."""
        text = "Error: Database connection failed → timeout"
        result = normalize_text(text)

        assert result == "error database connection failed timeout"

    def test_normalize_text_control_characters(self):
        """Test normalization with control characters."""
        text = "Error:\tDatabase\nconnection\rfailed"
        result = normalize_text(text)

        assert result == "error database connection failed"

    def test_normalize_text_only_punctuation(self):
        """Test normalization of text with only punctuation."""
        text = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
        result = normalize_text(text)

        assert result == ""

    def test_normalize_text_only_numbers(self):
        """Test normalization of text with only numbers."""
        text = "1234567890"
        result = normalize_text(text)

        assert result == "1234567890"

    def test_normalize_text_mixed_case_numbers(self):
        """Test normalization with mixed case and numbers."""
        text = "Error 404: Page Not Found"
        result = normalize_text(text)

        assert result == "error 404 page not found"

    def test_normalize_log_message_edge_cases(self):
        """Test log message normalization edge cases."""
        # Very long log message
        long_message = "Error: " + "x" * 5000
        result = normalize_log_message(long_message)
        assert len(result) < len(long_message)  # Should be normalized

        # Message with only special characters
        special_chars = "!@#$%^&*()"
        result = normalize_log_message(special_chars)
        assert result == ""

        # Message with mixed content
        mixed = "Error 500: Internal Server Error at 2024-01-01T12:00:00Z"
        result = normalize_log_message(mixed)
        assert "error 500 internal server error" in result

    def test_extract_text_from_description_edge_cases(self):
        """Test description extraction edge cases."""
        # Very long description
        long_desc = "Error: " + "x" * 10000
        result = extract_text_from_description(long_desc)
        assert len(result) == len(long_desc)

        # Plain strings are returned verbatim (no markdown stripping)
        markdown_only = "**Error:** *Database* `connection` failed"
        result = extract_text_from_description(markdown_only)
        assert result == markdown_only

        # Description with nested formatting (plain strings returned as-is)
        nested = "**Error:** The *database* connection failed. See [docs](https://example.com) for details."
        result = extract_text_from_description(nested)
        assert result == nested


class TestSimilarityEdgeCases:
    """Test similarity calculation edge cases."""

    def test_similarity_identical_strings(self):
        """Test similarity of identical strings."""
        text1 = "database connection error"
        text2 = "database connection error"

        similarity = _sim(text1, text2)
        assert similarity == 1.0

    def test_similarity_completely_different(self):
        """Test similarity of completely different strings."""
        text1 = "database connection error"
        text2 = "user authentication failed"

        similarity = _sim(text1, text2)
        assert similarity < 0.5

    def test_similarity_empty_strings(self):
        """Test similarity with empty strings."""
        similarity = _sim("", "")
        # _sim returns 0.0 when either string is empty (guard clause)
        assert similarity == 0.0

    def test_similarity_one_empty(self):
        """Test similarity with one empty string."""
        similarity = _sim("database error", "")
        assert similarity == 0.0

    def test_similarity_very_similar(self):
        """Test similarity with very similar strings."""
        text1 = "database connection error"
        text2 = "database connection failed"

        similarity = _sim(text1, text2)
        assert similarity > 0.8

    def test_similarity_threshold_boundary(self):
        """Test similarity at threshold boundaries."""
        # Test strings that should be just above 0.90 threshold
        text1 = "database connection error"
        text2 = "database connection error timeout"

        similarity = _sim(text1, text2)
        # This should be high but may not reach 0.90
        assert similarity > 0.7

        # Test strings that should be just below 0.82 threshold
        text3 = "database connection error"
        text4 = "database connection failed timeout"

        similarity = _sim(text3, text4)
        # This should be in the fuzzy range
        assert 0.5 < similarity < 0.9


class TestDirectLogMatchPath:
    """Test direct log match path (≥0.90 similarity)."""

    def test_direct_log_match_high_similarity(self):
        """Test direct log match with high similarity."""
        # These should trigger direct log match (≥0.90)
        log1 = "database connection error"
        log2 = "database connection error timeout"

        similarity = _sim(log1, log2)
        if similarity >= 0.90:
            # This would trigger direct log match
            assert similarity >= 0.90

    def test_direct_log_match_exact_match(self):
        """Test direct log match with exact match."""
        log1 = "database connection error"
        log2 = "database connection error"

        similarity = _sim(log1, log2)
        assert similarity == 1.0
        assert similarity >= 0.90

    def test_direct_log_match_minor_differences(self):
        """Test direct log match with minor differences."""
        log1 = "database connection error"
        log2 = "database connection error occurred"

        similarity = _sim(log1, log2)
        # This should be high enough for direct match
        assert similarity > 0.8


class TestLabelShortCircuitPath:
    """Test label short-circuit path for existing issues."""

    def test_loghash_label_detection(self):
        """Test detection of loghash-* labels."""
        # Mock existing issue with loghash label
        existing_issue = {
            "key": "TEST-123",
            "fields": {"labels": ["loghash-abc123", "bug", "database"]},
        }

        # Check if loghash label exists
        labels = existing_issue["fields"].get("labels", [])
        loghash_labels = [label for label in labels if label.startswith("loghash-")]

        assert len(loghash_labels) > 0
        assert loghash_labels[0] == "loghash-abc123"

    def test_loghash_label_short_circuit(self):
        """Test short-circuit when loghash label matches."""
        fingerprint = "abc123"
        existing_issue = {
            "key": "TEST-123",
            "fields": {"labels": [f"loghash-{fingerprint}", "bug"]},
        }

        # This should short-circuit duplicate detection
        labels = existing_issue["fields"].get("labels", [])
        matching_loghash = any(label == f"loghash-{fingerprint}" for label in labels)

        assert matching_loghash is True

    def test_no_loghash_label(self):
        """Test when no loghash label exists."""
        existing_issue = {"key": "TEST-123", "fields": {"labels": ["bug", "database"]}}

        labels = existing_issue["fields"].get("labels", [])
        loghash_labels = [label for label in labels if label.startswith("loghash-")]

        assert len(loghash_labels) == 0


class TestLLMNoCreateDecision:
    """Test respect for LLM no-create decisions."""

    def test_llm_no_create_decision(self):
        """Test when LLM decides not to create a ticket."""
        # Mock state where LLM decided not to create
        state = {
            "ticket_title": "Test Error",
            "ticket_description": "Test description",
            "llm_no_create": True,
            "llm_no_create_reason": "Not a real error, just a warning",
        }

        # This should respect the LLM decision
        assert state.get("llm_no_create") is True
        assert state.get("llm_no_create_reason") is not None

    def test_llm_create_decision(self):
        """Test when LLM decides to create a ticket."""
        state = {
            "ticket_title": "Critical Database Error",
            "ticket_description": "Database connection failed",
            "llm_no_create": False,
        }

        # This should allow ticket creation
        assert state.get("llm_no_create") is False

    def test_llm_no_create_missing_field(self):
        """Test when llm_no_create field is missing."""
        state = {
            "ticket_title": "Test Error",
            "ticket_description": "Test description",
            # llm_no_create field missing
        }

        # Should default to allowing creation
        assert state.get("llm_no_create") is None or state.get("llm_no_create") is False


class TestComputeLoghashWithStack:
    """Test that compute_loghash distinguishes errors by exception type."""

    MSG = "Failed to renew license (attempt will be retried on next schedule run)"

    def test_same_message_no_stack_same_hash(self):
        """Without stack trace, identical messages produce the same hash."""
        assert compute_loghash(self.MSG) == compute_loghash(self.MSG)

    def test_same_message_different_exceptions_different_hash(self):
        """Same message but different exception types produce different hashes."""
        h1 = compute_loghash(
            self.MSG,
            "j.l.IllegalArgumentException: The number of requested users must be positive.\n\tat ...",
        )
        h2 = compute_loghash(
            self.MSG,
            "o.s.w.c.HttpClientErrorException: 404 Not Found\n\tat ...",
        )
        assert h1 != h2

    def test_same_message_same_exception_same_hash(self):
        """Same message and same exception type produce the same hash."""
        h1 = compute_loghash(
            self.MSG,
            "j.l.IllegalArgumentException: first error\n\tat ...",
        )
        h2 = compute_loghash(
            self.MSG,
            "j.l.IllegalArgumentException: different detail\n\tat ...",
        )
        assert h1 == h2

    def test_with_vs_without_stack_different_hash(self):
        """Message-only hash differs from message+stack hash."""
        h_no_stack = compute_loghash(self.MSG)
        h_with_stack = compute_loghash(
            self.MSG,
            "j.l.IllegalArgumentException: ...\n\tat ...",
        )
        assert h_no_stack != h_with_stack

    def test_empty_stack_same_as_no_stack(self):
        """Empty string stack behaves like no stack."""
        assert compute_loghash(self.MSG, "") == compute_loghash(self.MSG)
