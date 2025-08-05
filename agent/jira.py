import os
import requests
import base64

from dotenv import load_dotenv

load_dotenv()

JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")
JIRA_USER = os.getenv("JIRA_USER")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")

def escape_for_jql(text: str) -> str:
    special_chars = ['\\', '\'', '"', '~', '*', '+', '?', '&', '|', '!', '(', ')', '{', '}', '[', ']', '^']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def check_jira_for_ticket(summary: str) -> bool:
    if not all([JIRA_DOMAIN, JIRA_USER, JIRA_API_TOKEN, JIRA_PROJECT_KEY]):
        print("❌ Missing Jira configuration in .env")
        return False

    escaped_summary = escape_for_jql(summary)
    jql = f'project = {JIRA_PROJECT_KEY} AND summary ~ "{escaped_summary}"'

    auth_string = f"{JIRA_USER}:{JIRA_API_TOKEN}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()

    url = f"https://{JIRA_DOMAIN}/rest/api/3/search"
    headers = {
        "Authorization": f"Basic {auth_encoded}",
        "Content-Type": "application/json"
    }

    params = {
        "jql": jql,
        "maxResults": 1
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        issues = response.json().get("issues", [])
        return len(issues) > 0
    except requests.RequestException as e:
        print(f"❌ Error checking Jira: {e}")
        return False

def create_ticket(state: dict) -> dict:
    title = state.get("ticket_title")
    description = state.get("ticket_description")

    if title and check_jira_for_ticket(title):
        print("⚠️ Ticket already exists in Jira. Skipping creation.\n")
        return state

    print("\n🪄 Simulating JIRA Ticket Creation...")
    print(f"📌 Title      : {title}")
    print(f"📝 Description: {description}")
    print("✅ Ticket created successfully (simulated)\n")

    return state