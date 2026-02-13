"""Jira payload builder — pure formatting, no side effects.

Extracts ticket payload construction from ``agent.nodes.ticket`` into a
reusable, easily testable module.  The :class:`JiraPayloadBuilder` receives
an ``AppConfig`` at construction time and exposes deterministic formatting
methods that depend only on their inputs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.jira.utils import (
    compute_fingerprint,
    compute_loghash,
    normalize_log_message,
    priority_name_from_severity,
    sanitize_for_jira,
)


@dataclass
class TicketPayload:
    """Jira ticket payload with metadata."""

    payload: Dict[str, Any]
    title: str
    description: str
    labels: List[str]
    fingerprint: str


class JiraPayloadBuilder:
    """Builds Jira ticket payloads.  Pure functions, no side effects.

    Parameters
    ----------
    config:
        Application configuration object (``agent.config.Config``).
    """

    def __init__(self, config) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        state: Dict[str, Any],
        title: str,
        description: str,
        *,
        extra_labels: Optional[List[str]] = None,
    ) -> TicketPayload:
        """Assemble a complete Jira ticket payload.

        Parameters
        ----------
        state:
            Current agent state (log_data, error_type, etc.).
        title:
            Raw ticket title from LLM analysis.
        description:
            Raw ticket description from LLM analysis.
        extra_labels:
            Additional labels to include (e.g. ``["async-created"]``).
        """
        fingerprint = self.compute_fingerprint(state)
        full_description = self.build_enhanced_description(state, description)
        labels = self.build_labels(state, fingerprint, extra_labels=extra_labels)
        clean_title = self.clean_title(title, state.get("error_type"))

        payload: Dict[str, Any] = {
            "fields": {
                "project": {"key": self.config.jira_project_key},
                "summary": clean_title,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"text": full_description, "type": "text"}],
                        }
                    ],
                },
                "issuetype": {"name": "Bug"},
                "labels": labels,
                "priority": {
                    "name": priority_name_from_severity(state.get("severity"))
                },
            }
        }

        # Optional team custom field injection
        self._inject_team_field(payload, state)

        return TicketPayload(
            payload=payload,
            title=clean_title,
            description=full_description,
            labels=labels,
            fingerprint=fingerprint,
        )

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def build_enhanced_description(
        self, state: Dict[str, Any], description: str
    ) -> str:
        """Build enhanced description with log context, MDC fields, and Datadog links."""
        log_data = state.get("log_data", {})
        win = state.get("window_hours", 48)
        raw_msg = log_data.get("message", "")
        fp_source = f"{log_data.get('logger', '')}|{raw_msg}"
        occ = (state.get("fp_counts") or {}).get(fp_source, 1)

        # Extract MDC fields from log attributes (if available)
        attributes = log_data.get("attributes", {})
        request_id = (
            attributes.get("requestId")
            or attributes.get("request_id")
            or log_data.get("requestId", "")
        )
        user_id = (
            attributes.get("userId")
            or attributes.get("user_id")
            or log_data.get("userId", "")
        )
        error_type = (
            attributes.get("errorType")
            or attributes.get("error_type")
            or log_data.get("errorType", "")
        )

        # Build basic context info
        extra_info = f"""
---
\U0001f552 Timestamp: {log_data.get('timestamp', 'N/A')}
\U0001f9e9 Logger: {log_data.get('logger', 'N/A')}
\U0001f9f5 Thread: {log_data.get('thread', 'N/A')}
\U0001f4dd Original Log: {sanitize_for_jira(log_data.get('message', 'N/A'))}
\U0001f50d Detail: {sanitize_for_jira(log_data.get('detail', 'N/A'))}
\U0001f4c8 Occurrences in last {win}h: {occ}"""

        # Add MDC context if available
        mdc_context: list[str] = []
        if request_id:
            mdc_context.append(f"\U0001f4cb Request ID: {request_id}")
        if user_id:
            mdc_context.append(f"\U0001f464 User ID: {user_id}")
        if error_type:
            mdc_context.append(f"\U0001f3f7\ufe0f Error Type: {error_type}")

        if mdc_context:
            extra_info += "\n---\n" + "\n".join(mdc_context)

        # Build Datadog trace links
        datadog_links = self.build_datadog_links(log_data, request_id, user_id)
        if datadog_links:
            extra_info += f"\n---\n\U0001f517 Datadog Links:\n{datadog_links}"

        return f"{description.strip()}\n{extra_info}"

    def build_datadog_links(
        self, log_data: Dict[str, Any], request_id: str, user_id: str
    ) -> str:
        """Build Datadog query links for tracing."""
        links: list[str] = []
        base_url = self.config.datadog_logs_url
        service = self.config.datadog_service

        # Link to full request trace (if requestId available)
        if request_id:
            query = f"service:{service} @requestId:{request_id}"
            encoded_query = (
                query.replace(" ", "%20").replace(":", "%3A").replace("@", "%40")
            )
            links.append(f"\u2022 Request Trace: {base_url}?query={encoded_query}")

        # Link to user activity (if userId available)
        if user_id:
            query = f"service:{service} @userId:{user_id}"
            encoded_query = (
                query.replace(" ", "%20").replace(":", "%3A").replace("@", "%40")
            )
            links.append(f"\u2022 User Activity: {base_url}?query={encoded_query}")

        # Link to similar errors (by logger)
        logger = log_data.get("logger", "")
        if logger:
            query = f"service:{service} @logger_name:{logger} status:error"
            encoded_query = (
                query.replace(" ", "%20").replace(":", "%3A").replace("@", "%40")
            )
            links.append(f"\u2022 Similar Errors: {base_url}?query={encoded_query}")

        return "\n".join(links)

    def build_labels(
        self,
        state: Dict[str, Any],
        fingerprint: str,
        *,
        extra_labels: Optional[List[str]] = None,
    ) -> List[str]:
        """Build labels for the ticket."""
        labels = ["datadog-log"]

        if extra_labels:
            labels.extend(extra_labels)

        # Add loghash label
        try:
            loghash = compute_loghash((state.get("log_data") or {}).get("message", ""))
            if loghash:
                labels.append(f"loghash-{loghash}")
        except Exception:
            pass

        # Add error_type label (enables duplicate detection via error_type label search)
        etype = (state.get("error_type") or "").strip().lower()
        if etype and etype != "unknown":
            labels.append(etype)

        # Add aggregation labels based on error type
        if self.config.aggregate_email_not_found and etype == "email-not-found":
            labels.append("aggregate-email-not-found")

        if self.config.aggregate_kafka_consumer and etype == "kafka-consumer":
            labels.append("aggregate-kafka-consumer")

        return labels

    def clean_title(self, title: str, error_type: Optional[str]) -> str:
        """Clean and format the ticket title."""
        base_title = title.replace("**", "").strip()

        # Handle aggregation cases
        if error_type == "email-not-found" and self.config.aggregate_email_not_found:
            base_title = "Investigate Email Not Found errors (aggregated)"
        elif error_type == "kafka-consumer" and self.config.aggregate_kafka_consumer:
            base_title = "Investigate Kafka Consumer errors (aggregated)"

        # Truncate if too long
        max_title = self.config.max_title_length
        if len(base_title) > max_title:
            base_title = base_title[: max_title - 1] + "\u2026"

        # Add prefix
        prefix = "[Datadog]" + (f"[{error_type}]" if error_type else "")
        return f"{prefix} {base_title}".strip()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_fingerprint(state: Dict[str, Any]) -> str:
        """Compute a stable fingerprint for the log entry."""
        log_data = state.get("log_data", {})
        raw_msg = log_data.get("message", "")
        error_type = state.get("error_type", "unknown")
        return compute_fingerprint(error_type, raw_msg)

    def _inject_team_field(
        self, payload: Dict[str, Any], state: Dict[str, Any]
    ) -> None:
        """Inject the optional Jira team custom field into the payload."""
        try:
            team_id = state.get("team_id")
            if team_id:
                from agent.team_loader import load_teams_config

                tcfg = load_teams_config()
                if tcfg:
                    team = tcfg.get_team(team_id)
                    field_id = tcfg.jira_team_field_id
                    field_val = team.jira_team_field_value if team else None
                    if field_id and field_val:
                        payload["fields"][field_id] = [{"value": field_val}]
            else:
                team_field_id = os.getenv("JIRA_TEAM_FIELD_ID")
                team_field_value = os.getenv("JIRA_TEAM_VALUE")
                if team_field_id and team_field_value:
                    payload["fields"][team_field_id] = [{"value": team_field_value}]
        except Exception:
            # Do not fail payload building if optional field injection fails
            pass
