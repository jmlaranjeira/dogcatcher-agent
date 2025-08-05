from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

llm = ChatOpenAI(
    model="gpt-4-1106-preview",
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
    return {
        "message": f"✅ Ticket creado:\n📌 Título: {state.get('ticket_title')}\n📝 Descripción: {state.get('ticket_description')}"
    }