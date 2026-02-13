"""Context manager for safe multi-tenant environment variable overrides.

Wraps ``os.environ`` mutations so that the original values are guaranteed to
be restored after each team/service run, even if the body raises.

.. deprecated::
    Prefer building a ``RunConfig.from_team(team, service, base_config)``
    and injecting it into the graph state instead of mutating ``os.environ``.
    See ``agent.run_config`` for the replacement pattern.
"""

from __future__ import annotations

import os
import warnings
from contextlib import contextmanager
from typing import TYPE_CHECKING

from agent.config import reload_config

if TYPE_CHECKING:
    from agent.team_config import TeamConfig

# Environment variables that are scoped per-team/service run.
_TEAM_SCOPED_VARS = (
    "JIRA_PROJECT_KEY",
    "DATADOG_SERVICE",
    "DATADOG_ENV",
    "MAX_TICKETS_PER_RUN",
)

_SENTINEL = object()


@contextmanager
def team_env_override(team: TeamConfig, service: str):
    """Temporarily override env vars for a team/service run.

    Guarantees restoration of **all** team-scoped env vars on exit,
    even if the body raises an exception.  This prevents value leakage
    between sequential team iterations (e.g. ``MAX_TICKETS_PER_RUN``
    from team A bleeding into team B).

    .. deprecated::
        Use ``RunConfig.from_team()`` instead.  This context manager is
        kept for backward compatibility but will be removed in a future
        version.
    """
    warnings.warn(
        "team_env_override is deprecated; use RunConfig.from_team() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    # 1. Snapshot originals (SENTINEL means "was not set")
    originals: dict[str, object] = {}
    for key in _TEAM_SCOPED_VARS:
        originals[key] = os.environ.get(key, _SENTINEL)

    try:
        # 2. Apply overrides
        os.environ["JIRA_PROJECT_KEY"] = team.jira_project_key
        os.environ["DATADOG_SERVICE"] = service
        os.environ["DATADOG_ENV"] = team.datadog_env

        if team.max_tickets_per_run is not None:
            os.environ["MAX_TICKETS_PER_RUN"] = str(team.max_tickets_per_run)
        else:
            # Remove so reload_config() picks up the base default
            os.environ.pop("MAX_TICKETS_PER_RUN", None)

        reload_config()
        yield

    finally:
        # 3. Restore originals
        for key, prev in originals.items():
            if prev is _SENTINEL:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev  # type: ignore[assignment]

        reload_config()
