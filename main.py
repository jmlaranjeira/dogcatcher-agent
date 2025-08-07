from agent.datadog import get_logs
from agent.graph import build_graph
from dotenv import load_dotenv

load_dotenv()

graph = build_graph()
logs = get_logs()
graph.invoke(
    {"logs": logs, "log_index": 0, "seen_logs": set()},
    {"recursion_limit": 100}
)