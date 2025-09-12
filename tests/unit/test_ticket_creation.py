"""Unit tests for ticket creation functionality."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any

from agent.nodes.ticket import (
    create_ticket,
    _validate_ticket_fields,
    _prepare_context,
    _check_fingerprint_dup,
    _check_llm_no_create,
    _check_jira_duplicate,
    _build_jira_payload,
    _execute_ticket_creation,
    TicketValidationResult,
    DuplicateCheckResult,
    TicketPayload
)


class TestTicketValidation:
    """Test ticket field validation."""
    
    def test_validate_ticket_fields_success(self, sample_state):
        """Test successful ticket field validation."""
        result = _validate_ticket_fields(sample_state)
        
        assert isinstance(result, TicketValidationResult)
        assert result.is_valid is True
        assert result.title == "Database Connection Error"
        assert result.description == "The application failed to connect to the database due to a timeout."
        assert result.should_return is False
    
    def test_validate_ticket_fields_missing_title(self, sample_state):
        """Test validation failure when title is missing."""
        sample_state.pop("ticket_title")
        result = _validate_ticket_fields(sample_state)
        
        assert isinstance(result, TicketValidationResult)
        assert result.is_valid is False
        assert result.should_return is True
        assert "ticket_title" in result.state.get("message", "")
    
    def test_validate_ticket_fields_missing_description(self, sample_state):
        """Test validation failure when description is missing."""
        sample_state.pop("ticket_description")
        result = _validate_ticket_fields(sample_state)
        
        assert isinstance(result, TicketValidationResult)
        assert result.is_valid is False
        assert result.should_return is True
        assert "ticket_description" in result.state.get("message", "")
    
    def test_validate_ticket_fields_empty_title(self, sample_state):
        """Test validation failure when title is empty."""
        sample_state["ticket_title"] = ""
        result = _validate_ticket_fields(sample_state)
        
        assert isinstance(result, TicketValidationResult)
        assert result.is_valid is False
        assert result.should_return is True
    
    def test_validate_ticket_fields_empty_description(self, sample_state):
        """Test validation failure when description is empty."""
        sample_state["ticket_description"] = ""
        result = _validate_ticket_fields(sample_state)
        
        assert isinstance(result, TicketValidationResult)
        assert result.is_valid is False
        assert result.should_return is True


class TestContextPreparation:
    """Test context preparation and fingerprint generation."""
    
    def test_prepare_context_success(self, sample_state, mock_config):
        """Test successful context preparation."""
        result = _prepare_context(sample_state, "Test Title", "Test Description")
        
        assert result.fingerprint is not None
        assert len(result.fingerprint) > 0
        assert result.processed is not None
        assert result.occ is not None
        assert result.fp_source is not None
        assert result.full_description is not None
        assert result.win == 48  # From sample_state
    
    def test_prepare_context_with_severity_override(self, sample_state, mock_config):
        """Test context preparation with severity override."""
        # Mock severity rules
        mock_config.agent.get_severity_rules.return_value = {
            "database-connection": "high"
        }
        
        result = _prepare_context(sample_state, "Test Title", "Test Description")
        
        # Should use overridden severity
        assert "high" in result.full_description.lower() or "high" in result.full_description


class TestFingerprintDuplicateCheck:
    """Test fingerprint-based duplicate detection."""
    
    def test_check_fingerprint_dup_no_duplicate(self, sample_state, mock_config):
        """Test when no fingerprint duplicate is found."""
        fingerprint = "test-fingerprint-123"
        processed = set()
        
        result = _check_fingerprint_dup(sample_state, fingerprint, processed, 1, "test-source")
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.should_return is False
        assert result.issue_key is None
    
    def test_check_fingerprint_dup_in_run_duplicate(self, sample_state, mock_config):
        """Test when fingerprint duplicate is found in current run."""
        fingerprint = "test-fingerprint-123"
        processed = {fingerprint}  # Already processed
        
        result = _check_fingerprint_dup(sample_state, fingerprint, processed, 1, "test-source")
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.should_return is True
        assert "already processed" in result.state.get("message", "").lower()
    
    def test_check_fingerprint_dup_cross_run_duplicate(self, sample_state, mock_config):
        """Test when fingerprint duplicate is found across runs."""
        fingerprint = "test-fingerprint-123"
        processed = set()
        
        # Mock file existence and content
        with patch('builtins.open', mock_open_file_with_fingerprint(fingerprint)):
            with patch('os.path.exists', return_value=True):
                result = _check_fingerprint_dup(sample_state, fingerprint, processed, 1, "test-source")
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.should_return is True
        assert "already processed" in result.state.get("message", "").lower()


class TestLLMNoCreateCheck:
    """Test LLM decision to not create ticket."""
    
    def test_check_llm_no_create_allow(self, sample_state, mock_config):
        """Test when LLM allows ticket creation."""
        result = _check_llm_no_create(sample_state, "test-fingerprint", 1)
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.should_return is False
    
    def test_check_llm_no_create_deny(self, sample_state, mock_config):
        """Test when LLM denies ticket creation."""
        sample_state["llm_no_create"] = True
        
        result = _check_llm_no_create(sample_state, "test-fingerprint", 1)
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.should_return is True
        assert "llm decision" in result.state.get("message", "").lower()


class TestJiraDuplicateCheck:
    """Test Jira duplicate detection."""
    
    def test_check_jira_duplicate_no_duplicate(self, sample_state, mock_config, mock_jira_client):
        """Test when no Jira duplicate is found."""
        with patch('agent.jira.match.find_similar_ticket', return_value=(None, 0.0, None)):
            result = _check_jira_duplicate("Test Title", sample_state, 48, 1, set(), "test-fingerprint")
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.should_return is False
        assert result.issue_key is None
    
    def test_check_jira_duplicate_found(self, sample_state, mock_config, mock_jira_client):
        """Test when Jira duplicate is found."""
        with patch('agent.jira.match.find_similar_ticket', return_value=("TEST-123", 0.85, "Duplicate Title")):
            with patch('agent.nodes.ticket._maybe_comment_duplicate'):
                result = _check_jira_duplicate("Test Title", sample_state, 48, 1, set(), "test-fingerprint")
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.should_return is True
        assert result.issue_key == "TEST-123"
        assert "duplicate" in result.state.get("message", "").lower()


class TestJiraPayloadBuilding:
    """Test Jira payload construction."""
    
    def test_build_jira_payload_success(self, sample_state, mock_config):
        """Test successful Jira payload building."""
        result = _build_jira_payload(sample_state, "Test Title", "Test Description")
        
        assert isinstance(result, TicketPayload)
        assert result.clean_title is not None
        assert result.full_description is not None
        assert result.payload is not None
        assert "fields" in result.payload
        assert "project" in result.payload["fields"]
        assert result.payload["fields"]["project"]["key"] == "TEST"
    
    def test_build_jira_payload_with_aggregation(self, sample_state, mock_config):
        """Test Jira payload building with aggregation labels."""
        sample_state["error_type"] = "email-not-found"
        mock_config.agent.aggregate_email_not_found = True
        
        result = _build_jira_payload(sample_state, "Test Title", "Test Description")
        
        assert isinstance(result, TicketPayload)
        # Should include aggregation label
        labels = result.payload["fields"].get("labels", [])
        assert any("aggregate-email-not-found" in label for label in labels)


class TestTicketExecution:
    """Test ticket execution (creation or simulation)."""
    
    def test_execute_ticket_creation_simulation(self, sample_state, mock_config, mock_jira_client):
        """Test ticket creation in simulation mode."""
        mock_config.agent.auto_create_ticket = False
        
        payload = TicketPayload(
            clean_title="Test Title",
            full_description="Test Description",
            payload={"fields": {"summary": "Test Title"}},
            fingerprint="test-fingerprint"
        )
        
        result = _execute_ticket_creation(sample_state, payload.clean_title, payload.full_description, 
                                        payload.payload, payload.fingerprint, 1, set())
        
        assert result.state["ticket_created"] is True
        assert "simulated" in result.state.get("message", "").lower()
    
    def test_execute_ticket_creation_real(self, sample_state, mock_config, mock_jira_client):
        """Test real ticket creation."""
        mock_config.agent.auto_create_ticket = True
        
        payload = TicketPayload(
            clean_title="Test Title",
            full_description="Test Description",
            payload={"fields": {"summary": "Test Title"}},
            fingerprint="test-fingerprint"
        )
        
        result = _execute_ticket_creation(sample_state, payload.clean_title, payload.full_description, 
                                        payload.payload, payload.fingerprint, 1, set())
        
        assert result.state["ticket_created"] is True
        assert "created" in result.state.get("message", "").lower()
    
    def test_execute_ticket_creation_max_tickets_reached(self, sample_state, mock_config, mock_jira_client):
        """Test when maximum tickets per run is reached."""
        mock_config.agent.auto_create_ticket = True
        mock_config.agent.max_tickets_per_run = 1
        sample_state["_tickets_created_in_run"] = 1  # Already at limit
        
        payload = TicketPayload(
            clean_title="Test Title",
            full_description="Test Description",
            payload={"fields": {"summary": "Test Title"}},
            fingerprint="test-fingerprint"
        )
        
        result = _execute_ticket_creation(sample_state, payload.clean_title, payload.full_description, 
                                        payload.payload, payload.fingerprint, 1, set())
        
        assert result.state["ticket_created"] is True
        assert "max tickets" in result.state.get("message", "").lower()


class TestCreateTicketIntegration:
    """Integration tests for the main create_ticket function."""
    
    def test_create_ticket_success_simulation(self, sample_state, mock_config, mock_jira_client):
        """Test successful ticket creation in simulation mode."""
        mock_config.agent.auto_create_ticket = False
        
        with patch('agent.nodes.ticket._load_processed_fingerprints', return_value=set()):
            with patch('agent.nodes.ticket._save_processed_fingerprints'):
                with patch('agent.jira.match.find_similar_ticket', return_value=(None, 0.0, None)):
                    result = create_ticket(sample_state)
        
        assert result["ticket_created"] is True
        assert "simulated" in result.get("message", "").lower()
    
    def test_create_ticket_validation_error(self, sample_state, mock_config):
        """Test ticket creation with validation error."""
        sample_state.pop("ticket_title")  # Remove required field
        
        result = create_ticket(sample_state)
        
        assert result["ticket_created"] is False
        assert "ticket_title" in result.get("message", "")
    
    def test_create_ticket_duplicate_found(self, sample_state, mock_config, mock_jira_client):
        """Test ticket creation when duplicate is found."""
        with patch('agent.nodes.ticket._load_processed_fingerprints', return_value=set()):
            with patch('agent.jira.match.find_similar_ticket', return_value=("TEST-123", 0.85, "Duplicate Title")):
                with patch('agent.nodes.ticket._maybe_comment_duplicate'):
                    result = create_ticket(sample_state)
        
        assert result["ticket_created"] is False
        assert "duplicate" in result.get("message", "").lower()


# Helper functions for testing
def mock_open_file_with_fingerprint(fingerprint: str):
    """Mock file content with specific fingerprint."""
    def mock_open(*args, **kwargs):
        mock_file = MagicMock()
        mock_file.__enter__.return_value = mock_file
        mock_file.read.return_value = f'["{fingerprint}"]'
        return mock_file
    return mock_open
