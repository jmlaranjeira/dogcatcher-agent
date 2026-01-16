"""Unit tests for async ticket creation module.

Tests async ticket creation, duplicate detection,
validation, and Jira integration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import hashlib

from agent.nodes.ticket_async import (
    create_ticket_async,
    create_tickets_batch_async,
    _validate_ticket_fields,
    _check_duplicates_async,
    _build_jira_payload,
    _execute_ticket_creation_async,
    _compute_fingerprint,
    _is_cap_reached,
    TicketValidationResult,
    DuplicateCheckResult,
    TicketPayload,
)


@pytest.fixture
def mock_config():
    """Mock configuration for ticket creation."""
    config = MagicMock()
    config.jira_project_key = "TEST"
    config.jira_similarity_threshold = 0.85
    config.auto_create_ticket = True
    config.max_tickets_per_run = 10
    config.aggregate_email_not_found = False
    config.aggregate_kafka_consumer = False
    config.max_title_length = 100
    config.persist_sim_fp = False
    config.comment_on_duplicate = False
    return config


@pytest.fixture
def sample_log_data():
    """Sample log data for testing."""
    return {
        "message": "NullPointerException in UserService",
        "logger": "com.example.UserService",
        "thread": "http-nio-8080-exec-1",
        "detail": "User not found",
        "timestamp": "2025-01-01T10:00:00Z"
    }


@pytest.fixture
def sample_state(sample_log_data):
    """Sample state with analysis results."""
    return {
        "log_data": sample_log_data,
        "error_type": "null-pointer-exception",
        "create_ticket": True,
        "ticket_title": "Fix NullPointerException in UserService",
        "ticket_description": "## Problem\nUser not found error",
        "severity": "high",
        "_tickets_created_in_run": 0
    }


@pytest.fixture
def mock_jira_client():
    """Mock async Jira client."""
    client = AsyncMock()
    client.is_configured.return_value = True
    client.create_issue.return_value = {"key": "TEST-123"}
    client.search.return_value = {"issues": []}
    return client


class TestValidateTicketFields:
    """Test ticket field validation."""

    def test_valid_fields(self, sample_state):
        """Test validation passes with valid fields."""
        result = _validate_ticket_fields(sample_state)

        assert result.is_valid is True
        assert result.title == sample_state["ticket_title"]
        assert result.description == sample_state["ticket_description"]
        assert result.error_message is None

    def test_missing_title(self, sample_state):
        """Test validation fails when title is missing."""
        del sample_state["ticket_title"]
        result = _validate_ticket_fields(sample_state)

        assert result.is_valid is False
        assert "Missing" in result.error_message

    def test_missing_description(self, sample_state):
        """Test validation fails when description is missing."""
        del sample_state["ticket_description"]
        result = _validate_ticket_fields(sample_state)

        assert result.is_valid is False
        assert "Missing" in result.error_message

    def test_empty_title(self, sample_state):
        """Test validation fails with empty title."""
        sample_state["ticket_title"] = ""
        result = _validate_ticket_fields(sample_state)

        assert result.is_valid is False
        assert "empty" in result.error_message.lower()

    def test_empty_description(self, sample_state):
        """Test validation fails with empty description."""
        sample_state["ticket_description"] = ""
        result = _validate_ticket_fields(sample_state)

        assert result.is_valid is False
        assert "empty" in result.error_message.lower()


class TestComputeFingerprint:
    """Test fingerprint computation."""

    def test_fingerprint_consistency(self, sample_state):
        """Test fingerprint is consistent for same input."""
        fp1 = _compute_fingerprint(sample_state)
        fp2 = _compute_fingerprint(sample_state)

        assert fp1 == fp2
        assert len(fp1) == 12

    def test_fingerprint_different_messages(self, sample_state):
        """Test different messages produce different fingerprints."""
        fp1 = _compute_fingerprint(sample_state)

        sample_state["log_data"]["message"] = "Different error message"
        fp2 = _compute_fingerprint(sample_state)

        assert fp1 != fp2


class TestCheckDuplicatesAsync:
    """Test async duplicate checking."""

    @pytest.mark.asyncio
    async def test_no_duplicates(self, mock_config, sample_state, mock_jira_client):
        """Test no duplicates found."""
        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async._load_processed_fingerprints', return_value=set()):
                with patch('agent.nodes.ticket_async.check_fingerprint_duplicate_async', new_callable=AsyncMock) as mock_fp:
                    mock_fp.return_value = (False, None)

                    with patch('agent.nodes.ticket_async.find_similar_ticket_async', new_callable=AsyncMock) as mock_sim:
                        mock_sim.return_value = (None, 0.0, None)

                        result = await _check_duplicates_async(
                            sample_state,
                            sample_state["ticket_title"],
                            mock_jira_client
                        )

        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_fingerprint_cache_duplicate(self, mock_config, sample_state, mock_jira_client):
        """Test duplicate found in fingerprint cache."""
        fingerprint = _compute_fingerprint(sample_state)

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async._load_processed_fingerprints', return_value={fingerprint}):
                result = await _check_duplicates_async(
                    sample_state,
                    sample_state["ticket_title"],
                    mock_jira_client
                )

        assert result.is_duplicate is True
        assert "fingerprint" in result.message.lower()

    @pytest.mark.asyncio
    async def test_jira_fingerprint_duplicate(self, mock_config, sample_state, mock_jira_client):
        """Test duplicate found via Jira fingerprint label."""
        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async._load_processed_fingerprints', return_value=set()):
                with patch('agent.nodes.ticket_async.check_fingerprint_duplicate_async', new_callable=AsyncMock) as mock_fp:
                    mock_fp.return_value = (True, "TEST-456")

                    result = await _check_duplicates_async(
                        sample_state,
                        sample_state["ticket_title"],
                        mock_jira_client
                    )

        assert result.is_duplicate is True
        assert result.existing_ticket_key == "TEST-456"

    @pytest.mark.asyncio
    async def test_similarity_duplicate(self, mock_config, sample_state, mock_jira_client):
        """Test duplicate found via similarity search."""
        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async._load_processed_fingerprints', return_value=set()):
                with patch('agent.nodes.ticket_async.check_fingerprint_duplicate_async', new_callable=AsyncMock) as mock_fp:
                    mock_fp.return_value = (False, None)

                    with patch('agent.nodes.ticket_async.find_similar_ticket_async', new_callable=AsyncMock) as mock_sim:
                        mock_sim.return_value = ("TEST-789", 0.92, "Similar issue title")

                        result = await _check_duplicates_async(
                            sample_state,
                            sample_state["ticket_title"],
                            mock_jira_client
                        )

        assert result.is_duplicate is True
        assert result.existing_ticket_key == "TEST-789"
        assert result.similarity_score == 0.92

    @pytest.mark.asyncio
    async def test_llm_decided_no_ticket(self, mock_config, sample_state, mock_jira_client):
        """Test when LLM decided not to create ticket."""
        sample_state["create_ticket"] = False

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async._load_processed_fingerprints', return_value=set()):
                result = await _check_duplicates_async(
                    sample_state,
                    sample_state["ticket_title"],
                    mock_jira_client
                )

        assert result.is_duplicate is False
        assert "LLM decision" in result.message


class TestBuildJiraPayload:
    """Test Jira payload building."""

    def test_payload_structure(self, mock_config, sample_state):
        """Test payload has correct structure."""
        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            payload = _build_jira_payload(
                sample_state,
                sample_state["ticket_title"],
                sample_state["ticket_description"]
            )

        assert isinstance(payload, TicketPayload)
        assert payload.payload["fields"]["project"]["key"] == "TEST"
        assert payload.payload["fields"]["issuetype"]["name"] == "Bug"
        assert "datadog-log" in payload.labels

    def test_payload_labels(self, mock_config, sample_state):
        """Test payload includes correct labels."""
        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            payload = _build_jira_payload(
                sample_state,
                sample_state["ticket_title"],
                sample_state["ticket_description"]
            )

        assert "datadog-log" in payload.labels
        assert "async-created" in payload.labels
        # Should have loghash label
        loghash_labels = [l for l in payload.labels if l.startswith("loghash-")]
        assert len(loghash_labels) == 1

    def test_payload_title_cleaned(self, mock_config, sample_state):
        """Test title is cleaned and prefixed."""
        sample_state["ticket_title"] = "**Bold Title**"
        sample_state["error_type"] = "db-error"

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            payload = _build_jira_payload(
                sample_state,
                sample_state["ticket_title"],
                sample_state["ticket_description"]
            )

        assert "**" not in payload.title
        assert "[Datadog]" in payload.title
        assert "[db-error]" in payload.title


class TestIsCapReached:
    """Test ticket cap checking."""

    def test_cap_not_reached(self, mock_config, sample_state):
        """Test cap not reached."""
        sample_state["_tickets_created_in_run"] = 5
        mock_config.max_tickets_per_run = 10

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            assert _is_cap_reached(sample_state) is False

    def test_cap_reached(self, mock_config, sample_state):
        """Test cap reached."""
        sample_state["_tickets_created_in_run"] = 10
        mock_config.max_tickets_per_run = 10

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            assert _is_cap_reached(sample_state) is True

    def test_cap_exceeded(self, mock_config, sample_state):
        """Test cap exceeded."""
        sample_state["_tickets_created_in_run"] = 15
        mock_config.max_tickets_per_run = 10

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            assert _is_cap_reached(sample_state) is True

    def test_cap_unlimited(self, mock_config, sample_state):
        """Test unlimited cap (0 or negative)."""
        sample_state["_tickets_created_in_run"] = 100
        mock_config.max_tickets_per_run = 0

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            assert _is_cap_reached(sample_state) is False


class TestCreateTicketAsync:
    """Test main async ticket creation."""

    @pytest.mark.asyncio
    async def test_create_ticket_success(self, mock_config, sample_state, mock_jira_client):
        """Test successful ticket creation."""
        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async.AsyncJiraClient') as MockClient:
                MockClient.return_value.__aenter__.return_value = mock_jira_client
                MockClient.return_value.__aexit__.return_value = None

                with patch('agent.nodes.ticket_async._check_duplicates_async', new_callable=AsyncMock) as mock_dup:
                    mock_dup.return_value = DuplicateCheckResult(is_duplicate=False)

                    with patch('agent.nodes.ticket_async._load_processed_fingerprints', return_value=set()):
                        with patch('agent.nodes.ticket_async._save_processed_fingerprints'):
                            result = await create_ticket_async(sample_state)

        assert result["ticket_created"] is True
        assert result.get("jira_response_key") == "TEST-123"

    @pytest.mark.asyncio
    async def test_create_ticket_validation_failure(self, mock_config):
        """Test ticket creation fails with invalid state."""
        invalid_state = {"log_data": {}}  # Missing required fields

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            result = await create_ticket_async(invalid_state)

        assert result["ticket_created"] is True  # Still True to mark as processed
        assert "Missing" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_create_ticket_duplicate(self, mock_config, sample_state, mock_jira_client):
        """Test ticket creation skipped for duplicate."""
        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async.AsyncJiraClient') as MockClient:
                MockClient.return_value.__aenter__.return_value = mock_jira_client
                MockClient.return_value.__aexit__.return_value = None

                with patch('agent.nodes.ticket_async._check_duplicates_async', new_callable=AsyncMock) as mock_dup:
                    mock_dup.return_value = DuplicateCheckResult(
                        is_duplicate=True,
                        existing_ticket_key="TEST-999",
                        message="Duplicate found"
                    )

                    result = await create_ticket_async(sample_state)

        assert result["ticket_created"] is True
        assert "Duplicate" in result.get("message", "")
        assert result.get("jira_response_key") is None

    @pytest.mark.asyncio
    async def test_create_ticket_dry_run(self, mock_config, sample_state, mock_jira_client):
        """Test ticket creation in dry-run mode."""
        mock_config.auto_create_ticket = False

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async.AsyncJiraClient') as MockClient:
                MockClient.return_value.__aenter__.return_value = mock_jira_client
                MockClient.return_value.__aexit__.return_value = None

                with patch('agent.nodes.ticket_async._check_duplicates_async', new_callable=AsyncMock) as mock_dup:
                    mock_dup.return_value = DuplicateCheckResult(is_duplicate=False)

                    result = await create_ticket_async(sample_state)

        assert result["ticket_created"] is True
        assert "simulated" in result.get("message", "").lower()
        # Should not call create_issue in dry-run
        mock_jira_client.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_ticket_cap_reached(self, mock_config, sample_state, mock_jira_client):
        """Test ticket creation when cap is reached."""
        sample_state["_tickets_created_in_run"] = 10
        mock_config.max_tickets_per_run = 10

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async.AsyncJiraClient') as MockClient:
                MockClient.return_value.__aenter__.return_value = mock_jira_client
                MockClient.return_value.__aexit__.return_value = None

                with patch('agent.nodes.ticket_async._check_duplicates_async', new_callable=AsyncMock) as mock_dup:
                    mock_dup.return_value = DuplicateCheckResult(is_duplicate=False)

                    result = await create_ticket_async(sample_state)

        assert result["ticket_created"] is True
        assert "limit" in result.get("message", "").lower()


class TestCreateTicketsBatchAsync:
    """Test batch ticket creation."""

    @pytest.mark.asyncio
    async def test_batch_creation_success(self, mock_config, sample_state):
        """Test successful batch creation."""
        states = [sample_state.copy() for _ in range(3)]

        with patch('agent.nodes.ticket_async.create_ticket_async', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {
                "ticket_created": True,
                "jira_response_key": "TEST-123"
            }

            results = await create_tickets_batch_async(states, max_concurrent=2)

        assert len(results) == 3
        assert mock_create.call_count == 3

    @pytest.mark.asyncio
    async def test_batch_creation_partial_failure(self, mock_config, sample_state):
        """Test batch creation handles partial failures."""
        states = [sample_state.copy() for _ in range(3)]

        call_count = 0

        async def mock_create(state):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Creation failed")
            return {"ticket_created": True, "jira_response_key": "TEST-123"}

        with patch('agent.nodes.ticket_async.create_ticket_async', side_effect=mock_create):
            results = await create_tickets_batch_async(states)

        assert len(results) == 3
        # Failed one should have error message
        assert "failed" in results[1].get("message", "").lower()

    @pytest.mark.asyncio
    async def test_batch_creation_respects_concurrency(self, mock_config, sample_state):
        """Test batch creation respects max_concurrent."""
        import asyncio

        states = [sample_state.copy() for _ in range(5)]

        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def mock_create(state):
            nonlocal concurrent_count, max_concurrent

            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)

            await asyncio.sleep(0.05)

            async with lock:
                concurrent_count -= 1

            return {"ticket_created": True}

        with patch('agent.nodes.ticket_async.create_ticket_async', side_effect=mock_create):
            await create_tickets_batch_async(states, max_concurrent=2)

        assert max_concurrent <= 2


class TestExecuteTicketCreationAsync:
    """Test ticket execution function."""

    @pytest.mark.asyncio
    async def test_execute_real_creation(self, mock_config, sample_state, mock_jira_client):
        """Test real ticket creation execution."""
        payload = TicketPayload(
            payload={"fields": {}},
            title="Test Title",
            description="Test Description",
            labels=["test"],
            fingerprint="abc123"
        )

        mock_config.auto_create_ticket = True

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            with patch('agent.nodes.ticket_async._load_processed_fingerprints', return_value=set()):
                with patch('agent.nodes.ticket_async._save_processed_fingerprints'):
                    result = await _execute_ticket_creation_async(
                        sample_state,
                        payload,
                        mock_jira_client
                    )

        assert result["ticket_created"] is True
        assert result.get("jira_response_key") == "TEST-123"
        mock_jira_client.create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_simulation(self, mock_config, sample_state, mock_jira_client):
        """Test simulated ticket creation."""
        payload = TicketPayload(
            payload={"fields": {}},
            title="Test Title",
            description="Test Description",
            labels=["test"],
            fingerprint="abc123"
        )

        mock_config.auto_create_ticket = False

        with patch('agent.nodes.ticket_async.get_config', return_value=mock_config):
            result = await _execute_ticket_creation_async(
                sample_state,
                payload,
                mock_jira_client
            )

        assert result["ticket_created"] is True
        assert "simulated" in result.get("message", "").lower()
        mock_jira_client.create_issue.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
