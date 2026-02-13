import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from agent.run_config import RunConfig
from agent.nodes.ticket import _execute_ticket_creation, TicketPayload


def make_state(max_tickets_per_run=3):
    return {
        "run_config": RunConfig(
            jira_project_key="TEST",
            auto_create_ticket=True,
            max_tickets_per_run=max_tickets_per_run,
            datadog_service="test-service",
        ),
        "log_data": {
            "logger": "org.example.Logger",
            "thread": "worker-1",
            "message": "Failed to get file size by name 1111_foo.DPplan, Cause: Status code 404, (BlobNotFound)",
            "timestamp": "2025-09-15T10:00:00Z",
            "detail": "demo",
        },
        "ticket_title": "Demo",
        "ticket_description": "Demo desc",
        "severity": "low",
        "_tickets_created_in_run": 0,
        "created_fingerprints": set(),
    }


@pytest.mark.parametrize("max_cap,expected_calls", [(1, 1), (2, 2), (5, 5)])
def test_per_run_cap_strictly_enforced(max_cap, expected_calls):
    """Test that the per-run ticket creation cap is strictly enforced.

    The cap should prevent additional ticket creation attempts once the
    max_tickets_per_run limit is reached, regardless of how many times
    _execute_ticket_creation is called.
    """
    # Patch the actual create_jira_ticket function in ticket.py
    cfg = SimpleNamespace(auto_create_ticket=True, max_tickets_per_run=max_cap)
    with patch("agent.nodes.ticket.get_config", return_value=cfg):
        # Patch filesystem functions to prevent side effects
        with patch(
            "agent.nodes.ticket._load_processed_fingerprints", return_value=set()
        ):
            with patch("agent.nodes.ticket._save_processed_fingerprints"):
                with patch("agent.nodes.ticket._invoke_patchy"):
                    # Patch the Jira client functions called by agent.jira.create_ticket
                    with patch("agent.jira.is_configured", return_value=True):
                        with patch(
                            "agent.jira.load_processed_fingerprints", return_value=set()
                        ):
                            with patch("agent.jira.save_processed_fingerprints"):
                                # Mock find_similar_ticket to return no duplicates
                                with patch(
                                    "agent.jira.find_similar_ticket",
                                    return_value=(None, 0.0, ""),
                                ):
                                    # Patch os.getenv to enable auto_create in agent.jira module
                                    with patch("agent.jira.os.getenv") as mock_getenv:
                                        # Return "true" for AUTO_CREATE_TICKET, empty string for others
                                        mock_getenv.side_effect = (
                                            lambda key, default="": (
                                                "true"
                                                if key == "AUTO_CREATE_TICKET"
                                                else default
                                            )
                                        )

                                        # Mock Jira domain for URL construction
                                        with patch(
                                            "agent.jira.get_jira_domain",
                                            return_value="jira.example.com",
                                        ):
                                            with patch(
                                                "agent.jira.get_jira_project_key",
                                                return_value="TEST",
                                            ):
                                                with patch(
                                                    "agent.jira.jira_create_issue"
                                                ) as mock_jira_api:
                                                    # Mock the actual Jira API call
                                                    mock_jira_api.side_effect = lambda p: {
                                                        "key": f"TEST-{len(mock_jira_api.call_args_list)}",
                                                        "id": f"{len(mock_jira_api.call_args_list)}",
                                                    }

                                                    state = make_state(
                                                        max_tickets_per_run=max_cap
                                                    )

                                                    # Try to create well beyond the cap
                                                    # Use different log messages for each iteration to get different fingerprints
                                                    for i in range(max_cap + 3):
                                                        # Update log_data to get a unique fingerprint
                                                        state["log_data"][
                                                            "message"
                                                        ] = f"Error message {i}"
                                                        state["ticket_title"] = (
                                                            f"Demo {i}"
                                                        )
                                                        state["ticket_description"] = (
                                                            f"Demo desc {i}"
                                                        )

                                                        payload = TicketPayload(
                                                            payload={
                                                                "fields": {
                                                                    "summary": f"Demo {i}"
                                                                }
                                                            },
                                                            title=f"Demo {i}",
                                                            description=f"Demo desc {i}",
                                                            labels=["datadog-log"],
                                                            fingerprint=f"fp-{i}",
                                                        )
                                                        state = (
                                                            _execute_ticket_creation(
                                                                state, payload
                                                            )
                                                        )

                                                    # Verify the Jira API was only called up to the cap
                                                    assert (
                                                        mock_jira_api.call_count
                                                        == expected_calls
                                                    )
                                                    # Counter should equal number of successful creations
                                                    assert (
                                                        state.get(
                                                            "_tickets_created_in_run"
                                                        )
                                                        == expected_calls
                                                    )
