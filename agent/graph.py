"""Graph definition for the Datadog → LLM → Jira pipeline.

This module wires the processing flow using LangGraph. The state is a plain
dictionary carrying the current log, LLM outputs, and control flags.
"""

from __future__ import annotations

from typing import Any, Dict

from agent.state import GraphState
from agent.utils.logger import log_info, log_debug

from langgraph.graph import END, StateGraph
from agent.nodes import analyze_log, fetch_logs
from agent.jira.utils import normalize_log_message
from agent.nodes import create_ticket as create_jira_ticket


def analyze_log_wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
    """Load the current log, skip local duplicates, then call `analyze_log`.

    Local duplicate detection uses a simple fingerprint: ``logger|thread|message``
    for the current item. If the log list is empty or the index is out of range,
    mark the graph as finished gracefully.
    """
    if "seen_logs" not in state:
        state["seen_logs"] = set()
    if "created_fingerprints" not in state:
        state["created_fingerprints"] = set()

    logs = state.get("logs", [])
    idx = state.get("log_index", 0)
    if not logs or idx >= len(logs):
        log_info("No logs to analyze; finishing.")
        return {**state, "finished": True, "create_ticket": False}

    log = logs[idx]
    raw_msg = log.get('message', '<no message>')
    norm_msg = normalize_log_message(raw_msg)
    # Key ignores thread to avoid duplicates across worker threads
    log_key = (
        f"{log.get('logger', 'unknown')}|"
        f"{norm_msg or raw_msg}"
    )
    log_debug("Analyzing log", log_index=state.get('log_index'), log_key=log_key)

    # Skip logs already analyzed in this run
    if log_key in state["seen_logs"]:
        log_debug("Skipping duplicate log", log_index=state.get('log_index'), log_key=log_key)
        return {**state, "skipped_duplicate": True, "create_ticket": False}

    state["seen_logs"].add(log_key)

    return analyze_log({
        **state,
        "log_message": raw_msg,
        "log_data": log,
        "seen_logs": state["seen_logs"],
    })


def next_log(state: Dict[str, Any]) -> Dict[str, Any]:
    """Advance to the next log or finish if the end is reached."""
    logs = state.get("logs", [])
    index = state.get("log_index", 0) + 1

    if index >= len(logs):
        return {**state, "finished": True}

    log = logs[index]
    raw_msg = log.get('message', '<no message>')
    norm_msg = normalize_log_message(raw_msg)
    log_key = (
        f"{log.get('logger', 'unknown')}|"
        f"{norm_msg or raw_msg}"
    )

    if log_key in state.get("seen_logs", set()):
        return {**state, "log_index": index, "skipped_duplicate": True}

    return {
        **state,
        "log_index": index,
        "log_message": raw_msg,
        "log_data": log,
    }


def build_graph():
    """Compile and return the LangGraph graph for this pipeline."""
    builder = StateGraph(GraphState)

    builder.set_entry_point("fetch_logs")
    builder.add_node("fetch_logs", fetch_logs)
    builder.add_edge("fetch_logs", "analyze_log")

    builder.add_node("analyze_log", analyze_log_wrapper)
    builder.add_node("create_ticket", create_jira_ticket)

    builder.add_conditional_edges(
        "analyze_log",
        lambda s: "create_ticket" if s.get("create_ticket") else "next_log",
        {"create_ticket": "create_ticket", "next_log": "next_log"},
    )

    builder.add_edge("create_ticket", "next_log")

    builder.add_node("next_log", next_log)
    builder.add_conditional_edges(
        "next_log",
        lambda s: END if s.get("finished") else "analyze_log",
        {END: END, "analyze_log": "analyze_log"},
    )

    return builder.compile()
