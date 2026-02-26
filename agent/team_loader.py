"""Team configuration loader.

Reads config/teams.yaml and provides access to per-team settings.
When the file is absent the agent runs in single-tenant mode (backward compatible).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from agent.team_config import TeamConfig, TeamsConfig
from agent.utils.logger import log_info, log_error

_TEAMS_FILE = Path(__file__).parent.parent / "config" / "teams.yaml"
_cache: Optional[TeamsConfig] = None


def load_teams_config(path: Path | None = None) -> Optional[TeamsConfig]:
    """Load teams configuration from YAML.

    Returns None when the file does not exist (single-tenant mode).
    """
    global _cache
    if _cache is not None:
        return _cache

    teams_path = path or _TEAMS_FILE
    if not teams_path.exists():
        return None

    try:
        with open(teams_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

        # Inject team_id into each team dict so the model can validate it
        for tid, tdata in (raw.get("teams") or {}).items():
            if isinstance(tdata, dict):
                tdata.setdefault("team_id", tid)

        cfg = TeamsConfig(**raw)
        _cache = cfg
        disabled = [tid for tid, t in cfg.teams.items() if not t.enabled]
        log_info(
            "Loaded teams configuration",
            path=str(teams_path),
            team_count=len(cfg.teams),
            enabled_count=len(cfg.teams) - len(disabled),
            disabled_teams=disabled or None,
        )
        return cfg
    except Exception as exc:
        log_error("Failed to load teams.yaml", error=str(exc), path=str(teams_path))
        raise


def reset_cache() -> None:
    """Clear cached config (useful for tests)."""
    global _cache
    _cache = None


def is_multi_tenant(path: Path | None = None) -> bool:
    """Return True when config/teams.yaml exists."""
    return (path or _TEAMS_FILE).exists()


def get_team(team_id: str) -> Optional[TeamConfig]:
    """Get configuration for a specific team (None if not found or single-tenant)."""
    cfg = load_teams_config()
    return cfg.get_team(team_id) if cfg else None


def list_team_ids() -> List[str]:
    """List all configured team IDs (empty list in single-tenant mode)."""
    cfg = load_teams_config()
    return cfg.list_team_ids() if cfg else []
