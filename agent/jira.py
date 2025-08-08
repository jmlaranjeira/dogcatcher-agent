import os
import requests
import base64
import hashlib
import json
import pathlib

from difflib import SequenceMatcher

from dotenv import load_dotenv

load_dotenv()

JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")
JIRA_USER = os.getenv("JIRA_USER")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")
TICKET_FLAG = os.getenv("TICKET_FLAG", "")
TICKET_LABEL = os.getenv("TICKET_LABEL", "")

_CACHE_PATH = pathlib.Path(".agent_cache/processed_logs.json")


def _load_processed_fingerprints():
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def _save_processed_fingerprints(fps):
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(fps)), f, ensure_ascii=False, indent=2)


def check_jira_for_ticket(summary: str, similarity_threshold: float = 0.9) -> bool:
    """
    Check Jira for a similar ticket using fuzzy string similarity to avoid duplicates.
    Returns True if a similar ticket exists.
    """
    if not all([JIRA_DOMAIN, JIRA_USER, JIRA_API_TOKEN, JIRA_PROJECT_KEY]):
        print("❌ Missing Jira configuration in .env")
        return False

    jql = f"project = {JIRA_PROJECT_KEY} AND statusCategory != Done ORDER BY created DESC"
    print(f"🔍 JQL used: {jql}")

    auth_string = f"{JIRA_USER}:{JIRA_API_TOKEN}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()

    url = f"https://{JIRA_DOMAIN}/rest/api/3/search"
    headers = {
        "Authorization": f"Basic {auth_encoded}",
        "Content-Type": "application/json"
    }

    params = {
        "jql": jql,
        "maxResults": 100,
        "fields": "summary"
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        issues = response.json().get("issues", [])

        for issue in issues:
            existing_summary = issue.get("fields", {}).get("summary", "")
            similarity = SequenceMatcher(None, summary, existing_summary).ratio()
            if similarity >= similarity_threshold:
                print(f"⚠️ Similar ticket found (similarity {similarity:.2f}): {existing_summary}")
                return True

        print("✅ No similar ticket found.")
        return False
    except requests.RequestException as e:
        print(f"❌ Error checking Jira: {e}")
        return False


def create_ticket(state: dict) -> dict:
    print(f"🛠️ Entered create_ticket() | AUTO_CREATE_TICKET={os.getenv('AUTO_CREATE_TICKET')}")

    assert "ticket_title" in state and "ticket_description" in state, "Missing LLM fields before jira.create_ticket"

    description = state.get("ticket_description")
    title = state.get("ticket_title")
    print(f"🧾 Title to create: {title}")
    print(f"📝 Description to create: {description[:160]}{'...' if description and len(description) > 160 else ''}")

    if state.get("ticket_created"):
        print("🔔 Ya se ha creado un ticket en esta ejecución (incluye simulación). Se omite crear más.")
        return {**state, "message": "⚠️ Solo se permite crear un ticket por ejecución (incluye simulación)."}

    if title is None or description is None:
        return state

    if not all([JIRA_DOMAIN, JIRA_USER, JIRA_API_TOKEN, JIRA_PROJECT_KEY]):
        return state

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

    if check_jira_for_ticket(title):
        print("⚠️ Duplicate ticket detected in Jira, skipping creation.")
        processed.add(state["log_fingerprint"]) if state.get("log_fingerprint") else None
        _save_processed_fingerprints(processed)
        return {**state, "message": "⚠️ Duplicate ticket found in Jira, skipping creation.", "ticket_created": True}

    # labels to include (simulation payload only; real payload built in jira.create_ticket)
    labels = [TICKET_LABEL] if TICKET_LABEL else []
    if state.get("log_fingerprint"):
        labels.append(f"logfp-{state['log_fingerprint']}")

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": f"{TICKET_FLAG} {title.replace('**', '')}".strip(),
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "text": description,
                                "type": "text"
                            }
                        ]
                    }
                ]
            },
            "issuetype": {"name": "Bug"},
            "labels": labels,
            "priority": {"name": "Low"},
            "customfield_10767": [{"value": "Team Vega"}]
        }
    }

    AUTO_CREATE_TICKET = os.getenv("AUTO_CREATE_TICKET", "").lower() in ("1", "true", "yes")

    if AUTO_CREATE_TICKET:
        print(f"🚀 Creating ticket in project: {JIRA_PROJECT_KEY}")
        print(f"🧾 Summary sent to Jira: {payload['fields']['summary']}")
        try:
            auth_string = f"{JIRA_USER}:{JIRA_API_TOKEN}"
            auth_encoded = base64.b64encode(auth_string.encode()).decode()

            url = f"https://{JIRA_DOMAIN}/rest/api/3/issue"
            headers = {
                "Authorization": f"Basic {auth_encoded}",
                "Content-Type": "application/json"
            }
            response = requests.post(url, headers=headers, json=payload)
            print(f"🔴 Jira API raw response code: {response.status_code}")
            print(f"🔴 Jira API raw response body: {response.text}")
            response.raise_for_status()
            response_json = response.json()
            issue_key = response_json.get("key", "UNKNOWN")
            jira_url = f"https://{JIRA_DOMAIN}/browse/{issue_key}"
            print(f"✅ Jira ticket created: {issue_key}")
            print(f"🔗 {jira_url}")
            state["jira_response_key"] = issue_key
            state["jira_response_url"] = jira_url
            state["jira_response_raw"] = response_json
            processed.add(state["log_fingerprint"]) if state.get("log_fingerprint") else None
            _save_processed_fingerprints(processed)
            return state
        except requests.RequestException as e:
            print(f"❌ Failed to create Jira ticket. Exception: {str(e)}")
            return state
    else:
        print(f"ℹ️ Simulated ticket creation: {payload['fields']['summary']}")
        print("✅ Ticket creation skipped (simulation mode enabled)\n")
        state["ticket_created"] = True
        persist_sim = os.getenv("PERSIST_SIM_FP", "false").lower() in ("1", "true", "yes")
        if persist_sim and state.get("log_fingerprint"):
            processed.add(state["log_fingerprint"])
            _save_processed_fingerprints(processed)
        return state