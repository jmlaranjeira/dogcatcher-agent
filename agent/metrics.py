"""Agent self-observability via DogStatsD custom metrics.

When DATADOG_METRICS_ENABLED=true, this module emits operational metrics
to a local Datadog Agent (or DogStatsD sidecar). When disabled or if the
connection fails, all calls become no-ops.

Metrics emitted:
  - dogcatcher.run.duration          (gauge, seconds)
  - dogcatcher.logs.fetched          (count)
  - dogcatcher.logs.processed        (count)
  - dogcatcher.tickets.created       (count)
  - dogcatcher.tickets.simulated     (count)
  - dogcatcher.tickets.cap_reached   (count)
  - dogcatcher.duplicates.found      (count)
  - dogcatcher.duplicates.fingerprint(count)
  - dogcatcher.duplicates.jira       (count)
  - dogcatcher.cache.hit             (count)
  - dogcatcher.cache.miss            (count)
  - dogcatcher.cache.hit_rate        (gauge, percent)
  - dogcatcher.api.datadog_duration  (timing, ms)
"""

from __future__ import annotations

import atexit
from typing import Dict, List, Optional

from agent.utils.logger import log_debug, log_info, log_warning


class _NoOpStatsd:
    """Drop-in replacement when DogStatsD is unavailable."""

    def increment(self, *a, **kw):
        pass

    def gauge(self, *a, **kw):
        pass

    def histogram(self, *a, **kw):
        pass

    def timing(self, *a, **kw):
        pass

    def close(self):
        pass


_client = None  # will be _NoOpStatsd or real DogStatsd


def _init_client() -> None:
    global _client
    if _client is not None:
        return

    from agent.config import get_config

    config = get_config()

    if not config.datadog_metrics_enabled:
        log_debug("DogStatsD metrics disabled (DATADOG_METRICS_ENABLED=false)")
        _client = _NoOpStatsd()
        return

    try:
        from datadog import DogStatsd

        _client = DogStatsd(
            host=config.dd_agent_host,
            port=config.dd_agent_port,
            namespace=config.metrics_prefix,
            constant_tags=[
                f"service:{config.datadog_service}",
                f"env:{config.datadog_env}",
            ],
        )
        atexit.register(_client.close)
        log_info(
            "DogStatsD client initialized",
            host=config.dd_agent_host,
            port=config.dd_agent_port,
            prefix=config.metrics_prefix,
        )
    except Exception as exc:
        log_warning("DogStatsD unavailable, metrics disabled", error=str(exc))
        _client = _NoOpStatsd()


def _get_client():
    if _client is None:
        _init_client()
    return _client


def _tags(extra: Optional[Dict[str, str]] = None) -> List[str]:
    """Build tag list from key-value pairs, filtering out None values."""
    tags: List[str] = []
    if extra:
        tags.extend(f"{k}:{v}" for k, v in extra.items() if v)
    return tags


# --- Public API ---


def incr(
    metric: str, value: int = 1, *, team_id: str | None = None, **extra_tags
) -> None:
    """Increment a counter metric."""
    tag_dict = {"team_id": team_id, **extra_tags} if team_id else extra_tags or None
    tags = _tags(tag_dict)
    _get_client().increment(metric, value=value, tags=tags or None)


def gauge(
    metric: str, value: float, *, team_id: str | None = None, **extra_tags
) -> None:
    """Set a gauge metric."""
    tag_dict = {"team_id": team_id, **extra_tags} if team_id else extra_tags or None
    tags = _tags(tag_dict)
    _get_client().gauge(metric, value=value, tags=tags or None)


def timing(
    metric: str, value_ms: float, *, team_id: str | None = None, **extra_tags
) -> None:
    """Record a timing metric in milliseconds."""
    tag_dict = {"team_id": team_id, **extra_tags} if team_id else extra_tags or None
    tags = _tags(tag_dict)
    _get_client().timing(metric, value=value_ms, tags=tags or None)
