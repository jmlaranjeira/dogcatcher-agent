"""Datadog log fetcher.

Provides a small client function `get_logs` to retrieve logs from the Datadog
Logs v2 Search API, with environment-driven defaults and safe pagination.
All comments and messages are in English for consistency across the project.
"""
import os
import json
from typing import List, Dict, Any, Optional, Tuple

import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from agent.config import get_config
from agent.utils.logger import log_info, log_error

load_dotenv()

# Get configuration
config = get_config()

HEADERS = {
    "DD-API-KEY": config.datadog.api_key,
    "DD-APPLICATION-KEY": config.datadog.app_key,
    "Content-Type": "application/json",
}

MAX_LOG_DETAIL_LENGTH = 300


def _missing_dd_config() -> list[str]:
    missing = []
    if not config.datadog.api_key:
        missing.append("DATADOG_API_KEY")
    if not config.datadog.app_key:
        missing.append("DATADOG_APP_KEY")
    if not config.datadog.site:
        missing.append("DATADOG_SITE")
    return missing


def _coerce_detail(value: Any, fallback: str = "no detailed log") -> str:
    """Return a safe string representation for the `detail` field.

    - Dict/list are JSON-encoded (UTF-8, not ASCII-escaped).
    - None becomes `fallback`.
    - Other types are stringified.
    """
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    if value is None:
        return fallback
    return str(value)


def _build_dd_query(service: str, env: str, statuses_csv: str, extra_csv: str, extra_mode: str) -> Tuple[str, str]:
    """Build Datadog Logs query string and return (query, extra_clause).

    `extra_csv` supports comma-separated terms and `extra_mode` combines them as
    AND/OR. Parentheses are applied to avoid precedence issues.
    """
    statuses = [s.strip() for s in statuses_csv.split(",") if s.strip()]
    if statuses:
        status_part = "(" + " OR ".join([f"status:{s}" for s in statuses]) + ")"
    else:
        status_part = "status:error"

    extra_terms = [t.strip() for t in extra_csv.split(",") if t.strip()]
    mode = (extra_mode or "AND").upper()
    if extra_terms:
        joiner = " OR " if mode == "OR" else " AND "
        extra_clause = " (" + joiner.join(extra_terms) + ")"
    else:
        extra_clause = ""

    query = f"service:{service} env:{env} {status_part}{extra_clause}".strip()
    return query, extra_clause


# Fetch error logs from Datadog based on service and environment parameters.
def get_logs(service=None, env=None, hours_back=None, limit=None):
    service = config.datadog.service if service is None else service
    env = config.datadog.env if env is None else env
    hours_back = config.datadog.hours_back if hours_back is None else hours_back
    limit = config.datadog.limit if limit is None else limit

    log_info("Datadog query parameters", 
             service=service, 
             env=env, 
             hours_back=hours_back, 
             limit=limit, 
             max_pages=config.datadog.max_pages)

    now = datetime.utcnow()
    start = now - timedelta(hours=hours_back)

    # --- config validation ---
    missing = _missing_dd_config()
    if missing:
        log_error("Missing Datadog configuration", missing_fields=missing)
        return []
    # --- end validation ---

    base_url = f"https://api.{config.datadog.site}/api/v2/logs/events/search"

    # Build final query (and keep the extra clause for optional fallback)
    dd_query, extra_clause = _build_dd_query(
        service=service,
        env=env,
        statuses_csv=config.datadog.statuses,
        extra_csv=config.datadog.query_extra,
        extra_mode=config.datadog.query_extra_mode,
    )
    log_info("Datadog query built", query=dd_query)

    def _fetch_page(cursor: str | None = None):
        payload = {
            "filter": {
                "from": start.isoformat() + "Z",
                "to": now.isoformat() + "Z",
                "query": dd_query,
            },
            "page": {"limit": limit},
        }
        if cursor:
            payload["page"]["cursor"] = cursor
        try:
            resp = requests.post(base_url, json=payload, headers=HEADERS, timeout=config.datadog.timeout)
            resp.raise_for_status()
            data = resp.json()
            next_cursor = None
            try:
                next_cursor = ((data or {}).get("meta") or {}).get("page", {}).get("after")
            except Exception:
                next_cursor = None
            return data.get("data", []) or [], next_cursor
        except requests.RequestException as e:
            log_error("Datadog request failed", error=str(e))
            return [], None

    # Pagination loop (bounded by DATADOG_MAX_PAGES)
    results = []
    page = 0
    cursor = None
    while True:
        page += 1
        data, cursor = _fetch_page(cursor)
        if not data:
            break
        for log in data:
            attr = log.get("attributes", {})
            msg = attr.get("message", "<no message>")
            logger_name = attr.get("attributes", {}).get("logger", {}).get("name", "unknown.logger")
            thread_name = attr.get("attributes", {}).get("logger", {}).get("thread_name", "unknown.thread")
            logger_name = str(logger_name) if logger_name is not None else "unknown.logger"
            thread_name = str(thread_name) if thread_name is not None else "unknown.thread"
            detail = _coerce_detail(
                attr.get("attributes", {}).get("properties", {}).get("Log", "no detailed log")
            )
            if len(detail) > MAX_LOG_DETAIL_LENGTH:
                detail = detail[:MAX_LOG_DETAIL_LENGTH] + "... [truncated]"

            results.append({
                "logger": logger_name,
                "thread": thread_name,
                "message": msg,
                "timestamp": attr.get("timestamp"),
                "detail": detail,
            })
        if not cursor or page >= config.datadog.max_pages:
            break

    # If no results and we used an extra clause, retry once without it to aid diagnosis
    if not results and extra_clause:
        log_info("No results with extra clause; retrying once without DATADOG_QUERY_EXTRA")
        def _fetch_page_no_extra(cursor: str | None = None):
            payload = {
                "filter": {
                    "from": start.isoformat() + "Z",
                    "to": now.isoformat() + "Z",
                    "query": f"service:{service} env:{env} {extra_clause}".strip() if not extra_clause else f"service:{service} env:{env} {extra_clause}".strip(),
                },
                "page": {"limit": limit},
            }
            if cursor:
                payload["page"]["cursor"] = cursor
            try:
                resp = requests.post(base_url, json=payload, headers=HEADERS, timeout=config.datadog.timeout)
                resp.raise_for_status()
                data = resp.json()
                next_cursor = None
                try:
                    next_cursor = ((data or {}).get("meta") or {}).get("page", {}).get("after")
                except Exception:
                    next_cursor = None
                return data.get("data", []) or [], next_cursor
            except requests.RequestException as e:
                log_error("Datadog fallback request failed", error=str(e))
                return [], None

        # One-page probe (we only need to know if there are any logs without the extra)
        data_no_extra, _ = _fetch_page_no_extra(None)
        log_info("Fallback query results", 
                 logs_found=len(data_no_extra), 
                 suggestion="relax DATADOG_QUERY_EXTRA or set DATADOG_QUERY_EXTRA_MODE=OR if appropriate")

    log_info("Datadog logs collected", total_logs=len(results))
    return results


if __name__ == "__main__":
    logs = get_logs()
    for i, log in enumerate(logs, start=1):
        print(f"\nLog #{i}")
        print(f"Logger  : {log['logger']}")
        print(f"Thread  : {log['thread']}")
        print(f"Message : {log['message']}")
        print(f"Detail  : {log['detail']}")
        print("-" * 60)