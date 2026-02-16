"""Main entry point for the Datadog → Jira automation agent.

Loads environment variables, builds the processing graph, fetches logs,
and runs the LangGraph pipeline to analyze and create Jira tickets.
All logging and comments are in English for consistency.
"""

from dotenv import load_dotenv
import argparse
import os
import sys

# Load environment variables first, before any other imports
load_dotenv()

from agent.graph import build_graph
from agent.utils.logger import log_info, log_error, log_agent_progress
from agent.config import get_config
from agent.run_config import RunConfig
from agent.performance import (
    log_performance_summary,
    log_configuration_performance,
    get_performance_recommendations,
)

parser = argparse.ArgumentParser(description="Run the Datadog → Jira automation agent.")
group = parser.add_mutually_exclusive_group()
group.add_argument(
    "--dry-run",
    dest="auto_create_ticket",
    action="store_false",
    help="Run in dry-run mode (do not create tickets).",
)
group.add_argument(
    "--real",
    dest="auto_create_ticket",
    action="store_true",
    help="Run in real mode (create tickets).",
)
parser.add_argument("--env", type=str, help="Environment filter for logs.")
parser.add_argument("--service", type=str, help="Service filter for logs.")
parser.add_argument("--hours", type=int, help="Number of hours to look back for logs.")
parser.add_argument("--limit", type=int, help="Limit the number of logs fetched.")
parser.add_argument(
    "--max-tickets",
    type=int,
    help="Per-run cap on real Jira ticket creation (0 = no limit).",
)
parser.add_argument(
    "--async",
    dest="async_enabled",
    action="store_true",
    help="Enable async parallel processing.",
)
parser.add_argument(
    "--workers",
    type=int,
    help="Number of parallel workers for async mode (default: 5).",
)
parser.add_argument(
    "--batch-size", type=int, help="Batch size for async processing (default: 10)."
)
parser.add_argument(
    "--profile",
    type=str,
    choices=["development", "staging", "production", "testing"],
    help="Configuration profile to use (overrides .env defaults)",
)
parser.add_argument(
    "--check",
    action="store_true",
    help="Run health checks to verify OpenAI, Datadog, and Jira connections, then exit.",
)
parser.add_argument(
    "--patchy",
    action="store_true",
    help="Invoke Patchy to create draft PRs for tickets created (requires GITHUB_TOKEN).",
)
parser.add_argument(
    "--team",
    type=str,
    help="Run only for a specific team (requires config/teams.yaml).",
)

parser.set_defaults(
    auto_create_ticket=os.getenv("AUTO_CREATE_TICKET", "true").lower() == "true"
)

args = parser.parse_args()

# Track whether --real / --dry-run was explicitly passed on the CLI
# (as opposed to falling through to the .env-based default).
_cli_auto_create: bool | None = None
if "--real" in sys.argv:
    _cli_auto_create = True
elif "--dry-run" in sys.argv:
    _cli_auto_create = False

# Apply parsed arguments to environment variables
os.environ["AUTO_CREATE_TICKET"] = "true" if args.auto_create_ticket else "false"
if args.env is not None:
    os.environ["DATADOG_ENV"] = args.env
if args.service is not None:
    os.environ["DATADOG_SERVICE"] = args.service
if args.hours is not None:
    os.environ["DATADOG_HOURS_BACK"] = str(args.hours)
if args.limit is not None:
    os.environ["DATADOG_LIMIT"] = str(args.limit)
if args.max_tickets is not None:
    os.environ["MAX_TICKETS_PER_RUN"] = str(args.max_tickets)
if args.async_enabled:
    os.environ["ASYNC_ENABLED"] = "true"
if args.workers is not None:
    os.environ["ASYNC_MAX_WORKERS"] = str(args.workers)
if args.batch_size is not None:
    os.environ["ASYNC_BATCH_SIZE"] = str(args.batch_size)
if args.patchy:
    os.environ["INVOKE_PATCHY"] = "true"

# Handle --check flag: run health checks and exit
if args.check:
    from agent.healthcheck import run_health_checks

    all_healthy, _ = run_health_checks(verbose=True)
    sys.exit(0 if all_healthy else 1)

from agent.datadog import get_logs

# Load and validate configuration
config = get_config()

# Apply profile overrides if specified
if args.profile:
    try:
        config.load_profile_overrides(args.profile)
        print(f"✓ Using configuration profile: {args.profile}")

        # If --real or --dry-run was explicitly passed on CLI, it overrides
        # the profile value.  Otherwise the profile value wins over .env.
        if _cli_auto_create is not None:
            config.auto_create_ticket = _cli_auto_create
    except (ValueError, FileNotFoundError) as e:
        log_error(
            "Failed to load configuration profile", profile=args.profile, error=str(e)
        )
        print(f"❌ Failed to load profile '{args.profile}': {e}")
        sys.exit(1)

config.log_configuration()

# Log performance configuration
log_configuration_performance()

# Validate configuration
issues = config.validate_configuration()
if issues:
    log_error("Configuration validation failed", issues=issues)
    print("❌ Configuration issues found:")
    for issue in issues:
        print(f"  - {issue}")
    print("\nPlease fix these issues and try again.")
    sys.exit(1)

# Log performance recommendations
recommendations = get_performance_recommendations()
if recommendations:
    log_info("Performance optimization recommendations")
    for rec in recommendations:
        log_info(f"  💡 {rec}")


def _run_for_service(graph, run_config: RunConfig):
    """Run the pipeline for one (service, team) combination."""
    _auto = run_config.auto_create_ticket
    _max = run_config.max_tickets_per_run

    if _auto:
        if _max > 0:
            log_info(
                f"Safety guard: up to {_max} real Jira tickets will be created per run."
            )
        else:
            log_info(
                "Safety guard: no per-run cap on real Jira tickets (MAX_TICKETS_PER_RUN=0). Be careful."
            )
    else:
        log_info("Dry-run mode: Jira ticket creation is disabled.")

    logs = get_logs(
        service=run_config.datadog_service,
        env=run_config.datadog_env,
        hours_back=run_config.datadog_hours_back,
        limit=run_config.datadog_limit,
    )
    log_agent_progress("Logs loaded", log_count=len(logs))
    if not logs:
        log_info("No logs to process for this service; skipping.")
        return

    initial_state = {
        "run_config": run_config,
        "logs": logs,
        "log_index": 0,
        "seen_logs": set(),
        "created_fingerprints": set(),
    }
    if run_config.team_id:
        initial_state["team_id"] = run_config.team_id
    if run_config.team_service:
        initial_state["team_service"] = run_config.team_service

    import time as _time

    from agent.metrics import gauge as _m_gauge, incr as _m_incr

    _run_start = _time.time()

    config = get_config()
    if config.async_enabled:
        log_info("Running in ASYNC mode", max_workers=config.async_max_workers)
        import asyncio
        from agent.async_processor import process_logs_parallel

        async def run_async():
            return await process_logs_parallel(
                logs=logs,
                max_workers=config.async_max_workers,
                enable_rate_limiting=config.async_rate_limiting,
                run_config=run_config,
            )

        result = asyncio.run(run_async())
        log_info(
            "Async processing completed",
            processed=result.get("processed", 0),
            successful=result.get("successful", 0),
            errors=result.get("errors", 0),
            duration_seconds=result.get("stats", {}).get("duration_seconds", 0),
        )
    else:
        log_info("Running in SYNC mode (sequential processing)")
        graph.invoke(initial_state, {"recursion_limit": 2000})

    _run_duration = _time.time() - _run_start
    _m_gauge("run.duration", _run_duration, team_id=run_config.team_id)
    _m_incr("logs.processed", value=len(logs), team_id=run_config.team_id)


# --- Multi-tenant support ---
from agent.team_loader import load_teams_config, is_multi_tenant

teams_config = load_teams_config()

if teams_config:
    # Multi-tenant mode
    if args.team:
        team = teams_config.get_team(args.team)
        if not team:
            print(f"Team '{args.team}' not found in config/teams.yaml")
            sys.exit(1)
        team_ids = [args.team]
    else:
        team_ids = teams_config.list_team_ids()

    log_agent_progress("Starting agent (multi-tenant)", team_count=len(team_ids))
    graph = build_graph()

    for tid in team_ids:
        team = teams_config.get_team(tid)
        if not team:
            continue
        for svc in team.datadog_services:
            log_agent_progress(
                "Processing team/service",
                team_id=tid,
                service=svc,
                jira_project=team.jira_project_key,
            )
            rc = RunConfig.from_team(team, svc, config)
            _run_for_service(graph, rc)

    log_agent_progress("Agent execution finished (multi-tenant)")
else:
    # Single-tenant mode (backward compatible)
    if args.team:
        print("--team requires config/teams.yaml to exist")
        sys.exit(1)

    log_agent_progress("Starting agent", jira_project=config.jira_project_key)
    graph = build_graph()
    rc = RunConfig.from_config(config)
    _run_for_service(graph, rc)
    log_agent_progress("Agent execution finished")

# Log performance summary
log_performance_summary()
