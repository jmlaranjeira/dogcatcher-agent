"""Team-specific configuration models for multi-tenant deployments.

When config/teams.yaml exists, the agent operates in multi-tenant mode:
each team gets its own Jira project, Datadog service filters, cache
directory, and audit log.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class TeamConfig(BaseModel):
    """Configuration for a single team."""

    team_id: str = Field(..., description="Unique team identifier (e.g. 'team-vega')")
    team_name: str = Field(..., description="Human-readable team name")
    enabled: bool = Field(True, description="Set to false to skip this team during runs")

    # Jira
    jira_project_key: str = Field(..., description="Jira project key for this team")
    jira_team_field_value: Optional[str] = Field(
        None, description="Value for the Jira Team custom field"
    )

    # Datadog
    datadog_services: List[str] = Field(
        ..., description="Datadog service names to monitor"
    )
    datadog_env: str = Field("prod", description="Datadog environment filter")

    # Agent behavior overrides (None = inherit from base config)
    max_tickets_per_run: Optional[int] = Field(
        None, description="Per-run ticket cap override"
    )
    severity_rules: Optional[Dict[str, str]] = Field(
        None, description="Team-specific error_type → severity mapping"
    )

    @field_validator("team_id", mode="after")
    @classmethod
    def validate_team_id(cls, v: str) -> str:
        if not v or not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "team_id must be alphanumeric with optional hyphens/underscores"
            )
        return v

    @field_validator("datadog_services", mode="after")
    @classmethod
    def validate_services(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("datadog_services must contain at least one service")
        return v


class TeamsConfig(BaseModel):
    """Root container for all team configurations."""

    jira_team_field_id: Optional[str] = Field(
        None, description="Custom field ID for team assignment in Jira"
    )
    teams: Dict[str, TeamConfig] = Field(
        default_factory=dict, description="team_id → TeamConfig"
    )

    def get_team(self, team_id: str) -> Optional[TeamConfig]:
        return self.teams.get(team_id)

    def list_team_ids(self) -> List[str]:
        """Return sorted IDs of enabled teams only."""
        return sorted(tid for tid, t in self.teams.items() if t.enabled)
