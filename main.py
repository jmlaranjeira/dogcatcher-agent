from agent.graph import build_graph
from dotenv import load_dotenv
from agent.datadog import get_logs

load_dotenv()  # Cargar variables de entorno desde .env

graph = build_graph()

logs = get_logs()
for log in logs:
    result = graph.invoke({"log": log})
    print(result)