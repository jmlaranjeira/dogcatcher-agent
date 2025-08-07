import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

DATADOG_API_KEY = os.getenv("DATADOG_API_KEY")
DATADOG_APP_KEY = os.getenv("DATADOG_APP_KEY")
DATADOG_SITE = os.getenv("DATADOG_SITE", "datadoghq.eu")

HEADERS = {
    "DD-API-KEY": DATADOG_API_KEY,
    "DD-APPLICATION-KEY": DATADOG_APP_KEY,
    "Content-Type": "application/json",
}

MAX_LOG_DETAIL_LENGTH = 300

# Fetch error logs from Datadog based on service and environment parameters.
def get_logs(service="dehnproject", env="prod", hours_back=24, limit=10):
    now = datetime.utcnow()
    start = now - timedelta(hours=hours_back)

    url = f"https://api.{DATADOG_SITE}/api/v2/logs/events/search"
    payload = {
        "filter": {
            "from": start.isoformat() + "Z",
            "to": now.isoformat() + "Z",
            "query": f"service:{service} env:{env} status:error",
        },
        "page": {"limit": limit},
    }

    response = requests.post(url, json=payload, headers=HEADERS)
    response.raise_for_status()

    results = []
    for log in response.json().get("data", []):
        attr = log["attributes"]
        msg = attr.get("message", "<no message>")
        logger_name = attr.get("attributes", {}).get("logger", {}).get("name", "unknown.logger")
        thread_name = attr.get("attributes", {}).get("logger", {}).get("thread_name", "unknown.thread")
        detail = attr.get("attributes", {}).get("properties", {}).get("Log", "no detailed log")
        if len(detail) > MAX_LOG_DETAIL_LENGTH:
            detail = detail[:MAX_LOG_DETAIL_LENGTH] + "... [truncated]"

        results.append({
            "logger": logger_name,
            "thread": thread_name,
            "log_message": msg,
            "detail": detail
        })

    return results

def extract_stable_query_key(msg):
    for token in ["__UUID__", "__HASH__", "__NUM__"]:
        if token in msg:
            return msg.split(token)[0].strip()
    return msg.strip()

def escape_for_jql(text):
    import re
    return re.sub(r"([\\'\"~*+?&|!(){}\[\]^])", r"\\\\\1", text)

if __name__ == "__main__":
    logs = get_logs()
    for i, log in enumerate(logs, start=1):
        print(f"\nLog #{i}")
        print(f"Logger  : {log['logger']}")
        print(f"Thread  : {log['thread']}")
        print(f"Message : {log['log_message']}")
        print(f"Detail  : {log['detail']}")
        print("-" * 60)