from langgraph.graph import StateGraph, END
from agent.nodes import analyze_log, fetch_logs
from agent.jira import create_ticket as create_jira_ticket


# El estado compartido será simplemente un dict (JSON-like)
def build_graph():
    state_schema = dict  # puedes usar TypedDict más adelante si quieres validaciones
    builder = StateGraph(state_schema)

    builder.add_node("get_logs", fetch_logs)
    builder.set_entry_point("get_logs")
    builder.add_edge("get_logs", "analyze_log")

    builder.add_node("analyze_log", analyze_log)
    builder.add_node("create_ticket", create_jira_ticket)
    builder.add_conditional_edges(
        "analyze_log",
        lambda state: "create_ticket" if state.get("create_ticket") else END,
        {
            "create_ticket": "create_ticket",
            END: END
        }
    )
    builder.set_finish_point("create_ticket")

    return builder.compile()