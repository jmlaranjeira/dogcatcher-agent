from langgraph.graph import StateGraph, END
from agent.nodes import analyze_log


# El estado compartido será simplemente un dict (JSON-like)
def build_graph():
    state_schema = dict  # puedes usar TypedDict más adelante si quieres validaciones
    builder = StateGraph(state_schema)

    builder.add_node("analyze_log", analyze_log)
    builder.set_entry_point("analyze_log")
    builder.set_finish_point("analyze_log")

    return builder.compile()