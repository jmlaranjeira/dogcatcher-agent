from agent.graph import build_graph

graph = build_graph()

input_data = {
    "log": "2025-08-03 10:15:44 ERROR: NullPointerException in RiskPlanService.java:127"
}

result = graph.invoke(input_data)
print(result)