import os
import requests
import base64
from difflib import SequenceMatcher

from dotenv import load_dotenv

load_dotenv()

JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")
JIRA_USER = os.getenv("JIRA_USER")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")
TICKET_FLAG = os.getenv("TICKET_FLAG", "")
TICKET_LABEL = os.getenv("TICKET_LABEL", "")

def escape_for_jql(text: str) -> str:
    """
    Escape special characters in text for use in JQL queries.
    """
    special_chars = ['\\', '\'', '"', '~', '*', '+', '?', '&', '|', '!', '(', ')', '{', '}', '[', ']', '^']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def check_jira_for_ticket(summary: str, similarity_threshold: float = 0.9) -> bool:
    """
    Check Jira for a similar ticket using fuzzy string similarity to avoid duplicates.
    Returns True if a similar ticket exists.
    """
    if not all([JIRA_DOMAIN, JIRA_USER, JIRA_API_TOKEN, JIRA_PROJECT_KEY]):
        print("❌ Missing Jira configuration in .env")
        return False

    jql = f'project = {JIRA_PROJECT_KEY} ORDER BY created DESC'
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
        "maxResults": 50,
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
    # print("🚀 Entering create_ticket")
    import json
    debug_state = {k: (list(v) if isinstance(v, set) else v) for k, v in state.items()}
    # print("📥 State received in create_ticket:", json.dumps(debug_state, indent=2))

    description = state.get("ticket_description")
    title = state.get("ticket_title")

    if title is None or description is None:
        # print("⚠️ Skipping ticket creation due to missing title or description.")
        return state

    if not all([JIRA_DOMAIN, JIRA_USER, JIRA_API_TOKEN, JIRA_PROJECT_KEY]):
        # print("❌ Missing Jira configuration in .env")
        # print(f"ℹ️ Simulated ticket creation: {TICKET_FLAG} {title.replace('**', '')}")
        return state

    auth_string = f"{JIRA_USER}:{JIRA_API_TOKEN}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()

    url = f"https://{JIRA_DOMAIN}/rest/api/3/issue"
    headers = {
        "Authorization": f"Basic {auth_encoded}",
        "Content-Type": "application/json"
    }

    formatted_summary = f"{TICKET_FLAG} {title.replace('**', '')}"

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": formatted_summary,
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
            "labels": [TICKET_LABEL] if TICKET_LABEL else [],
            "priority": {"name": "Low"},
            "customfield_10767": [{"value": "Team Vega"}]
        }
    }

    print(f"🚀 Creating ticket in project: {JIRA_PROJECT_KEY}")

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        issue_key = response.json().get("key", "UNKNOWN")
        jira_url = f"https://{JIRA_DOMAIN}/browse/{issue_key}"
        print(f"✅ Jira ticket created: {issue_key}")
        print(f"🔗 {jira_url}")
        state["jira_response_key"] = issue_key
        state["jira_response_url"] = jira_url
    except requests.RequestException:
        print("❌ Failed to create Jira ticket.")

    return state