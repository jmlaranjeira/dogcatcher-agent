import os
import requests
import base64
import hashlib
import json
import pathlib

from difflib import SequenceMatcher
import importlib.util

_USE_RAPIDFUZZ = False
_rapidfuzz_spec = importlib.util.find_spec("rapidfuzz")
if _rapidfuzz_spec is not None:
    try:
        from rapidfuzz import fuzz  # type: ignore
        _USE_RAPIDFUZZ = True
    except Exception:
        _USE_RAPIDFUZZ = False

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

import re

_RE_WS = re.compile(r"\s+")
_RE_PUNCT = re.compile(r"[^a-z0-9]+")

def _normalize(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    t = t.replace("pre persist", "pre-persist").replace("prepersist", "pre-persist")
    t = _RE_PUNCT.sub(" ", t)
    t = _RE_WS.sub(" ", t).strip()
    return t

def _extract_text_from_description(desc):
    if not desc:
        return ""
    if isinstance(desc, str):
        return desc
    # Jira ADF
    try:
        parts = []
        for block in desc.get("content", []) or []:
            for item in block.get("content", []) or []:
                txt = item.get("text")
                if txt:
                    parts.append(txt)
        return "\n".join(parts)[:400]
    except Exception:
        return ""

def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if _USE_RAPIDFUZZ:
        return fuzz.token_set_ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()

def find_similar_ticket(summary: str, state: dict | None = None, similarity_threshold: float = 0.82):
    """Return (issue_key, score, issue_summary) of the best matching ticket if score>=threshold, else (None, 0.0, None)."""
    if not all([JIRA_DOMAIN, JIRA_USER, JIRA_API_TOKEN, JIRA_PROJECT_KEY]):
        print("❌ Missing Jira configuration in .env")
        return None, 0.0, None

    norm_summary = _normalize(summary)
    tokens = [t for t in norm_summary.split() if len(t) >= 4]
    if "pre" in tokens and "persist" in tokens:
        tokens.append("pre-persist")

    token_clauses = []
    for t in set(tokens[:6]):  # limit to a few tokens
        token_clauses.append(f'summary ~ "\\"{t}\\""')
        token_clauses.append(f'description ~ "\\"{t}\\""')
    token_clauses.append('labels = datadog-log')
    token_filter = " OR ".join(token_clauses) if token_clauses else "labels = datadog-log"

    jql = (
        f"project = {JIRA_PROJECT_KEY} AND statusCategory != Done AND created >= -180d AND (" +
        token_filter + ") ORDER BY created DESC"
    )
    print(f"🔍 JQL used: {jql}")

    auth_string = f"{JIRA_USER}:{JIRA_API_TOKEN}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()

    url = f"https://{JIRA_DOMAIN}/rest/api/3/search"
    headers = {"Authorization": f"Basic {auth_encoded}", "Content-Type": "application/json"}
    params = {"jql": jql, "maxResults": 200, "fields": "summary,description,labels,created,status"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        issues = response.json().get("issues", [])

        etype = (state or {}).get("error_type") if state else None
        logger = ((state or {}).get("log_data") or {}).get("logger") if state else None
        q_text = norm_summary

        best = (None, 0.0, None)
        for issue in issues:
            fields = issue.get("fields", {})
            s = _normalize(fields.get("summary", ""))
            d = _normalize(_extract_text_from_description(fields.get("description")))
            title_sim = _sim(q_text, s)
            desc_sim = _sim(q_text, d)

            score = 0.6 * title_sim + 0.3 * desc_sim
            if etype and (etype in s or etype in d):
                score += 0.10
            if logger and (logger.lower() in s or logger.lower() in d):
                score += 0.05
            if any(t in s or t in d for t in tokens):
                score += 0.05

            if score > best[1]:
                best = (issue.get("key"), score, fields.get("summary", ""))

        if best[0] and best[1] >= similarity_threshold:
            print(f"⚠️ Similar ticket found with score {best[1]:.2f}: {best[2]}")
            return best

        print("✅ No similar ticket found with advanced matching.")
        return None, 0.0, None
    except requests.RequestException as e:
        print(f"❌ Error checking Jira: {e}")
        return None, 0.0, None

def check_jira_for_ticket(summary: str, state: dict | None = None, similarity_threshold: float = 0.82) -> bool:
    key, score, _ = find_similar_ticket(summary, state, similarity_threshold)
    return key is not None

def comment_on_issue(issue_key: str, comment_text: str) -> bool:
    if not all([JIRA_DOMAIN, JIRA_USER, JIRA_API_TOKEN]):
        print("❌ Missing Jira configuration for commenting.")
        return False
    auth_string = f"{JIRA_USER}:{JIRA_API_TOKEN}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()
    url = f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}/comment"
    headers = {"Authorization": f"Basic {auth_encoded}", "Content-Type": "application/json"}
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": comment_text}]}
            ]
        }
    }
    try:
        resp = requests.post(url, headers=headers, json=body)
        print(f"🗨️ Comment response: {resp.status_code}")
        return resp.status_code in (200, 201)
    except requests.RequestException as e:
        print(f"❌ Failed to add comment: {e}")
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

    from agent.jira import find_similar_ticket, comment_on_issue
    key, score, existing_summary = find_similar_ticket(title, state)
    if key:
        print(f"⚠️ Duplicate detected → {key} ({existing_summary}) with score {score:.2f}")
        if os.getenv("COMMENT_ON_DUPLICATE", "true").lower() in ("1", "true", "yes"):
            comment = (
                f"Detected by Datadog Logs Agent as a likely duplicate (score {score:.2f}).\n"
                f"Logger: {log_data.get('logger', 'N/A')} | Thread: {log_data.get('thread', 'N/A')} | Timestamp: {log_data.get('timestamp', 'N/A')}\n"
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

    # Remove "**" from title if present
    clean_title = (title or "").replace("**", "").strip()

    # labels to include (simulation payload only; real payload built in jira.create_ticket)
    labels = ["datadog-log"]

    sev = (state.get("severity") or "low").lower()
    priority_name = "Low" if sev == "low" else ("High" if sev == "high" else "Medium")

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": clean_title,
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
            "priority": {"name": priority_name},
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