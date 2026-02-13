"""Fetch/prep node (fetch_logs, fp_counts, window hours)."""

from typing import Dict, Any

from agent.run_config import get_run_config


def fetch_logs(state: Dict[str, Any]) -> Dict[str, Any]:
    if state.get("skipped_duplicate"):
        return state

    logs = state.get("logs", [])

    # Compute per-run fingerprint counts once (logger|message)
    if "fp_counts" not in state:
        counts = {}
        for lg in logs:
            k = f"{lg.get('logger','')}|{lg.get('message','')}"
            counts[k] = counts.get(k, 0) + 1
        state["fp_counts"] = counts
        rc = get_run_config(state)
        state["window_hours"] = rc.datadog_hours_back

    index = state.get("log_index", 0)
    if index < len(logs):
        log = logs[index]
        return {**state, "log_message": log.get("message", ""), "log_data": log or {}}
    else:
        return {**state, "log_message": "", "log_data": {}}
