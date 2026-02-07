import os
import pytest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from agent.graph import build_graph


def make_logs(n=10):
    logs = []
    for i in range(n):
        logs.append({
            "logger": "com.example.myservice.converter.DpItemConverter",
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
        jira_similarity_threshold=0.82,
        jira_direct_log_threshold=0.90,
        jira_partial_log_threshold=0.70,
        jira_search_window_days=365,
        jira_search_max_results=200,
        datadog_service="test-service",
        datadog_limit=50,
        datadog_max_pages=3,
        datadog_timeout=20,
        datadog_logs_url="https://app.datadoghq.eu/logs",
        circuit_breaker_enabled=False,
        fallback_analysis_enabled=False,
        jira_domain="test.atlassian.net",
        jira_user="test@example.com",
        jira_api_token="fake-token",
    )

    logs = make_logs(10)

    # Patch _call_llm_with_circuit_breaker instead of chain.invoke
    # (chain is a Pydantic model that doesn't support mock patching)
    async_mock = AsyncMock(return_value=dummy_llm_json())

    with patch.dict(os.environ, {"AUTO_CREATE_TICKET": "true", "COMMENT_ON_DUPLICATE": "false"}):
        with patch("agent.config.get_config", return_value=cfg), \
             patch("agent.nodes.ticket.get_config", return_value=cfg), \
             patch("agent.performance.get_config", return_value=cfg), \
             patch("agent.nodes.analysis.get_config", return_value=cfg), \
             patch("agent.jira.match.get_config", return_value=cfg), \
             patch("agent.jira.client.get_config", return_value=cfg):
            with patch("agent.nodes.analysis._call_llm_with_circuit_breaker", async_mock):
                # Disable Jira search (simulate failure/skip) so local fingerprinting is decisive
                with patch("agent.jira.client.search", return_value=None), \
                     patch("agent.jira.match.client.search", return_value=None):
                    with patch("agent.jira.client.is_configured", return_value=True), \
                         patch("agent.jira.is_configured", return_value=True):
                        # Patch create_issue at both client and __init__ level
                        # (__init__ imports it as jira_create_issue at load time)
                        with patch("agent.jira.jira_create_issue") as mock_create:
                            mock_create.side_effect = lambda payload: {"key": f"TEST-{mock_create.call_count}"}
                            # Prevent fingerprint persistence during test
                            # Patch fingerprint functions at all import sites
                            with patch("agent.jira.utils.save_processed_fingerprints"), \
                                 patch("agent.jira.utils.load_processed_fingerprints", return_value=set()), \
                                 patch("agent.jira.load_processed_fingerprints", return_value=set()), \
                                 patch("agent.jira.save_processed_fingerprints"), \
                                 patch("agent.nodes.ticket._load_processed_fingerprints", return_value=set()), \
                                 patch("agent.nodes.ticket._save_processed_fingerprints"), \
                                 patch("agent.nodes.ticket._append_audit_log"):
                                graph = build_graph()
                                state = graph.invoke({
                                    "logs": logs,
                                    "log_index": 0,
                                    "seen_logs": set(),
                                    "created_fingerprints": set(),
                                }, {"recursion_limit": 2000})

                                # Only 1 ticket should be created due to duplicate collapse
                                assert mock_create.call_count == 1
