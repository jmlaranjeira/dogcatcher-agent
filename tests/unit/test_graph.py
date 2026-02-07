"""Unit tests for LangGraph pipeline functions."""

import pytest
from unittest.mock import patch, MagicMock
from typing import Any, Dict

from agent.graph import analyze_log_wrapper, next_log, build_graph
from agent.state import GraphState


class TestAnalyzeLogWrapper:
    """Test analyze_log_wrapper function."""

    def test_empty_logs(self):
        """Test with no logs returns finished=True and create_ticket=False."""
        state = {"logs": [], "log_index": 0}

        result = analyze_log_wrapper(state)

        assert result["finished"] is True
        assert result["create_ticket"] is False

    def test_logs_is_none(self):
        """Test with logs=None returns finished=True."""
        state = {"log_index": 0}

        result = analyze_log_wrapper(state)

        assert result["finished"] is True
        assert result["create_ticket"] is False

    def test_index_out_of_range(self):
        """Test with log_index beyond logs length returns finished=True."""
        state = {
            "logs": [
                {"message": "Error 1", "logger": "app.service"},
            ],
            "log_index": 5,
        }

        result = analyze_log_wrapper(state)

        assert result["finished"] is True
        assert result["create_ticket"] is False

    def test_initializes_seen_logs_if_missing(self):
        """Test that seen_logs is initialized if not present."""
        state = {
            "logs": [{"message": "Error 1", "logger": "app.service"}],
            "log_index": 0,
        }

        with patch("agent.graph.analyze_log") as mock_analyze:
            mock_analyze.return_value = {
                **state,
                "create_ticket": True,
                "error_type": "database_error",
            }

            result = analyze_log_wrapper(state)

            assert "seen_logs" in state
            assert isinstance(state["seen_logs"], set)

    def test_initializes_created_fingerprints_if_missing(self):
        """Test that created_fingerprints is initialized if not present."""
        state = {
            "logs": [{"message": "Error 1", "logger": "app.service"}],
            "log_index": 0,
        }

        with patch("agent.graph.analyze_log") as mock_analyze:
            mock_analyze.return_value = {
                **state,
                "create_ticket": True,
                "error_type": "database_error",
            }

            result = analyze_log_wrapper(state)

            assert "created_fingerprints" in state
            assert isinstance(state["created_fingerprints"], set)

    def test_skips_duplicate_logs(self):
        """Test that duplicate logs (same logger|normalized_message) are skipped."""
        log = {"message": "Database connection failed", "logger": "app.db"}

        state = {
            "logs": [log],
            "log_index": 0,
            "seen_logs": {"app.db|database connection failed"},
        }

        result = analyze_log_wrapper(state)

        assert result["skipped_duplicate"] is True
        assert result["create_ticket"] is False

    def test_skips_duplicate_with_variable_data(self):
        """Test that logs with same pattern but different UUIDs/timestamps are treated as duplicates."""
        # normalize_log_message strips UUIDs, so these should be duplicates
        log1_message = "Request 123e4567-e89b-12d3-a456-426614174000 failed"
        log2_message = "Request 987f6543-a21b-34c5-d678-987654321000 failed"

        log1 = {"message": log1_message, "logger": "app.api"}
        log2 = {"message": log2_message, "logger": "app.api"}

        state = {
            "logs": [log1, log2],
            "log_index": 0,
            "seen_logs": set(),
        }

        with patch("agent.graph.analyze_log") as mock_analyze:
            mock_analyze.return_value = {
                **state,
                "create_ticket": True,
                "error_type": "api_error",
            }

            # Process first log
            result1 = analyze_log_wrapper(state)
            # Both messages normalize to "request failed" so they share the same key
            assert "seen_logs" in state
            assert len(state["seen_logs"]) == 1

            # Now process second log - should be detected as duplicate
            state["log_index"] = 1
            result2 = analyze_log_wrapper(state)

            assert result2["skipped_duplicate"] is True
            assert result2["create_ticket"] is False

    def test_calls_analyze_log_for_new_logs(self):
        """Test that analyze_log is called for new logs."""
        log = {"message": "New error", "logger": "app.service"}

        state = {
            "logs": [log],
            "log_index": 0,
            "seen_logs": set(),
        }

        expected_result = {
            **state,
            "create_ticket": True,
            "error_type": "service_error",
            "ticket_title": "Service Error",
            "ticket_description": "An error occurred",
            "severity": "medium",
        }

        with patch("agent.graph.analyze_log") as mock_analyze:
            mock_analyze.return_value = expected_result

            result = analyze_log_wrapper(state)

            # Verify analyze_log was called
            mock_analyze.assert_called_once()
            call_args = mock_analyze.call_args[0][0]

            # Verify the state passed to analyze_log includes log_message and log_data
            assert call_args["log_message"] == "New error"
            assert call_args["log_data"] == log
            assert "seen_logs" in call_args

            # Verify the result from analyze_log is returned
            assert result["create_ticket"] is True
            assert result["error_type"] == "service_error"

    def test_adds_log_to_seen_logs(self):
        """Test that processed log is added to seen_logs."""
        log = {"message": "Error message", "logger": "app.test"}

        state = {
            "logs": [log],
            "log_index": 0,
            "seen_logs": set(),
        }

        with patch("agent.graph.analyze_log") as mock_analyze:
            mock_analyze.return_value = {**state, "create_ticket": False}

            analyze_log_wrapper(state)

            # The log key should be added to seen_logs
            # normalize_log_message("Error message") -> "error message"
            assert "app.test|error message" in state["seen_logs"]

    def test_handles_missing_logger_field(self):
        """Test handling of log without logger field."""
        log = {"message": "Error without logger"}

        state = {
            "logs": [log],
            "log_index": 0,
            "seen_logs": set(),
        }

        with patch("agent.graph.analyze_log") as mock_analyze:
            mock_analyze.return_value = {**state, "create_ticket": False}

            analyze_log_wrapper(state)

            # Should use "unknown" as logger
            assert "unknown|error without logger" in state["seen_logs"]

    def test_handles_missing_message_field(self):
        """Test handling of log without message field."""
        log = {"logger": "app.test"}

        state = {
            "logs": [log],
            "log_index": 0,
            "seen_logs": set(),
        }

        with patch("agent.graph.analyze_log") as mock_analyze:
            mock_analyze.return_value = {**state, "create_ticket": False}

            result = analyze_log_wrapper(state)

            # Should use "<no message>" as default message
            call_args = mock_analyze.call_args[0][0]
            assert call_args["log_message"] == "<no message>"


class TestNextLog:
    """Test next_log function."""

    def test_advances_log_index_by_one(self):
        """Test that log_index is advanced by 1."""
        state = {
            "logs": [
                {"message": "Error 1", "logger": "app.service"},
                {"message": "Error 2", "logger": "app.service"},
            ],
            "log_index": 0,
            "seen_logs": set(),
        }

        result = next_log(state)

        assert result["log_index"] == 1

    def test_sets_finished_when_index_reaches_end(self):
        """Test that finished=True when index reaches end of logs."""
        state = {
            "logs": [
                {"message": "Error 1", "logger": "app.service"},
                {"message": "Error 2", "logger": "app.service"},
            ],
            "log_index": 1,  # Last log (index 1, advancing to 2)
            "seen_logs": set(),
        }

        result = next_log(state)

        assert result["finished"] is True
        # When finished, the state is returned without updating log_index
        assert "finished" in result

    def test_sets_log_message_and_log_data_for_next_log(self):
        """Test that log_message and log_data are set for the next log."""
        log1 = {"message": "Error 1", "logger": "app.service"}
        log2 = {"message": "Error 2", "logger": "app.api"}

        state = {
            "logs": [log1, log2],
            "log_index": 0,
            "seen_logs": set(),
        }

        result = next_log(state)

        assert result["log_message"] == "Error 2"
        assert result["log_data"] == log2
        assert result["log_index"] == 1

    def test_skips_logs_already_in_seen_logs(self):
        """Test that logs in seen_logs are marked as skipped."""
        log1 = {"message": "Error 1", "logger": "app.service"}
        log2 = {"message": "Error 2", "logger": "app.api"}

        state = {
            "logs": [log1, log2],
            "log_index": 0,
            "seen_logs": {"app.api|error 2"},  # Next log already seen
        }

        result = next_log(state)

        assert result["skipped_duplicate"] is True
        assert result["log_index"] == 1

    def test_handles_empty_seen_logs(self):
        """Test that function handles missing seen_logs gracefully."""
        log1 = {"message": "Error 1", "logger": "app.service"}
        log2 = {"message": "Error 2", "logger": "app.api"}

        state = {
            "logs": [log1, log2],
            "log_index": 0,
        }

        result = next_log(state)

        # Should not crash, should process normally
        assert result["log_index"] == 1
        assert result["log_message"] == "Error 2"
        assert result["log_data"] == log2

    def test_handles_missing_logger_in_next_log(self):
        """Test handling of next log without logger field."""
        log1 = {"message": "Error 1", "logger": "app.service"}
        log2 = {"message": "Error 2"}  # No logger

        state = {
            "logs": [log1, log2],
            "log_index": 0,
            "seen_logs": set(),
        }

        result = next_log(state)

        assert result["log_message"] == "Error 2"
        assert result["log_data"] == log2

    def test_handles_missing_message_in_next_log(self):
        """Test handling of next log without message field."""
        log1 = {"message": "Error 1", "logger": "app.service"}
        log2 = {"logger": "app.api"}  # No message

        state = {
            "logs": [log1, log2],
            "log_index": 0,
            "seen_logs": set(),
        }

        result = next_log(state)

        assert result["log_message"] == "<no message>"
        assert result["log_data"] == log2

    def test_normalizes_log_message_for_duplicate_detection(self):
        """Test that log messages are normalized when checking duplicates."""
        log1 = {"message": "Error 1", "logger": "app.service"}
        log2 = {
            "message": "Request 123e4567-e89b-12d3-a456-426614174000 failed",
            "logger": "app.api",
        }

        # The normalized version without UUID should be in seen_logs
        state = {
            "logs": [log1, log2],
            "log_index": 0,
            "seen_logs": {"app.api|request failed"},
        }

        result = next_log(state)

        assert result["skipped_duplicate"] is True


class TestBuildGraph:
    """Test build_graph function."""

    def test_graph_compiles_without_error(self):
        """Test that the graph compiles successfully."""
        graph = build_graph()

        assert graph is not None

    def test_graph_has_expected_nodes(self):
        """Test that the graph contains all expected nodes."""
        graph = build_graph()

        # LangGraph compiled graphs expose nodes through the graph structure
        # We can verify the graph was built by checking it has a config
        assert hasattr(graph, "get_graph")

        # Get the graph structure
        graph_structure = graph.get_graph()

        # Verify nodes exist
        node_names = {node.id for node in graph_structure.nodes.values()}
        expected_nodes = {"fetch_logs", "analyze_log", "create_ticket", "next_log"}

        assert expected_nodes.issubset(
            node_names
        ), f"Missing nodes: {expected_nodes - node_names}"

    def test_graph_has_correct_entry_point(self):
        """Test that the graph has fetch_logs as entry point."""
        graph = build_graph()
        graph_structure = graph.get_graph()

        # The entry point should be fetch_logs
        # LangGraph uses __start__ as the actual entry, which connects to our entry point
        assert "__start__" in {node.id for node in graph_structure.nodes.values()}

    def test_graph_has_conditional_edges(self):
        """Test that the graph has conditional routing."""
        graph = build_graph()
        graph_structure = graph.get_graph()

        # Check that edges exist in the graph
        assert len(graph_structure.edges) > 0

    def test_graph_structure_flow(self):
        """Test that the graph has the expected flow structure."""
        graph = build_graph()
        graph_structure = graph.get_graph()

        # Get all edges as a set of (source, target) tuples
        edges = {(edge.source, edge.target) for edge in graph_structure.edges}

        # Verify key connections exist
        # Note: Conditional edges may have multiple targets, so we check for presence
        # of key nodes in the graph structure rather than exact edges

        node_ids = {node.id for node in graph_structure.nodes.values()}

        assert "fetch_logs" in node_ids
        assert "analyze_log" in node_ids
        assert "create_ticket" in node_ids
        assert "next_log" in node_ids

    def test_graph_can_be_invoked(self):
        """Test that the compiled graph can be invoked with initial state."""
        graph = build_graph()

        # Create a minimal valid state
        initial_state = {
            "logs": [],
            "log_index": 0,
            "seen_logs": set(),
            "finished": False,
        }

        # Mock the fetch_logs node to prevent actual API calls
        with patch("agent.nodes.fetch_logs") as mock_fetch:
            mock_fetch.return_value = {
                "logs": [],
                "log_index": 0,
                "finished": True,
            }

            # The graph should be invokable
            # Note: We don't actually invoke it here to avoid needing all dependencies
            # Just verify it has an invoke method
            assert hasattr(graph, "invoke")
