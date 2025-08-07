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
    ("system", "You are a support engineer. Analyze log messages and return a JSON with fields: error_type, create_ticket, ticket_title and ticket_description."),
    ("human", "{log_message}")
])

chain = prompt | llm

import re
import json

# Analyze a log entry using an LLM to extract structured incident data.
def analyze_log(state):
    log_data = state.get("log_data", {})
    msg = log_data.get("message", "")
    logger = log_data.get("logger", "unknown.logger")
    thread = log_data.get("thread", "unknown.thread")
    detail = log_data.get("detail", "")
    # Debug: structured log data
    # print(json.dumps(log_data, indent=2))
    contextual_log = (
        f"[Logger]: {logger if logger else 'unknown.logger'}\n"
        f"[Thread]: {thread if thread else 'unknown.thread'}\n"
        f"[Message]: {msg if msg else '<no message>'}\n"
        f"[Detail]: {detail if detail else '<no detail>'}"
    )

    response = chain.invoke({"log_message": contextual_log})
    # Debug: LLM response content
    print("🧠 LLM response:", response.content)
    content = response.content

    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    raw_json = match.group(1) if match else content

    try:
        parsed = json.loads(raw_json)
        title = parsed.get("ticket_title")
        desc = parsed.get("ticket_description")
        if not title or not desc:
            raise ValueError("Missing title or description")
        return {**state, **parsed}
    except (json.JSONDecodeError, ValueError):
        return {
            **state,
            "error_type": "unknown",
            "create_ticket": False,
            "ticket_title": "LLM returned invalid or incomplete data",
            "ticket_description": content
        }

def create_ticket(state):
    title = state.get("ticket_title")
    description = state.get("ticket_description")

    if not title or not description:
        return {
            **state,
            "message": "⚠️ Ticket title or description missing. Ticket not created."
        }

    log_data = state.get("log_data", {})
    extra_info = f"""
    ---
    🕒 Timestamp: {log_data.get('timestamp', 'N/A')}
    🧩 Logger: {log_data.get('logger', 'N/A')}
    🧵 Thread: {log_data.get('thread', 'N/A')}
    📝 Original Log: {log_data.get('message', 'N/A')}
    🔍 Detail: {log_data.get('detail', 'N/A')}
    """

    full_description = f"{description.strip()}\n{extra_info.strip()}"

    if check_jira_for_ticket(title):
        return {
            **state,
            "message": f"⚠️ Ticket already exists for: {title}"
        }

    state["ticket_description"] = full_description
    create_jira_ticket(state)
    return {
        **state,
        "message": f"✅ Ticket created:\n📌 Title: {title}\n📝 Description: {full_description}"
    }

def fetch_logs(state):
    if state.get("skipped_duplicate"):
        return state

    # Debug: processing new log
    # print("🔄 Processing new log...")
    # print(json.dumps(state, indent=2))
    logs = state.get("logs", [])
    # print("🪵 fetch_logs called")
    # print(json.dumps(state, indent=2))
    index = state.get("log_index", 0)
    if index < len(logs):
        log = logs[index]
        # print(f"📝 Current log message: {log.get('message', '<no message>')}")
        return {
            **state,
            "log_message": log.get("message", ""),
            "log_data": log or {}
        }
    else:
        return {
            **state,
            "log_message": "",
            "log_data": {}
        }