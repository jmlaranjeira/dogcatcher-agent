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
        return "\n".join(parts)[:4000]
    except Exception:
        return ""

# --- Inserted helper functions ---
def _extract_original_log(desc) -> str:
    """Extracts the value after "Original Log:" from a Jira description (ADF or plain text)."""
    text = _extract_text_from_description(desc)
    if not text:
        return ""
    for line in text.splitlines():
        m = re.match(r"\s*Original\s+Log:\s*(.*)\s*$", line, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""

def _normalize_log_message(text: str) -> str:
    """Normalize log messages by removing volatile tokens like UUIDs/IDs, timestamps, and collapsing whitespace."""
    if not text:
        return ""
    t = text.lower()
    # Redact emails, URLs, and JWT-like tokens first (so they don't influence hashes/similarity)
    t = re.sub(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", " <email> ", t)
    t = re.sub(r"\bhttps?://[^\s]+", " <url> ", t)
    # JWT-like tokens (three base64url segments with dots)
    t = re.sub(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", " <token> ", t)
    # Remove UUIDs (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
    t = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", " ", t)
    # Remove 24-hex object ids (e.g., Mongo IDs)
    t = re.sub(r"\b[0-9a-f]{24}\b", " ", t)
    # Remove ISO datetime like 2025-08-09T12:34:56.789Z or 2025-08-09 12:34:56
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}[tT ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b", " ", t)
    # Remove bracketed timestamps [2025-08-09 12:34:56,123]
    t = re.sub(r"\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?\]?", " ", t)
    # Replace long numbers/hashes with a placeholder
    t = re.sub(r"\b\d{5,}\b", " ", t)
    t = re.sub(r"\b[a-f0-9]{10,}\b", " ", t)
    # Collapse punctuation and whitespace
    t = _RE_PUNCT.sub(" ", t)
    t = _RE_WS.sub(" ", t).strip()
    return t
# --- End inserted helper functions ---

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
    current_log_msg = ((state or {}).get("log_data") or {}).get("message", "")
    norm_current_log = _normalize_log_message(current_log_msg)

    # Fast path: exact label lookup for normalized log hash
    if norm_current_log:
        try:
            loghash = hashlib.sha1(norm_current_log.encode("utf-8")).hexdigest()[:12]
            jql_hash = (
                f"project = {JIRA_PROJECT_KEY} AND statusCategory != Done AND labels = loghash-{loghash} "
                f"ORDER BY created DESC"
            )
            params_hash = {"jql": jql_hash, "maxResults": 10, "fields": "summary,description,labels,created,status"}
            resp_hash = requests.get(url, headers=headers, params=params_hash)
            resp_hash.raise_for_status()
            issues_hash = resp_hash.json().get("issues", [])
            if issues_hash:
                first = issues_hash[0]
                print(f"⚠️ Exact duplicate by label loghash-{loghash}: {first.get('key')}")
                return first.get("key"), 1.00, first.get("fields", {}).get("summary", "")
        except requests.RequestException as _e:
            print(f"ℹ️ Hash label lookup failed (non-fatal): {_e}")
        except Exception as _e:
            print(f"ℹ️ Hash computation/lookup error (non-fatal): {_e}")

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
            # --- Direct Original Log similarity check (short-circuit) ---
            log_sim = None
            orig_log_issue = _extract_original_log(fields.get("description"))
            norm_issue_log = _normalize_log_message(orig_log_issue)
            if norm_current_log and norm_issue_log:
                log_sim = _sim(norm_current_log, norm_issue_log)
                if log_sim >= 0.90:
                    print(f"⚠️ Direct log match found (sim={log_sim:.2f}) against {issue.get('key')} — short-circuiting as duplicate.")
                    return issue.get("key"), 1.00, fields.get("summary", "")
            # --- end direct check ---
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

            if log_sim is not None and 0.70 <= log_sim < 0.90:
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

    MAX_TICKETS_PER_RUN = 3
    if state.get("_tickets_created_in_run", 0) >= MAX_TICKETS_PER_RUN:
        print(f"🔔 Se alcanzó el máximo de {MAX_TICKETS_PER_RUN} tickets en esta ejecución. Se omite crear más.")
        return {**state, "message": f"⚠️ Se alcanzó el máximo de {MAX_TICKETS_PER_RUN} tickets en esta ejecución."}

    state["_tickets_created_in_run"] = state.get("_tickets_created_in_run", 0) + 1

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

    # Base labels
    labels = ["datadog-log"]
    # Add stable label for exact duplicate detection
    try:
      norm_msg = _normalize_log_message((state.get("log_data") or {}).get("message", ""))
      if norm_msg:
          loghash = hashlib.sha1(norm_msg.encode("utf-8")).hexdigest()[:12]
          labels.append(f"loghash-{loghash}")
    except Exception:
      pass

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