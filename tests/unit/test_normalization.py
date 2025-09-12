"""Unit tests for text normalization and threshold checks."""
import pytest
from unittest.mock import Mock, patch

from agent.jira.utils import (
    normalize_text,
    normalize_log_message,
    extract_text_from_description,
    should_comment,
    update_comment_timestamp,
    priority_name_from_severity
)
from agent.jira.match import _sim


class TestTextNormalization:
    """Test text normalization functions."""
    
    def test_normalize_text_basic(self):
        """Test basic text normalization."""
        text = "Database Connection Error"
        result = normalize_text(text)
        
        assert result == "database connection error"
    
    def test_normalize_text_with_special_chars(self):
        """Test normalization with special characters."""
        text = "Error: Database connection failed! (Timeout: 30s)"
        result = normalize_text(text)
        
        assert result == "error database connection failed timeout 30s"
    
    def test_normalize_text_with_numbers(self):
        """Test normalization with numbers."""
        text = "Error 500: Internal Server Error"
        result = normalize_text(text)
        
        assert result == "error 500 internal server error"
    
    def test_normalize_text_empty(self):
        """Test normalization of empty text."""
        result = normalize_text("")
        assert result == ""
    
    def test_normalize_text_none(self):
        """Test normalization of None."""
        result = normalize_text(None)
        assert result == ""
    
    def test_normalize_text_whitespace(self):
        """Test normalization with excessive whitespace."""
        text = "  Database   Connection    Error  "
        result = normalize_text(text)
        
        assert result == "database connection error"


class TestLogMessageNormalization:
    """Test log message normalization."""
    
    def test_normalize_log_message_basic(self):
        """Test basic log message normalization."""
        message = "Database connection failed: Connection timeout"
        result = normalize_log_message(message)
        
        assert result == "database connection failed connection timeout"
    
    def test_normalize_log_message_with_timestamps(self):
        """Test normalization with timestamps."""
        message = "2025-12-09T10:30:00Z Database connection failed"
        result = normalize_log_message(message)
        
        assert result == "database connection failed"
    
    def test_normalize_log_message_with_uuids(self):
        """Test normalization with UUIDs."""
        message = "Request 123e4567-e89b-12d3-a456-426614174000 failed"
        result = normalize_log_message(message)
        
        assert result == "request failed"
    
    def test_normalize_log_message_with_emails(self):
        """Test normalization with email addresses."""
        message = "User john.doe@example.com not found"
        result = normalize_log_message(message)
        
        assert result == "user not found"
    
    def test_normalize_log_message_with_urls(self):
        """Test normalization with URLs."""
        message = "Failed to connect to https://api.example.com/v1/users"
        result = normalize_log_message(message)
        
        assert result == "failed to connect to"
    
    def test_normalize_log_message_with_tokens(self):
        """Test normalization with API tokens."""
        message = "Authentication failed for token sk-1234567890abcdef"
        result = normalize_log_message(message)
        
        assert result == "authentication failed for token"


class TestDescriptionExtraction:
    """Test description text extraction."""
    
    def test_extract_text_from_description_simple(self):
        """Test extraction from simple description."""
        description = "The application failed to connect to the database."
        result = extract_text_from_description(description)
        
        assert result == description
    
    def test_extract_text_from_description_with_formatting(self):
        """Test extraction from formatted description."""
        description = "**Error:** The application failed to connect to the database."
        result = extract_text_from_description(description)
        
        assert result == "Error: The application failed to connect to the database."
    
    def test_extract_text_from_description_with_links(self):
        """Test extraction from description with links."""
        description = "Error occurred. See [documentation](https://example.com) for details."
        result = extract_text_from_description(description)
        
        assert result == "Error occurred. See documentation for details."
    
    def test_extract_text_from_description_empty(self):
        """Test extraction from empty description."""
        result = extract_text_from_description("")
        assert result == ""
    
    def test_extract_text_from_description_none(self):
        """Test extraction from None description."""
        result = extract_text_from_description(None)
        assert result == ""


class TestSimilarityCalculation:
    """Test similarity calculation functions."""
    
    def test_sim_identical_strings(self):
        """Test similarity of identical strings."""
        result = _sim("database connection error", "database connection error")
        assert result == 1.0
    
    def test_sim_similar_strings(self):
        """Test similarity of similar strings."""
        result = _sim("database connection error", "database connection failed")
        assert 0.5 < result < 1.0
    
    def test_sim_different_strings(self):
        """Test similarity of different strings."""
        result = _sim("database connection error", "user authentication failed")
        assert 0.0 <= result < 0.5
    
    def test_sim_empty_strings(self):
        """Test similarity of empty strings."""
        result = _sim("", "")
        assert result == 1.0
    
    def test_sim_one_empty_string(self):
        """Test similarity when one string is empty."""
        result = _sim("database connection error", "")
        assert result == 0.0
    
    def test_sim_with_rapidfuzz(self):
        """Test similarity with rapidfuzz if available."""
        # This test will use rapidfuzz if available, otherwise fall back to difflib
        result = _sim("database connection error", "database connection failed")
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0


class TestCommentCooldown:
    """Test comment cooldown functionality."""
    
    def test_should_comment_no_cooldown(self):
        """Test commenting when no cooldown is configured."""
        result = should_comment("TEST-123", 0)  # 0 minutes cooldown
        assert result is True
    
    def test_should_comment_within_cooldown(self):
        """Test commenting within cooldown period."""
        # Mock current time and recent comment
        with patch('agent.jira.utils.datetime') as mock_datetime:
            mock_datetime.now.return_value = Mock()
            mock_datetime.now.return_value.timestamp.return_value = 1000
            
            with patch('agent.jira.utils.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open_file_with_timestamp(500)):  # 500 seconds ago
                    result = should_comment("TEST-123", 10)  # 10 minutes cooldown
        
        assert result is False  # Within cooldown period
    
    def test_should_comment_after_cooldown(self):
        """Test commenting after cooldown period."""
        # Mock current time and old comment
        with patch('agent.jira.utils.datetime') as mock_datetime:
            mock_datetime.now.return_value = Mock()
            mock_datetime.now.return_value.timestamp.return_value = 1000
            
            with patch('agent.jira.utils.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open_file_with_timestamp(100)):  # 900 seconds ago
                    result = should_comment("TEST-123", 10)  # 10 minutes cooldown
        
        assert result is True  # After cooldown period
    
    def test_should_comment_no_previous_comment(self):
        """Test commenting when no previous comment exists."""
        with patch('agent.jira.utils.os.path.exists', return_value=False):
            result = should_comment("TEST-123", 10)
        
        assert result is True
    
    def test_update_comment_timestamp(self):
        """Test updating comment timestamp."""
        with patch('builtins.open', mock_open_file_write()):
            with patch('agent.jira.utils.os.makedirs'):
                result = update_comment_timestamp("TEST-123")
        
        assert result is True


class TestPriorityMapping:
    """Test priority name mapping from severity."""
    
    def test_priority_name_from_severity_low(self):
        """Test priority mapping for low severity."""
        result = priority_name_from_severity("low")
        assert result == "Low"
    
    def test_priority_name_from_severity_medium(self):
        """Test priority mapping for medium severity."""
        result = priority_name_from_severity("medium")
        assert result == "Medium"
    
    def test_priority_name_from_severity_high(self):
        """Test priority mapping for high severity."""
        result = priority_name_from_severity("high")
        assert result == "High"
    
    def test_priority_name_from_severity_critical(self):
        """Test priority mapping for critical severity."""
        result = priority_name_from_severity("critical")
        assert result == "High"  # Critical maps to High in Jira
    
    def test_priority_name_from_severity_unknown(self):
        """Test priority mapping for unknown severity."""
        result = priority_name_from_severity("unknown")
        assert result == "Medium"  # Default fallback
    
    def test_priority_name_from_severity_none(self):
        """Test priority mapping for None severity."""
        result = priority_name_from_severity(None)
        assert result == "Medium"  # Default fallback
    
    def test_priority_name_from_severity_empty(self):
        """Test priority mapping for empty severity."""
        result = priority_name_from_severity("")
        assert result == "Medium"  # Default fallback


class TestThresholdValidation:
    """Test threshold validation and edge cases."""
    
    def test_similarity_threshold_boundaries(self):
        """Test similarity calculation at threshold boundaries."""
        # Test at exact threshold
        result = _sim("database connection error", "database connection error")
        assert result == 1.0
        
        # Test just below threshold
        result = _sim("database connection error", "database connection failed")
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
    
    def test_normalization_consistency(self):
        """Test that normalization is consistent."""
        text1 = "Database Connection Error"
        text2 = "database connection error"
        
        norm1 = normalize_text(text1)
        norm2 = normalize_text(text2)
        
        assert norm1 == norm2
    
    def test_log_message_normalization_consistency(self):
        """Test that log message normalization is consistent."""
        message1 = "Database connection failed: Connection timeout"
        message2 = "database connection failed connection timeout"
        
        norm1 = normalize_log_message(message1)
        norm2 = normalize_log_message(message2)
        
        assert norm1 == norm2


# Helper functions for testing
def mock_open_file_with_timestamp(timestamp: float):
    """Mock file content with specific timestamp."""
    def mock_open(*args, **kwargs):
        mock_file = Mock()
        mock_file.__enter__.return_value = mock_file
        mock_file.read.return_value = str(timestamp)
        return mock_file
    return mock_open


def mock_open_file_write():
    """Mock file write operation."""
    def mock_open(*args, **kwargs):
        mock_file = Mock()
        mock_file.__enter__.return_value = mock_file
        mock_file.write = Mock()
        return mock_file
    return mock_open
