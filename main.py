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
from agent.config import get_config, reload_config
from agent.performance import log_performance_summary, log_configuration_performance, get_performance_recommendations

parser = argparse.ArgumentParser(description="Run the Datadog → Jira automation agent.")
group = parser.add_mutually_exclusive_group()
group.add_argument('--dry-run', dest='auto_create_ticket', action='store_false', help='Run in dry-run mode (do not create tickets).')
group.add_argument('--real', dest='auto_create_ticket', action='store_true', help='Run in real mode (create tickets).')
parser.add_argument('--env', type=str, help='Environment filter for logs.')
parser.add_argument('--service', type=str, help='Service filter for logs.')
parser.add_argument('--hours', type=int, help='Number of hours to look back for logs.')
parser.add_argument('--limit', type=int, help='Limit the number of logs fetched.')
parser.add_argument('--max-tickets', type=int, help='Per-run cap on real Jira ticket creation (0 = no limit).')
parser.add_argument('--async', dest='async_enabled', action='store_true', help='Enable async parallel processing.')
parser.add_argument('--workers', type=int, help='Number of parallel workers for async mode (default: 5).')
parser.add_argument('--batch-size', type=int, help='Batch size for async processing (default: 10).')

parser.set_defaults(auto_create_ticket=os.getenv('AUTO_CREATE_TICKET', 'true').lower() == 'true')

args = parser.parse_args()

# Apply parsed arguments to environment variables
os.environ['AUTO_CREATE_TICKET'] = 'true' if args.auto_create_ticket else 'false'
if args.env is not None:
    os.environ['DATADOG_ENV'] = args.env
if args.service is not None:
    os.environ['DATADOG_SERVICE'] = args.service
if args.hours is not None:
    os.environ['DATADOG_HOURS_BACK'] = str(args.hours)
if args.limit is not None:
    os.environ['DATADOG_LIMIT'] = str(args.limit)
if args.max_tickets is not None:
    os.environ['MAX_TICKETS_PER_RUN'] = str(args.max_tickets)
if args.async_enabled:
    os.environ['ASYNC_ENABLED'] = 'true'
if args.workers is not None:
    os.environ['ASYNC_MAX_WORKERS'] = str(args.workers)
if args.batch_size is not None:
    os.environ['ASYNC_BATCH_SIZE'] = str(args.batch_size)

from agent.datadog import get_logs

# Load and validate configuration
config = get_config()
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

log_agent_progress("Starting agent", jira_project=config.jira_project_key)

graph = build_graph()
logs = get_logs()
log_agent_progress("Logs loaded", log_count=len(logs))
if not logs:
    log_info("No logs to process; exiting.")
    raise SystemExit(0)

# Use configuration values
_auto = config.auto_create_ticket
_max = config.max_tickets_per_run

if _auto:
    if _max > 0:
        log_info(f"Safety guard: up to {_max} real Jira tickets will be created per run.")
    else:
        log_info("Safety guard: no per-run cap on real Jira tickets (MAX_TICKETS_PER_RUN=0). Be careful.")
else:
    log_info("Dry-run mode: Jira ticket creation is disabled.")

# Choose processing mode based on configuration
if config.async_enabled:
    log_info("Running in ASYNC mode", max_workers=config.async_max_workers)

    import asyncio
    from agent.async_processor import process_logs_parallel

    # Run async processing
    async def run_async():
        return await process_logs_parallel(
            logs=logs,
            max_workers=config.async_max_workers,
            enable_rate_limiting=config.async_rate_limiting
        )

    result = asyncio.run(run_async())

    # Log async processing results
    log_info(
        "Async processing completed",
        processed=result.get("processed", 0),
        successful=result.get("successful", 0),
        errors=result.get("errors", 0),
        duration_seconds=result.get("stats", {}).get("duration_seconds", 0)
    )

else:
    log_info("Running in SYNC mode (sequential processing)")

    # Use traditional sync processing
    graph.invoke(
        {"logs": logs, "log_index": 0, "seen_logs": set(), "created_fingerprints": set()},
        {"recursion_limit": 2000}
    )

log_agent_progress("Agent execution finished")

# Log performance summary
log_performance_summary()
