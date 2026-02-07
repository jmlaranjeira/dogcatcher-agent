"""Fetch/prep node (fetch_logs, fp_counts, window hours)."""

from typing import Dict, Any


def fetch_logs(state: Dict[str, Any]) -> Dict[str, Any]:
    if state.get("skipped_duplicate"):
        return state

    logs = state.get("logs", [])

    # Compute per-run fingerprint counts once (logger|thread|message)
    if "fp_counts" not in state:
        counts = {}
        for lg in logs:
            k = f"{lg.get('logger','')}|{lg.get('thread','')}|{lg.get('message','')}"
            counts[k] = counts.get(k, 0) + 1
        state["fp_counts"] = counts
        try:
            import os as _os

            state["window_hours"] = int(_os.getenv("DATADOG_HOURS_BACK", "48"))
        except Exception:
            state["window_hours"] = 48

    index = state.get("log_index", 0)
    if index < len(logs):
        log = logs[index]
        return {**state, "log_message": log.get("message", ""), "log_data": log or {}}
    else:
        return {**state, "log_message": "", "log_data": {}}
