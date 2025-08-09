"""Pipeline nodes for analysis and ticket creation.

Contains:
- analyze_log(state): LLM-based log analysis producing structured fields
- create_ticket(state): orchestration and simulation of Jira ticket creation
- fetch_logs(state): prepares the current log in state
All comments and logs are in English for consistency across the project.
"""
from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from agent.jira import create_ticket as create_jira_ticket

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    model_kwargs={"response_format": {"type": "json_object"}}
)

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are a senior support engineer. Analyze the input log context and RETURN ONLY JSON (no code block). "
            "Fields required: "
            "error_type (kebab-case, e.g. pre-persist, db-constraint, kafka-consumer), "
            "create_ticket (boolean), "
            "ticket_title (short, action-oriented, no prefixes like [Datadog]), "
            "ticket_description (markdown including: Problem summary; Possible Causes as bullets; Suggested Actions as bullets), "
            "severity (one of: low, medium, high)."
        ),
    ),
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
    contextual_log = (
        f"[Logger]: {logger if logger else 'unknown.logger'}\n"
        f"[Thread]: {thread if thread else 'unknown.thread'}\n"
        f"[Message]: {msg if msg else '<no message>'}\n"
        f"[Detail]: {detail if detail else '<no detail>'}"
    )

    response = chain.invoke({"log_message": contextual_log})
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
        print(f"🧠 Log analyzed → Type: {parsed.get('error_type')}, Create ticket: {parsed.get('create_ticket')}")
        import pprint
        print("🚨 Returned state from analyze_log:")
        pprint.pprint({**state, **parsed})
        return {**state, **parsed, "severity": parsed.get("severity", "low")}
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
    print("🔎 State received in create_ticket:")
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

    MAX_TICKETS_PER_RUN = 3
    if state.get("_tickets_created_in_run", 0) >= MAX_TICKETS_PER_RUN:
        print(f"🔔 Ticket creation limit reached for this run (max {MAX_TICKETS_PER_RUN}). Skipping.")
        return {**state, "message": f"⚠️ Ticket creation limit reached for this run (max {MAX_TICKETS_PER_RUN})."}

    state["_tickets_created_in_run"] = state.get("_tickets_created_in_run", 0) + 1

    debug_state = {k: (list(v) if isinstance(v, set) else v) for k, v in state.items()}

    if not title or not description:
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

    # Occurrence stats for this fingerprint (for description/comments)
    log_key = fp_source
    occ = (state.get("fp_counts") or {}).get(log_key, 1)
    win = state.get("window_hours", 48)

    extra_info = f"""
    ---
    🕒 Timestamp: {log_data.get('timestamp', 'N/A')}
    🧩 Logger: {log_data.get('logger', 'N/A')}
    🧵 Thread: {log_data.get('thread', 'N/A')}
    📝 Original Log: {log_data.get('message', 'N/A')}
    🔍 Detail: {log_data.get('detail', 'N/A')}
    📈 Occurrences in last {win}h: {occ}
    """

    full_description = f"{description.strip()}\n{extra_info.strip()}"

    from agent.jira import find_similar_ticket, comment_on_issue
    key, score, existing_summary = find_similar_ticket(title, state)
    if key:
        print(f"⚠️ Duplicate detected → {key} ({existing_summary}) with score {score:.2f}")
        if os.getenv("COMMENT_ON_DUPLICATE", "true").lower() in ("1", "true", "yes"):
            comment = (
                f"Detected by Datadog Logs Agent as a likely duplicate (score {score:.2f}).\n"
                f"Logger: {log_data.get('logger', 'N/A')} | Thread: {log_data.get('thread', 'N/A')} | Timestamp: {log_data.get('timestamp', 'N/A')}\n"
                f"Occurrences in last {win}h: {occ}\n"
                f"Original message: {log_data.get('message', 'N/A')}\n"
            )
            comment_on_issue(key, comment)
        processed.add(state["log_fingerprint"]) if state.get("log_fingerprint") else None
        _save_processed_fingerprints(processed)
        return {
            **state,
            "message": f"⚠️ Duplicate in Jira: {key} — {existing_summary}",
            "ticket_created": True
        }


    # Prepare payload for Jira ticket creation
    TICKET_FLAG = os.getenv("TICKET_FLAG", "")
    TICKET_LABEL = os.getenv("TICKET_LABEL", "")

    # labels to include (simulation payload only; real payload built in jira.create_ticket)
    labels = ["datadog-log"]
    try:
        from agent.jira import _normalize_log_message
        norm_msg = _normalize_log_message((state.get("log_data") or {}).get("message", ""))
        if norm_msg:
            import hashlib as _hashlib
            loghash = _hashlib.sha1(norm_msg.encode("utf-8")).hexdigest()[:12]
            labels.append(f"loghash-{loghash}")
    except Exception:
        pass

    etype = state.get("error_type")
    base_title = title.replace("**", "")
    MAX_TITLE = 120
    if len(base_title) > MAX_TITLE:
        base_title = base_title[: MAX_TITLE - 1] + "…"
    prefix = "[Datadog]" + (f"[{etype}]" if etype else "")
    clean_title = f"{prefix} {base_title}".strip()

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

    severity = (state.get("severity") or "low").lower()
    if severity == "low":
        priority_name = "Low"
    elif severity == "high":
        priority_name = "High"
    else:
        priority_name = "Medium"

    payload = {
        "fields": {
            "summary": clean_title,
            "description": description_adf,
            "labels": labels,
            "priority": {"name": priority_name},
            "customfield_10767": [{"value": "Team Vega"}],
        }
    }

    state["ticket_description"] = full_description
    state["ticket_title"] = clean_title
    state["jira_payload"] = payload

    # Fetch environment variable to determine if ticket should be auto-created
    auto_create = os.getenv("AUTO_CREATE_TICKET", "false").lower() == "true"

    if auto_create:
        try:
            # Attempt to create the Jira ticket
            print(f"🚀 Creating ticket in project: {os.getenv('JIRA_PROJECT_KEY')}")
            state = create_jira_ticket(state)
            issue_key = state.get("jira_response_key", None)
            if issue_key:
                jira_url = state.get("jira_response_url", None)
                print(f"✅ Jira ticket created: {issue_key}")
                if jira_url:
                    print(f"🔗 {jira_url}")
                # Mark as created only on success and persist fingerprint
                state["ticket_created"] = True
                if state.get("log_fingerprint"):
                    processed.add(state["log_fingerprint"])
                    _save_processed_fingerprints(processed)
            else:
                print("❌ No Jira issue key found after ticket creation attempt.")
                if "jira_response_raw" in state:
                    import json
                    print(json.dumps(state["jira_response_raw"], indent=2))
        except Exception as e:
            # Log any unexpected errors during ticket creation but do not raise
            print(f"❌ Failed to create Jira ticket due to unexpected error: {e}")
    else:
        # Simulation mode: do not create ticket, just print payload info
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

    logs = state.get("logs", [])

    # Compute per-run fingerprint counts once (logger|thread|message)
    if "fp_counts" not in state:
        counts = {}
        for lg in logs:
            k = f"{lg.get('logger','')}|{lg.get('thread','')}|{lg.get('message','')}"
            counts[k] = counts.get(k, 0) + 1
        state["fp_counts"] = counts
        try:
            import os as _os
            state["window_hours"] = int(_os.getenv("DATADOG_HOURS_BACK", "48"))
        except Exception:
            state["window_hours"] = 48

    index = state.get("log_index", 0)
    if index < len(logs):
        log = logs[index]
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