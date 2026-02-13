"""Build the contextual log string sent to the LLM for analysis.

Centralises prompt construction so that both the sync (``analysis.py``)
and async (``analysis_async.py``) modules share a single implementation.

The function is **pure** — no side effects, no API calls — making it
trivial to unit-test.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_contextual_log(
    log_data: Dict[str, Any],
    state: Dict[str, Any],
    config: Any,
    *,
    team_severity_rules: Optional[Dict[str, str]] = None,
) -> str:
    """Build an enriched contextual log string for LLM analysis.

    Parameters
    ----------
    log_data:
        Individual log entry (keys: message, logger, thread, detail).
    state:
        Current graph state.  Used to look up ``fp_counts`` (occurrence
        counts computed by the fetch node) and ``window_hours``.
    config:
        Application config object (``get_config()``).  Reads
        ``datadog_service`` and ``datadog_env``.
    team_severity_rules:
        Optional team-specific ``error_type → severity`` mapping.
        When provided, included as ``[Severity hints]`` so the LLM can
        calibrate its severity output.

    Returns
    -------
    str
        Multi-line string suitable for the ``{log_message}`` template
        variable in the LLM prompt.
    """
    service = getattr(config, "datadog_service", None) or "unknown"
    env = getattr(config, "datadog_env", None) or "unknown"

    logger = log_data.get("logger") or "unknown.logger"
    thread = log_data.get("thread") or "unknown.thread"
    msg = log_data.get("message") or "<no message>"
    detail = log_data.get("detail") or "<no detail>"

    lines = [
        f"[Service]: {service}",
        f"[Environment]: {env}",
        f"[Logger]: {logger}",
        f"[Thread]: {thread}",
        f"[Message]: {msg}",
        f"[Detail]: {detail}",
    ]

    # Occurrence count (computed by fetch node in state["fp_counts"])
    fp_counts: Optional[Dict[str, int]] = state.get("fp_counts")
    if fp_counts is not None:
        fp_key = f"{logger}|{msg}"
        count = fp_counts.get(fp_key, 1)
        window_hours = state.get("window_hours", "?")
        lines.append(f"[Occurrences in last {window_hours}h]: {count}")

    # Team-specific severity hints (multi-tenant only)
    if team_severity_rules:
        hints = ", ".join(f"{k}={v}" for k, v in sorted(team_severity_rules.items()))
        lines.append(f"[Severity hints]: {hints}")

    return "\n".join(lines)
