from langgraph.graph import StateGraph, END
from agent.nodes import analyze_log, fetch_logs
from agent.nodes import create_ticket as create_jira_ticket


# Shared state is a simple dict (JSON-like)
from typing import TypedDict, List, Set

class GraphState(TypedDict):
    logs: List[dict]
    log_index: int
    seen_logs: Set[str]

state_schema = GraphState

# Wrapper for analyze_log to handle current log and duplicate detection
def analyze_log_wrapper(state):
    if "seen_logs" not in state:
        state["seen_logs"] = set()
    log = state["logs"][state["log_index"]]
    # Skip logs that have already been analyzed based on a unique log key
    log_key = f"{log.get('logger', 'unknown')}|{log.get('thread', 'unknown')}|{log.get('log_message', '<no message>')}"
    if log_key in state["seen_logs"]:
        return {
            **state,
            "skipped_duplicate": True
        }
    seen_logs = state["seen_logs"]
    seen_logs.add(log_key)
    print(f"🔍 Analyzing log: {log_key}")
    new_state = analyze_log({
        **state,
        "log_message": log.get("log_message", "<no message>"),
        "log_data": log,
        "seen_logs": seen_logs
    })
    return new_state

# Function to advance to next log, avoiding infinite loops via 'visited' set
def next_log(state):
    logs = state.get("logs", [])
    index = state.get("log_index", 0) + 1

    if index >= len(logs):
        print("✅ No more logs. Terminating.")
        return {**state, "finished": True}

    # Skip duplicate logs
    log = logs[index]
    log_key = f"{log.get('logger', 'unknown')}|{log.get('thread', 'unknown')}|{log.get('log_message', '<no message>')}"
    if log_key in state.get("seen_logs", set()):
        print(f"⏭️ Duplicate log skipped: {log_key}")
        return {
            **state,
            "log_index": index,
            "skipped_duplicate": True
        }

    return {
        **state,
        "log_index": index,
        "log_message": log.get("log_message", "<no message>"),
        "log_data": log
    }

# Build the LangGraph execution flow with steps:
# 1. Fetch log
# 2. Analyze log
# 3. Create ticket or move to next log
# 4. Avoid duplicate processing with memory (seen_logs and visited)
def build_graph():
    builder = StateGraph(GraphState)

    builder.set_entry_point("fetch_logs")
    builder.add_node("fetch_logs", fetch_logs)
    builder.add_edge("fetch_logs", "analyze_log")

    builder.add_node("analyze_log", analyze_log_wrapper)
    builder.add_node("create_ticket", create_jira_ticket)

    builder.add_conditional_edges(
        "analyze_log",
        lambda state: "create_ticket" if state.get("create_ticket") else "next_log",
        {
            "create_ticket": "create_ticket",
            "next_log": "next_log"
        }
    )

    builder.add_edge("create_ticket", "next_log")

    builder.add_node("next_log", next_log)
    # Conditional finish: if 'finished', terminate; else, continue
    builder.add_conditional_edges(
        "next_log",
        lambda state: END if state.get("finished") else "analyze_log",
        {
            END: END,
            "analyze_log": "analyze_log"
        }
    )
    return builder.compile()