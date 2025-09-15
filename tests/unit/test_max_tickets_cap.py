import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from agent.nodes.ticket import _execute_ticket_creation, TicketPayload


def make_state():
    return {
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
    cfg = SimpleNamespace(auto_create_ticket=True, max_tickets_per_run=max_cap)

    with patch("agent.config.get_config", return_value=cfg):
        with patch("agent.jira.create_ticket") as mock_create:
            mock_create.side_effect = lambda s: {**s, "jira_response_key": f"TEST-{s.get('_tickets_created_in_run', 0)+1}"}

            state = make_state()
            payload = TicketPayload(
                payload={"fields": {"summary": "Demo"}},
                title="Demo",
                description="Demo desc",
                labels=["datadog-log"],
                fingerprint="fp-123",
            )

            # Try to create well beyond the cap
            for _ in range(max_cap + 3):
                state = _execute_ticket_creation(state, payload)

            assert mock_create.call_count == expected_calls
            # Counter should equal number of successful creations
            assert state.get("_tickets_created_in_run") == expected_calls

