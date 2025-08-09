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

load_dotenv()

DATADOG_API_KEY = os.getenv("DATADOG_API_KEY")
DATADOG_APP_KEY = os.getenv("DATADOG_APP_KEY")
DATADOG_SITE = os.getenv("DATADOG_SITE", "datadoghq.eu")

# Optional runtime configuration via environment
DATADOG_SERVICE = os.getenv("DATADOG_SERVICE", "dehnproject")
DATADOG_ENV = os.getenv("DATADOG_ENV", "dev")
try:
    DATADOG_HOURS_BACK = int(os.getenv("DATADOG_HOURS_BACK", "24"))
except Exception:
    DATADOG_HOURS_BACK = 24
try:
    DATADOG_LIMIT = int(os.getenv("DATADOG_LIMIT", "10"))
except Exception:
    DATADOG_LIMIT = 10
try:
    DATADOG_MAX_PAGES = int(os.getenv("DATADOG_MAX_PAGES", "1"))  # pagination safeguard
except Exception:
    DATADOG_MAX_PAGES = 1
try:
    DATADOG_TIMEOUT = int(os.getenv("DATADOG_TIMEOUT", "15"))  # seconds
except Exception:
    DATADOG_TIMEOUT = 15
# Extra query terms appended to Datadog filter (e.g., status:error OR status:critical)
DATADOG_QUERY_EXTRA = os.getenv("DATADOG_QUERY_EXTRA", "")
# Default statuses; keep classic 'error' unless overridden via DATADOG_QUERY_EXTRA
DATADOG_STATUSES = os.getenv("DATADOG_STATUSES", "error")

HEADERS = {
    "DD-API-KEY": DATADOG_API_KEY,
    "DD-APPLICATION-KEY": DATADOG_APP_KEY,
    "Content-Type": "application/json",
}

MAX_LOG_DETAIL_LENGTH = 300


def _missing_dd_config() -> list[str]:
    missing = []
    if not DATADOG_API_KEY:
        missing.append("DATADOG_API_KEY")
    if not DATADOG_APP_KEY:
        missing.append("DATADOG_APP_KEY")
    if not DATADOG_SITE:
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
    service = DATADOG_SERVICE if service is None else service
    env = DATADOG_ENV if env is None else env
    hours_back = DATADOG_HOURS_BACK if hours_back is None else hours_back
    limit = DATADOG_LIMIT if limit is None else limit

    print(
        f"🔎 Datadog query → service={service}, env={env}, hours_back={hours_back}, limit={limit}, "
        f"max_pages={DATADOG_MAX_PAGES}"
    )

    now = datetime.utcnow()
    start = now - timedelta(hours=hours_back)

    # --- config validation ---
    missing = _missing_dd_config()
    if missing:
        print(f"❌ Missing Datadog configuration: {', '.join(missing)}. Returning no logs.")
        return []
    # --- end validation ---

    base_url = f"https://api.{DATADOG_SITE}/api/v2/logs/events/search"

    # Build final query (and keep the extra clause for optional fallback)
    extra_mode = os.getenv("DATADOG_QUERY_EXTRA_MODE", "AND")
    dd_query, extra_clause = _build_dd_query(
        service=service,
        env=env,
        statuses_csv=DATADOG_STATUSES,
        extra_csv=DATADOG_QUERY_EXTRA,
        extra_mode=extra_mode,
    )
    print(f"🔎 dd_query = {dd_query}")

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
            resp = requests.post(base_url, json=payload, headers=HEADERS, timeout=DATADOG_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            next_cursor = None
            try:
                next_cursor = ((data or {}).get("meta") or {}).get("page", {}).get("after")
            except Exception:
                next_cursor = None
            return data.get("data", []) or [], next_cursor
        except requests.RequestException as e:
            print(f"❌ Datadog request failed: {e}")
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
        if not cursor or page >= DATADOG_MAX_PAGES:
            break

    # If no results and we used an extra clause, retry once without it to aid diagnosis
    if not results and extra_clause:
        print("🧪 No results with extra clause; retrying once without DATADOG_QUERY_EXTRA…")
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
                resp = requests.post(base_url, json=payload, headers=HEADERS, timeout=DATADOG_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                next_cursor = None
                try:
                    next_cursor = ((data or {}).get("meta") or {}).get("page", {}).get("after")
                except Exception:
                    next_cursor = None
                return data.get("data", []) or [], next_cursor
            except requests.RequestException as e:
                print(f"❌ Datadog fallback request failed: {e}")
                return [], None

        # One-page probe (we only need to know if there are any logs without the extra)
        data_no_extra, _ = _fetch_page_no_extra(None)
        print(f"🧪 Fallback (no extra) returned {len(data_no_extra)} logs on first page.")
        if data_no_extra:
            print("💡 Suggestion: relax DATADOG_QUERY_EXTRA or set DATADOG_QUERY_EXTRA_MODE=OR if appropriate.")

    print(f"🪵 Collected {len(results)} logs from Datadog")
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