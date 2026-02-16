"""Validate config/teams.yaml against the Pydantic schema.

Usage:
    python -m tools.validate_teams                    # validate default path
    python -m tools.validate_teams config/teams.yaml  # validate specific file
    python -m tools.validate_teams --schema           # emit JSON Schema to stdout
    python -m tools.validate_teams --schema -o schema/teams.schema.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

import yaml

from agent.team_config import TeamsConfig


def validate_file(path: Path) -> Tuple[bool, List[str]]:
    """Validate a teams YAML file.

    Returns (ok, messages) — messages are errors when ok=False,
    or a success summary when ok=True.
    """
    if not path.exists():
        return False, [f"File not found: {path}"]

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        return False, [f"YAML parse error: {exc}"]

    errors: List[str] = []

    # Inject team_id into each team dict (same logic as team_loader)
    teams_raw = raw.get("teams")
    if isinstance(teams_raw, dict):
        for tid, tdata in teams_raw.items():
            if isinstance(tdata, dict):
                tdata.setdefault("team_id", tid)

    try:
        cfg = TeamsConfig(**raw)
    except Exception as exc:
        return False, [f"Schema validation failed: {exc}"]

    # Cross-field validations
    warnings: list[str] = []
    seen_projects: dict[str, str] = {}
    for tid, team in cfg.teams.items():
        if tid != team.team_id:
            errors.append(f"Key mismatch: dict key '{tid}' != team_id '{team.team_id}'")
        if team.jira_project_key in seen_projects:
            warnings.append(
                f"Shared jira_project_key '{team.jira_project_key}' "
                f"in teams '{seen_projects[team.jira_project_key]}' and '{tid}' "
                f"— use team labels to distinguish tickets"
            )
        seen_projects[team.jira_project_key] = tid

    if errors:
        return False, errors

    messages = [f"Valid: {len(cfg.teams)} team(s) configured"]
    for w in warnings:
        messages.append(f"[WARN] {w}")
    return True, messages


def generate_schema() -> dict:
    """Generate JSON Schema from TeamsConfig Pydantic model."""
    return TeamsConfig.model_json_schema()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate teams.yaml configuration")
    parser.add_argument(
        "file",
        nargs="?",
        default="config/teams.yaml",
        help="Path to teams.yaml (default: config/teams.yaml)",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Output JSON Schema and exit",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Write schema to file instead of stdout",
    )
    args = parser.parse_args()

    if args.schema:
        schema = generate_schema()
        output = json.dumps(schema, indent=2)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(output + "\n")
            print(f"Schema written to {args.output}")
        else:
            print(output)
        sys.exit(0)

    path = Path(args.file)
    ok, messages = validate_file(path)
    for msg in messages:
        symbol = "OK" if ok else "ERROR"
        print(f"[{symbol}] {msg}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
