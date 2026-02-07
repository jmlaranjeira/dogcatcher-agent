"""Async Datadog log fetcher using httpx.

Provides async functions to retrieve logs from the Datadog Logs v2 Search API,
with environment-driven defaults and safe pagination using connection pooling.
"""

from __future__ import annotations
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

import httpx

from agent.config import get_config
from agent.utils.logger import log_info, log_error, log_debug
from agent.performance import get_performance_metrics

MAX_LOG_DETAIL_LENGTH = 300


class AsyncDatadogClient:
    """Async Datadog API client with connection pooling."""

    def __init__(self):
        """Initialize async client with configuration."""
        self.config = get_config()
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Context manager entry - creates HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.datadog_timeout),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> Dict[str, str]:
        """Generate authorization headers.

        Returns:
            Headers dictionary with API keys
        """
        return {
            "DD-API-KEY": self.config.datadog_api_key,
            "DD-APPLICATION-KEY": self.config.datadog_app_key,
            "Content-Type": "application/json",
        }

    def is_configured(self) -> bool:
        """Check if Datadog is properly configured.

        Returns:
            True if all required config present
        """
        return all(
            [
                self.config.datadog_api_key,
                self.config.datadog_app_key,
                self.config.datadog_site,
            ]
        )

    async def fetch_page(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch a single page of logs from Datadog.

        Args:
            query: Datadog query string
            start: Start time for the search
            end: End time for the search
            limit: Maximum logs per page
            cursor: Pagination cursor

        Returns:
            Tuple of (logs list, next cursor or None)
        """
        if not self.is_configured():
            log_error("Datadog not configured")
            return [], None

        if not self._client:
            log_error("AsyncDatadogClient not initialized - use 'async with' context")
            return [], None

        url = f"https://api.{self.config.datadog_site}/api/v2/logs/events/search"

        payload: Dict[str, Any] = {
            "filter": {
                "from": start.isoformat() + "Z",
                "to": end.isoformat() + "Z",
                "query": query,
            },
            "page": {"limit": limit},
        }
        if cursor:
            payload["page"]["cursor"] = cursor

        try:
            resp = await self._client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

            # Extract next cursor
            next_cursor = None
            try:
                next_cursor = (
                    ((data or {}).get("meta") or {}).get("page", {}).get("after")
                )
            except Exception:
                next_cursor = None

            return data.get("data", []) or [], next_cursor

        except httpx.HTTPError as e:
            log_error("Datadog async request failed", error=str(e))
            return [], None


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


def _build_dd_query(
    service: str, env: str, statuses_csv: str, extra_csv: str, extra_mode: str
) -> Tuple[str, str]:
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


def _parse_log_entry(log: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a raw Datadog log entry into a normalized format.

    Args:
        log: Raw log entry from Datadog API

    Returns:
        Normalized log dict with message, logger, thread, detail, timestamp
    """
    attr = log.get("attributes", {})
    msg = attr.get("message", "<no message>")
    logger_name = (
        attr.get("attributes", {}).get("logger", {}).get("name", "unknown.logger")
    )
    thread_name = (
        attr.get("attributes", {})
        .get("logger", {})
        .get("thread_name", "unknown.thread")
    )
    logger_name = str(logger_name) if logger_name is not None else "unknown.logger"
    thread_name = str(thread_name) if thread_name is not None else "unknown.thread"

    detail = _coerce_detail(
        attr.get("attributes", {}).get("properties", {}).get("Log", "no detailed log")
    )
    if len(detail) > MAX_LOG_DETAIL_LENGTH:
        detail = detail[:MAX_LOG_DETAIL_LENGTH] + "... [truncated]"

    return {
        "logger": logger_name,
        "thread": thread_name,
        "message": msg,
        "timestamp": attr.get("timestamp"),
        "detail": detail,
    }


async def get_logs_async(
    service: Optional[str] = None,
    env: Optional[str] = None,
    hours_back: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch error logs from Datadog asynchronously.

    Args:
        service: Service name (defaults to config)
        env: Environment (defaults to config)
        hours_back: Hours to look back (defaults to config)
        limit: Max logs per page (defaults to config)

    Returns:
        List of normalized log entries
    """
    # Start performance timing
    metrics = get_performance_metrics()
    metrics.start_timer("get_logs_async")

    config = get_config()
    service = config.datadog_service if service is None else service
    env = config.datadog_env if env is None else env
    hours_back = config.datadog_hours_back if hours_back is None else hours_back
    limit = config.datadog_limit if limit is None else limit

    log_info(
        "Datadog async query parameters",
        service=service,
        env=env,
        hours_back=hours_back,
        limit=limit,
        max_pages=config.datadog_max_pages,
    )

    now = datetime.utcnow()
    start = now - timedelta(hours=hours_back)

    # Build query
    dd_query, extra_clause = _build_dd_query(
        service=service,
        env=env,
        statuses_csv=config.datadog_statuses,
        extra_csv=config.datadog_query_extra,
        extra_mode=config.datadog_query_extra_mode,
    )
    log_info("Datadog async query built", query=dd_query)

    results: List[Dict[str, Any]] = []

    async with AsyncDatadogClient() as client:
        # Pagination loop (bounded by DATADOG_MAX_PAGES)
        page = 0
        cursor = None

        while True:
            page += 1
            data, cursor = await client.fetch_page(
                query=dd_query, start=start, end=now, limit=limit, cursor=cursor
            )

            if not data:
                break

            for log in data:
                results.append(_parse_log_entry(log))

            if not cursor or page >= config.datadog_max_pages:
                break

        # If no results and we used an extra clause, retry once without it
        if not results and extra_clause:
            log_info(
                "No results with extra clause; retrying once without DATADOG_QUERY_EXTRA (async)"
            )

            simple_query = f"service:{service} env:{env} status:error"
            data_no_extra, _ = await client.fetch_page(
                query=simple_query, start=start, end=now, limit=limit, cursor=None
            )
            log_info(
                "Fallback query results (async)",
                logs_found=len(data_no_extra),
                suggestion="relax DATADOG_QUERY_EXTRA or set DATADOG_QUERY_EXTRA_MODE=OR if appropriate",
            )

    # End performance timing
    duration = metrics.end_timer("get_logs_async")
    log_info(
        "Datadog logs collected (async)",
        total_logs=len(results),
        duration_ms=round(duration * 1000, 2),
    )

    return results


async def get_logs_batch_async(
    services: List[str],
    env: Optional[str] = None,
    hours_back: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch logs for multiple services concurrently.

    Args:
        services: List of service names
        env: Environment (defaults to config)
        hours_back: Hours to look back (defaults to config)
        limit: Max logs per page (defaults to config)

    Returns:
        Dict mapping service name to logs list
    """
    import asyncio

    log_info("Starting batch async log fetch", service_count=len(services))

    async def fetch_service(svc: str) -> Tuple[str, List[Dict[str, Any]]]:
        logs = await get_logs_async(
            service=svc, env=env, hours_back=hours_back, limit=limit
        )
        return svc, logs

    tasks = [fetch_service(svc) for svc in services]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: Dict[str, List[Dict[str, Any]]] = {}
    for result in results:
        if isinstance(result, Exception):
            log_error("Batch fetch error", error=str(result))
            continue
        svc, logs = result
        output[svc] = logs

    log_info("Batch async log fetch completed", services_fetched=len(output))
    return output


# Convenience function for compatibility
async def fetch_logs_async(
    service: Optional[str] = None,
    env: Optional[str] = None,
    hours_back: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Alias for get_logs_async for backward compatibility."""
    return await get_logs_async(
        service=service, env=env, hours_back=hours_back, limit=limit
    )
