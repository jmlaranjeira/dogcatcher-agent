from dotenv import load_dotenv
load_dotenv()

import os
print("🚀 Starting agent for Jira project:", os.getenv("JIRA_PROJECT_KEY"))

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from agent.jira import create_ticket as create_jira_ticket, check_jira_for_ticket

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    model_kwargs={"response_format": {"type": "json_object"}}
)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a support engineer. Analyze log messages and return a JSON with fields: error_type, create_ticket, ticket_title and ticket_description."),
    ("human", "{log_message}")
])

chain = prompt | llm

import re
import json
import hashlib
import pathlib
from typing import Set as _Set

_CACHE_PATH = pathlib.Path(".agent_cache/processed_logs.json")


def _load_processed_fingerprints() -> _Set[str]:
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def _save_processed_fingerprints(fps: _Set[str]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(fps)), f, ensure_ascii=False, indent=2)

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
    # print("🧠 LLM response:", response.content)
    content = response.content
    print("🧠 LLM raw content:", content)
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
        # print("📦 State after analyze_log:", json.dumps(serializable_state, indent=2))
        print(f"🧠 Log analyzed → Type: {parsed.get('error_type')}, Create ticket: {parsed.get('create_ticket')}")
        import pprint
        print("🚨 Estado retornado de analyze_log:")
        pprint.pprint({**state, **parsed})
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
    print(f"🛠️ Entered create_ticket() | AUTO_CREATE_TICKET={os.getenv('AUTO_CREATE_TICKET')}")
    import pprint
    print("🔎 Estado recibido en create_ticket:")
    pprint.pprint(state)
    assert "ticket_title" in state and "ticket_description" in state, "Missing LLM fields before jira.create_ticket"
    title = state.get("ticket_title")
    description = state.get("ticket_description")
    print(f"🧾 Title to create: {title}")
    if description is None:
        print("❌ Error: ticket_description is None.")
        return {**state, "message": "❌ ticket_description is None. Skipping ticket creation.", "ticket_created": True}
    print(f"📝 Description to create: {description[:160]}{'...' if description and len(description) > 160 else ''}")

    import copy
    import json

    if state.get("ticket_created"):
        print("🔔 Ya se ha creado un ticket en esta ejecución (incluye simulación). Se omite crear más.")
        return {**state, "message": "⚠️ Solo se permite crear un ticket por ejecución (incluye simulación)."}

    # print("🚀 Entering create_ticket")
    debug_state = {k: (list(v) if isinstance(v, set) else v) for k, v in state.items()}
    # print("📥 State received in create_ticket:", json.dumps(debug_state, indent=2))

    if not title or not description:
        # print("⚠️ Ticket title or description missing. Ticket not created.")
        return {
            **state,
            "message": "⚠️ Ticket title or description missing. Ticket not created.",
            "ticket_created": True
        }

    log_data = state.get("log_data", {})

    # Stronger idempotence across runs: stable fingerprint from logger|thread|message
    fp_source = f"{log_data.get('logger','')}|{log_data.get('thread','')}|{log_data.get('message','')}"
    fingerprint = hashlib.sha1(fp_source.encode("utf-8")).hexdigest()[:12]
    processed = _load_processed_fingerprints()
    if fingerprint in processed:
        print(f"🔁 Skipping ticket creation: fingerprint already processed: {fingerprint}")
        return {**state, "message": "⚠️ Log already processed previously (fingerprint match).", "ticket_created": True}
    # Put fingerprint into state so Jira payload can add a label
    state["log_fingerprint"] = fingerprint

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
        processed.add(state["log_fingerprint"]) if state.get("log_fingerprint") else None
        _save_processed_fingerprints(processed)
        msg = f"⚠️ Ticket already exists for: {title}"
        # print(msg)
        return {
            **state,
            "message": msg,
            "ticket_created": True
        }

    # Prepare payload for Jira ticket creation
    TICKET_FLAG = os.getenv("TICKET_FLAG", "")
    TICKET_LABEL = os.getenv("TICKET_LABEL", "")

    # labels to include (simulation payload only; real payload built in jira.create_ticket)
    labels = [TICKET_LABEL] if TICKET_LABEL else []
    if state.get("log_fingerprint"):
        labels.append(f"logfp-{state['log_fingerprint']}")

    # Remove "**" from title if present
    clean_title = title.replace("**", "")

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
            "summary": clean_title,
            "description": description_adf,
            "labels": labels,
            "priority": {"name": "Low"},
            "customfield_10767": [{"value": "Team Vega"}],
        }
    }

    state["ticket_description"] = full_description
    state["ticket_title"] = clean_title
    state["jira_payload"] = payload

    if os.getenv("AUTO_CREATE_TICKET", "false").lower() == "true":
        try:
            print(f"🚀 Creating ticket in project: {os.getenv('JIRA_PROJECT_KEY')}")
            state = create_jira_ticket(state)
            issue_key = state.get("jira_response_key", None)
            if issue_key:
                jira_url = state.get("jira_response_url", None)
                print(f"✅ Jira ticket created: {issue_key}")
                if jira_url:
                    print(f"🔗 {jira_url}")
                # mark as created only on success and persist fingerprint
                state["ticket_created"] = True
                processed.add(state["log_fingerprint"]) if state.get("log_fingerprint") else None
                _save_processed_fingerprints(processed)
            else:
                print("❌ No Jira issue key found after ticket creation attempt.")
                if "jira_response_raw" in state:
                    import json
                    print(json.dumps(state["jira_response_raw"], indent=2))
        except Exception:
            print("❌ Failed to create Jira ticket.")
    else:
        print("\n🧪 Simulated Ticket Creation (AUTO_CREATE_TICKET is false)...")
        print(f"📌 Title      : {clean_title}")
        print(f"📝 Description: {full_description}")
        print(f"📦 Payload    : {json.dumps(payload, indent=2)}")
        print("✅ Ticket creation skipped (simulation mode enabled)\n")
        state["ticket_created"] = True
        persist_sim = os.getenv("PERSIST_SIM_FP", "false").lower() in ("1", "true", "yes")
        if persist_sim and state.get("log_fingerprint"):
            processed.add(state["log_fingerprint"])
            _save_processed_fingerprints(processed)

    return {
        **state,
        "message": f"✅ Ticket created:\n📌 Title: {clean_title}\n📝 Description: {full_description}",
        "ticket_created": True
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