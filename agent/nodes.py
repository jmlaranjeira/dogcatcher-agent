from dotenv import load_dotenv
load_dotenv()

import os
print("✅ Using Jira project:", os.getenv("JIRA_PROJECT_KEY"))

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
        import copy
        serializable_state = copy.deepcopy({**state, **parsed})
        if isinstance(serializable_state.get("seen_logs"), set):
            serializable_state["seen_logs"] = list(serializable_state["seen_logs"])
        print("📦 State after analyze_log:", json.dumps(serializable_state, indent=2))
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
    import os
    import copy
    import json

    # print("🚀 Entering create_ticket")
    # Only allow one ticket per run
    if state.get("ticket_created"):
        # print("⚠️ Ticket already created in this session. Skipping.")
        return {**state, "message": "⚠️ Only one ticket allowed per run."}
    debug_state = {k: (list(v) if isinstance(v, set) else v) for k, v in state.items()}
    # print("📥 State received in create_ticket:", json.dumps(debug_state, indent=2))

    title = state.get("ticket_title")
    description = state.get("ticket_description")

    if not title or not description:
        # print("⚠️ Ticket title or description missing. Ticket not created.")
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
        msg = f"⚠️ Ticket already exists for: {title}"
        # print(msg)
        return {
            **state,
            "message": msg
        }

    # Prepare payload for Jira ticket creation
    TICKET_FLAG = os.getenv("TICKET_FLAG", "")
    TICKET_LABEL = os.getenv("TICKET_LABEL", "")

    # Remove "**" from title if present
    clean_title = title.replace("**", "")

    summary = f"{TICKET_FLAG} {clean_title}".strip()

    # Atlassian Document Format (ADF) for description
    description_adf = {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": full_description
                    }
                ]
            }
        ]
    }

    payload = {
        "fields": {
            "summary": summary,
            "description": description_adf,
            "labels": [TICKET_LABEL] if TICKET_LABEL else [],
            "priority": {"name": "Low"},
            "customfield_10767": {"value": "Team Vega"},
        }
    }

    state["ticket_description"] = full_description
    state["ticket_title"] = summary
    state["jira_payload"] = payload

    if os.getenv("AUTO_CREATE_TICKET", "false").lower() == "true":
        try:
            print(f"🚀 Creating ticket in project: {os.getenv('JIRA_PROJECT_KEY')}")
            create_jira_ticket(state)
            issue_key = state.get("jira_response_key", None)
            if not issue_key:
                # fallback to response json if available
                response = state.get("jira_response", None)
                if response:
                    issue_key = response.json().get("key", "UNKNOWN")
            jira_url = f"https://{os.getenv('JIRA_DOMAIN')}/browse/{issue_key}"
            print(f"✅ Jira ticket created: {issue_key}")
            print(f"🔗 {jira_url}")
        except Exception:
            print("❌ Failed to create Jira ticket.")
    else:
        print("\n🧪 Simulated Ticket Creation (AUTO_CREATE_TICKET is false)...")
        print(f"📌 Title      : {summary}")
        print(f"📝 Description: {full_description}")
        print(f"📦 Payload    : {json.dumps(payload, indent=2)}")
        print("✅ Ticket creation skipped (simulation mode enabled)\n")

    state["ticket_created"] = True
    return {
        **state,
        "message": f"✅ Ticket created:\n📌 Title: {summary}\n📝 Description: {full_description}"
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