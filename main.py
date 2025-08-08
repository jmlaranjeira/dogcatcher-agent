from agent.datadog import get_logs
from agent.graph import build_graph
from dotenv import load_dotenv

load_dotenv()
import os

print("🚀 Starting agent for Jira project:", os.getenv("JIRA_PROJECT_KEY"))

graph = build_graph()
logs = get_logs()
print(f"🪵 Loaded {len(logs)} logs for processing")
print("🛡️ Safety guard: only one real Jira ticket will be created per run.")
graph.invoke(
    {"logs": logs, "log_index": 0, "seen_logs": set()},
    {"recursion_limit": 100}
)
print("🏁 Agent execution finished")