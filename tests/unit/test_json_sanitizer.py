"""Tests for the LLM JSON sanitizer."""

import json
import pytest

from agent.nodes.json_sanitizer import parse_llm_json, _sanitize_json_string


class TestParseLlmJson:
    """Tests for parse_llm_json()."""

    def test_valid_json_passes_through(self):
        raw = '{"error_type": "db-error", "ticket_title": "DB failure"}'
        result = parse_llm_json(raw)
        assert result["error_type"] == "db-error"

    def test_json_with_escaped_newlines(self):
        raw = '{"title": "Error", "desc": "line1\\nline2"}'
        result = parse_llm_json(raw)
        assert result["desc"] == "line1\nline2"

    def test_json_with_literal_newlines_in_values(self):
        """Bedrock sometimes returns literal newlines inside JSON strings."""
        raw = '{"title": "Error", "desc": "line1\nline2\nline3"}'
        # This would fail with json.loads directly
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw)
        # But parse_llm_json handles it
        result = parse_llm_json(raw)
        assert result["desc"] == "line1\nline2\nline3"

    def test_json_with_literal_tabs(self):
        raw = '{"title": "Error", "desc": "col1\tcol2"}'
        result = parse_llm_json(raw)
        assert result["desc"] == "col1\tcol2"

    def test_json_with_markdown_newlines(self):
        """Real-world Bedrock output with markdown in description."""
        raw = (
            '{"error_type": "unknown-error", '
            '"create_ticket": true, '
            '"ticket_title": "Node.js startup failure", '
            '"ticket_description": "**Problem:**\nThe service fails\n\n**Actions:**\n- Restart", '
            '"severity": "high"}'
        )
        result = parse_llm_json(raw)
        assert result["error_type"] == "unknown-error"
        assert "**Problem:**" in result["ticket_description"]

    def test_raises_on_non_dict(self):
        with pytest.raises(ValueError, match="Expected dict"):
            parse_llm_json("[1, 2, 3]")

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("not json at all")


class TestSanitizeJsonString:
    """Tests for _sanitize_json_string()."""

    def test_no_change_for_clean_json(self):
        raw = '{"key": "value"}'
        assert _sanitize_json_string(raw) == raw

    def test_newlines_outside_strings_untouched(self):
        raw = '{\n"key": "value"\n}'
        assert _sanitize_json_string(raw) == raw

    def test_newlines_inside_strings_escaped(self):
        raw = '{"key": "line1\nline2"}'
        sanitized = _sanitize_json_string(raw)
        assert sanitized == '{"key": "line1\\nline2"}'

    def test_handles_escaped_quotes(self):
        raw = '{"key": "say \\"hello\\" world"}'
        sanitized = _sanitize_json_string(raw)
        # Should not break on escaped quotes
        parsed = json.loads(sanitized)
        assert parsed["key"] == 'say "hello" world'
