import pytest
from types import SimpleNamespace
from unittest.mock import patch

from agent.graph import build_graph


class DummyLLMResponse:
    def __init__(self, content: str):
        self.content = content


def make_logs(n=10):
    logs = []
    for i in range(n):
        logs.append({
            "logger": "org.devpoint.dehnproject.converter.DpItemConverter",
            "thread": f"http-nio-1025-exec-{i}",
            "message": f"Failed to get file size by name {i:04d}_BlobX.DPplan, Cause: Status code 404, (BlobNotFound)",
            "timestamp": f"2025-09-15T10:{i:02d}:00Z",
            "detail": "sample"
        })
    return logs


def dummy_llm_json():
    return (
        '{"error_type": "blob-not-found", '
        '"create_ticket": true, '
        '"ticket_title": "Investigate Blob Not Found Error", '
        '"ticket_description": "Problem summary...", '
        '"severity": "low"}'
    )


def test_end_to_end_cap_and_duplicate_collapse():
    # Config: allow up to 5 tickets, but duplicates should collapse to 1
    cfg = SimpleNamespace(
        auto_create_ticket=True,
        max_tickets_per_run=5,
        jira_project_key="TEST",
        aggregate_email_not_found=False,
        aggregate_kafka_consumer=False,
        max_title_length=120,
        persist_sim_fp=False,
        comment_on_duplicate=False,
        comment_cooldown_minutes=0,
    )

    logs = make_logs(10)

    with patch("agent.config.get_config", return_value=cfg):
        # Patch LLM chain to deterministic JSON
        with patch("agent.nodes.analysis.chain.invoke", return_value=DummyLLMResponse(dummy_llm_json())):
            # Disable Jira search (simulate failure/skip) so local fingerprinting is decisive
            with patch("agent.jira.client.search", return_value=None):
                with patch("agent.jira.client.is_configured", return_value=True):
                    with patch("agent.jira.client.create_issue") as mock_create:
                        mock_create.side_effect = lambda payload: {"key": f"TEST-{mock_create.call_count+1}"}

                        graph = build_graph()
                        state = graph.invoke({
                            "logs": logs,
                            "log_index": 0,
                            "seen_logs": set(),
                            "created_fingerprints": set(),
                        }, {"recursion_limit": 2000})

                        # Only 1 ticket should be created due to duplicate collapse
                        assert mock_create.call_count == 1

