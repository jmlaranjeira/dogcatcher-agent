"""Main entry point for the Datadog → Jira automation agent.

Loads environment variables, builds the processing graph, fetches logs,
and runs the LangGraph pipeline to analyze and create Jira tickets.
All logging and comments are in English for consistency.
"""
from agent.datadog import get_logs
from agent.graph import build_graph
from dotenv import load_dotenv
import argparse
import os

load_dotenv()

parser = argparse.ArgumentParser(description="Run the Datadog → Jira automation agent.")
group = parser.add_mutually_exclusive_group()
group.add_argument('--dry-run', dest='auto_create_ticket', action='store_false', help='Run in dry-run mode (do not create tickets).')
group.add_argument('--real', dest='auto_create_ticket', action='store_true', help='Run in real mode (create tickets).')
parser.add_argument('--env', type=str, help='Environment filter for logs.')
parser.add_argument('--service', type=str, help='Service filter for logs.')
parser.add_argument('--hours', type=int, help='Number of hours to look back for logs.')
parser.add_argument('--limit', type=int, help='Limit the number of logs fetched.')

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

print("🚀 Starting agent for Jira project:", os.getenv("JIRA_PROJECT_KEY"))

graph = build_graph()
logs = get_logs()
print(f"🪵 Loaded {len(logs)} logs for processing")
if not logs:
    print("ℹ️ No logs to process; exiting.")
    raise SystemExit(0)
print("🛡️ Safety guard: up to 3 real Jira tickets will be created per run (per-process limit).")
graph.invoke(
    {"logs": logs, "log_index": 0, "seen_logs": set()},
    {"recursion_limit": 2000}
)
print("🏁 Agent execution finished")