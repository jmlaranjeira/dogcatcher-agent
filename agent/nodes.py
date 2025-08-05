from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from agent.datadog import get_logs
from agent.jira import create_ticket as create_jira_ticket, check_jira_for_ticket

llm = ChatOpenAI(
    model="gpt-3.5-turbo",
    temperature=0
)

prompt = ChatPromptTemplate.from_messages([
    ("system", "Eres un ingeniero de soporte. Analiza mensajes de log y devuelve un JSON con los campos: error_type, create_ticket, ticket_title y ticket_description."),
    ("human", "{log}")
])

chain = prompt | llm

import re
import json

def analyze_log(state):
    log = state.get("log", "")
    response = chain.invoke({"log": log})
    content = response.content

    # Intenta extraer JSON incluso si viene envuelto en ```json ... ```
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    raw_json = match.group(1) if match else content

    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return {
            "error_type": "desconocido",
            "create_ticket": False,
            "ticket_title": "Respuesta no válida del LLM",
            "ticket_description": content
        }



def create_ticket(state):
    title = state.get("ticket_title")
    description = state.get("ticket_description")

    if check_jira_for_ticket(title):
        return {
            "message": f"⚠️ Ticket ya existente para: {title}"
        }

    create_jira_ticket(title, description)
    return {
        "message": f"✅ Ticket creado:\n📌 Título: {title}\n📝 Descripción: {description}"
    }

def fetch_logs(_: dict) -> dict:
    logs = get_logs()
    return {"log": logs[0] if logs else ""}