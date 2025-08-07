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
    """
    Simulate the creation of a Jira ticket with the given state information.
    """
    title = state.get("ticket_title")
    description = state.get("ticket_description")

    if title is None or description is None:
        print("⚠️ Skipping ticket creation due to missing title or description.")
        return state

    print("\n🪄 Simulating JIRA Ticket Creation...")
    print(f"📌 Title      : {title}")
    print(f"📝 Description: {description}")
    print("✅ Ticket created successfully (simulated)\n")

    return state