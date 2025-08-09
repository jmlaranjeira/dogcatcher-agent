from agent.datadog import get_logs
from agent.graph import build_graph
from dotenv import load_dotenv

load_dotenv()
import os

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