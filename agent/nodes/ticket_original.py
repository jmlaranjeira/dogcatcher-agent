"""Ticket orchestration node (create_ticket, helpers)."""
from typing import Dict, Any, Set as _Set
import os
import json
import hashlib
import pathlib
import datetime

from agent.jira import create_ticket as create_jira_ticket, find_similar_ticket, comment_on_issue
from agent.jira.utils import normalize_log_message as _normalize_log_message
from agent.jira.utils import should_comment as _should_comment, update_comment_timestamp as _touch_comment
from agent.jira.utils import priority_name_from_severity as _priority_name_from_severity

# Module-level constants
MAX_TITLE = 120

from datetime import timezone as _tz

def _utcnow_iso() -> str:
    """UTC timestamp in ISO 8601 with timezone info."""
    return datetime.datetime.now(_tz.utc).isoformat()

def _map_severity_from_env(error_type: str, current: str) -> str:
    """Optionally override severity via SEVERITY_RULES_JSON mapping."""
    try:
        raw = os.getenv("SEVERITY_RULES_JSON", "").strip()
        if not raw:
            return current
        rules = json.loads(raw)
        if not isinstance(rules, dict):
            return current
        mapped = rules.get((error_type or "").strip().lower())
        if isinstance(mapped, str) and mapped.lower() in ("low", "medium", "high"):
            return mapped.lower()
        return current
    except json.JSONDecodeError:
        return current


def _maybe_escalate_severity_by_occurrences(current_sev: str, occ: int) -> str:
    """Optionally escalate severity based on occurrences in window.

    Controlled by env vars:
    - OCC_ESCALATE_ENABLED (default: false)
    - OCC_ESCALATE_THRESHOLD (default: 10)
    - OCC_ESCALATE_TO (one of low|medium|high, default: high)
    """
    try:
        enabled = (os.getenv("OCC_ESCALATE_ENABLED", "false") or "").lower() in ("1", "true", "yes")
        threshold = int(os.getenv("OCC_ESCALATE_THRESHOLD", "10") or "10")
        target = (os.getenv("OCC_ESCALATE_TO", "high") or "high").strip().lower()
    except Exception:
        enabled, threshold, target = False, 10, "high"

    if not enabled or occ < threshold:
        return current_sev

    order = {"low": 0, "medium": 1, "high": 2}
    cur = (current_sev or "low").strip().lower()
    tgt = target if target in order else "high"
    return target if order.get(tgt, 2) > order.get(cur, 0) else current_sev


# === Patch: Insert new helper functions ===

def _append_audit(*, decision: str, state: Dict[str, Any], fingerprint: str, occ: int, jira_key: str | None = None, duplicate: bool = False, create: bool = False, message: str = "") -> None:
    entry = {
        "timestamp": _utcnow_iso(),
        "fingerprint": fingerprint,
        "error_type": state.get("error_type"),
        "severity": state.get("severity"),
        "create_ticket": bool(create),
        "duplicate": bool(duplicate),
        "decision": decision,
        "existing_issue_key": jira_key,
        "jira_key": jira_key,
        "occurrences": occ,
        "message": message,
    }
    _append_audit_log(entry)


def _maybe_comment_duplicate(issue_key: str, score: float, log_data: Dict[str, Any], win: int, occ: int) -> None:
    if os.getenv("COMMENT_ON_DUPLICATE", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        cooldown_min = int(os.getenv("COMMENT_COOLDOWN_MINUTES", "120") or "0")
    except Exception:
        cooldown_min = 120
    if _should_comment(issue_key, cooldown_min):
        comment = (
            f"Detected by Datadog Logs Agent as a likely duplicate (score {score:.2f}).\n"
            f"Logger: {log_data.get('logger', 'N/A')} | Thread: {log_data.get('thread', 'N/A')} | Timestamp: {log_data.get('timestamp', 'N/A')}\n"
            f"Occurrences in last {win}h: {occ}\n"
            f"Original message: {log_data.get('message', 'N/A')}\n"
        )
        comment_on_issue(issue_key, comment)
        _touch_comment(issue_key)


def _build_extra_info(log_data: Dict[str, Any], win: int, occ: int) -> str:
    return (
        f"""
    ---
    🕒 Timestamp: {log_data.get('timestamp', 'N/A')}
    🧩 Logger: {log_data.get('logger', 'N/A')}
    🧵 Thread: {log_data.get('thread', 'N/A')}
    📝 Original Log: {log_data.get('message', 'N/A')}
    🔍 Detail: {log_data.get('detail', 'N/A')}
    📈 Occurrences in last {win}h: {occ}
    """.strip()
    )


def _build_labels_and_title(etype: str, title: str, norm_msg: str | None) -> tuple[list[str], str]:
    labels: list[str] = ["datadog-log"]
    if norm_msg:
        loghash = hashlib.sha1(norm_msg.encode("utf-8")).hexdigest()[:12]
        labels.append(f"loghash-{loghash}")

    base_title = title.replace("**", "")

    aggregate_blob = os.getenv("AGGREGATE_BLOB_NOT_FOUND", "true").lower() in ("1", "true", "yes")
    if aggregate_blob and etype == "blob-not-found":
        base_title = "Investigate Blob Not Found errors (aggregated)"
        labels.append("aggregate-blob-not-found")

    aggregate_email = os.getenv("AGGREGATE_EMAIL_NOT_FOUND", "false").lower() in ("1", "true", "yes")
    if aggregate_email and etype == "email-not-found":
        base_title = "Investigate Email Not Found errors (aggregated)"
        labels.append("aggregate-email-not-found")

    aggregate_kafka = os.getenv("AGGREGATE_KAFKA_CONSUMER", "false").lower() in ("1", "true", "yes")
    if aggregate_kafka and etype == "kafka-consumer":
        base_title = "Investigate Kafka Consumer errors (aggregated)"
        labels.append("aggregate-kafka-consumer")

    if len(base_title) > MAX_TITLE:
        base_title = base_title[: MAX_TITLE - 1] + "…"
    prefix = "[Datadog]" + (f"[{etype}]" if etype else "")
    clean_title = f"{prefix} {base_title}".strip()
    return labels, clean_title


def _build_payload(clean_title: str, full_description: str, severity: str) -> dict:
    priority_name = _priority_name_from_severity(severity)

    description_adf = {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": full_description}
                ],
            }
        ],
    }
    return {
        "fields": {
            "summary": clean_title,
            "description": description_adf,
            "priority": {"name": priority_name},
            "customfield_10767": [{"value": "Team Vega"}],
        }
    }

def _do_simulation(state: Dict[str, Any], clean_title: str, full_description: str, payload: dict,
                   fingerprint: str, occ: int, processed: _Set[str]) -> Dict[str, Any]:
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

    _append_audit(decision="simulated", state=state, fingerprint=fingerprint, occ=occ, jira_key=None,
                  duplicate=False, create=False, message="Ticket creation simulated (dry run)")

    state["message"] = (
        f"🧪 Simulation only (no ticket created):\n📌 Title: {clean_title}\n📝 Description: {full_description}"
    )
    return state


def _do_auto_create(state: Dict[str, Any], clean_title: str, full_description: str, payload: dict,
                    fingerprint: str, occ: int, processed: _Set[str], max_tickets: int) -> Dict[str, Any]:
    # Enforce per-run cap only for real creation
    if max_tickets > 0 and state.get("_tickets_created_in_run", 0) >= max_tickets:
        print(f"🔔 Ticket creation limit reached for this run (max {max_tickets}). Skipping.")
        _append_audit(decision="cap-reached", state=state, fingerprint=fingerprint, occ=occ, jira_key=None,
                      duplicate=False, create=False, message=f"Per-run ticket cap reached ({max_tickets})")
        state["message"] = f"⚠️ Ticket creation limit reached for this run (max {max_tickets})."
        state["ticket_created"] = True
        return state

    try:
        print(f"🚀 Creating ticket in project: {os.getenv('JIRA_PROJECT_KEY')}")
        state = create_jira_ticket(state)
        issue_key = state.get("jira_response_key")
        if issue_key:
            jira_url = state.get("jira_response_url")
            print(f"✅ Jira ticket created: {issue_key}")
            if jira_url:
                print(f"🔗 {jira_url}")
            state["ticket_created"] = True
            if state.get("log_fingerprint"):
                processed.add(state["log_fingerprint"])
                _save_processed_fingerprints(processed)
            state["_tickets_created_in_run"] = state.get("_tickets_created_in_run", 0) + 1
            _append_audit(decision="created", state=state, fingerprint=fingerprint, occ=occ, jira_key=issue_key,
                          duplicate=False, create=True, message="Ticket created successfully")
            state["message"] = (
                f"✅ Ticket created:\n📌 Title: {clean_title}\n📝 Description: {full_description}"
            )
        else:
            print("❌ No Jira issue key found after ticket creation attempt.")
            if "jira_response_raw" in state:
                print(json.dumps(state["jira_response_raw"], indent=2))
    except Exception as e:
        print(f"❌ Failed to create Jira ticket due to unexpected error: {e}")
    return state

# Local caches/paths used by ticket creation
_CACHE_PATH = pathlib.Path(".agent_cache/processed_logs.json")
_AUDIT_LOG_PATH = pathlib.Path(".agent_cache/audit_logs.jsonl")


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


def _append_audit_log(entry: dict) -> None:
    _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")




# --- Additional helpers to reduce create_ticket complexity ---

def _validate_and_preview(state: Dict[str, Any]) -> tuple[bool, Dict[str, Any] | None, str | None, str | None]:
    """Ensure LLM fields exist and print a short preview. Returns
    (ok, early_return_state, title, description).
    """
    print("\n🔎 Validating ticket fields from LLM…")
    if "ticket_title" not in state or "ticket_description" not in state:
        msg = "❌ Missing LLM fields before jira.create_ticket"
        print(msg)
        return False, {**state, "message": msg, "ticket_created": True}, None, None

    title = state.get("ticket_title")
    description = state.get("ticket_description")

    print(f"🧾 Title to create: {title}")
    if description is None:
        msg = "❌ ticket_description is None. Skipping ticket creation."
        print(msg)
        return False, {**state, "message": msg, "ticket_created": True}, None, None

    preview = description[:160] + ("…" if description and len(description) > 160 else "")
    print(f"📝 Description to create: {preview}")

    if not title or not description:
        return False, {
            **state,
            "message": "⚠️ Ticket title or description missing. Ticket not created.",
            "ticket_created": True,
        }, None, None

    return True, None, title, description


def _prepare_context(state: Dict[str, Any], title: str, description: str) -> tuple[str, str, _Set[str], int, int, str]:
    """Compute severity override, fingerprint + processed set, occurrences/window
    and return (fingerprint, full_description, processed, occ, win, fp_source).
    """
    log_data = state.get("log_data", {})

    # Optional severity override and occurrence-based escalation
    state["severity"] = _map_severity_from_env(state.get("error_type"), state.get("severity"))

    # Stable fingerprint across runs
    fp_source = f"{log_data.get('logger','')}|{log_data.get('thread','')}|{log_data.get('message','')}"
    fingerprint = hashlib.sha1(fp_source.encode("utf-8")).hexdigest()[:12]
    processed = _load_processed_fingerprints()

    # Occurrence stats for description/comments
    occ = (state.get("fp_counts") or {}).get(fp_source, 1)
    win = state.get("window_hours", 48)

    # Occurrence-based escalation (optional)
    state["severity"] = _maybe_escalate_severity_by_occurrences(state.get("severity"), occ)

    # Extra info block
    extra_info = _build_extra_info(log_data, win, occ)
    full_description = f"{description.strip()}\n{extra_info}"

    # Put fingerprint into state so Jira payload can add a label
    state["log_fingerprint"] = fingerprint

    return fingerprint, full_description, processed, occ, win, fp_source


def _check_fingerprint_dup(state: Dict[str, Any], fingerprint: str, processed: _Set[str], occ: int, fp_source: str) -> Dict[str, Any] | None:
    if fingerprint in processed:
        print(f"🔁 Skipping ticket creation: fingerprint already processed: {fingerprint}")
        _append_audit(
            decision="duplicate-fingerprint",
            state=state,
            fingerprint=fingerprint,
            occ=occ,
            jira_key=None,
            duplicate=True,
            create=False,
            message="Duplicate log skipped (fingerprint cache)",
        )
        return {**state, "message": "⚠️ Log already processed previously (fingerprint match).", "ticket_created": True}
    return None


def _check_llm_no_create(state: Dict[str, Any], fingerprint: str, occ: int) -> Dict[str, Any] | None:
    if not state.get("create_ticket", False):
        _append_audit(
            decision="no-create",
            state=state,
            fingerprint=fingerprint,
            occ=occ,
            jira_key=None,
            duplicate=False,
            create=False,
            message="LLM chose not to create a ticket",
        )
        return {**state, "message": "ℹ️ LLM decision: do not create a ticket for this log.", "ticket_created": True}
    return None


def _check_jira_duplicate(title: str, state: Dict[str, Any], win: int, occ: int, processed: _Set[str], fingerprint: str) -> Dict[str, Any] | None:
    log_data = state.get("log_data", {})
    key, score, existing_summary = find_similar_ticket(title, state)
    if not key:
        return None

    print(f"⚠️ Duplicate detected → {key} ({existing_summary}) with score {score:.2f}")
    _maybe_comment_duplicate(key, score, log_data, win, occ)

    if state.get("log_fingerprint"):
        processed.add(state["log_fingerprint"])
        _save_processed_fingerprints(processed)

    _append_audit(
        decision="duplicate-jira",
        state=state,
        fingerprint=fingerprint,
        occ=occ,
        jira_key=key,
        duplicate=True,
        create=False,
        message=f"Duplicate in Jira: {key} — {existing_summary}",
    )
    return {**state, "message": f"⚠️ Duplicate in Jira: {key} — {existing_summary}", "ticket_created": True}


def create_ticket(state: Dict[str, Any]) -> Dict[str, Any]:
    print(f"🛠️ Entered create_ticket() | AUTO_CREATE_TICKET={os.getenv('AUTO_CREATE_TICKET')}")
    import pprint
    print("🔎 State received in create_ticket:")
    pprint.pprint(state)

    # Initialize run counter
    state.setdefault("_tickets_created_in_run", 0)

    # 1) Validate
    ok, early, title, description = _validate_and_preview(state)
    if not ok:
        return early  # type: ignore[return-value]

    # 2) Prepare context and compute fingerprint
    fingerprint, full_description, processed, occ, win, fp_source = _prepare_context(state, title, description)

    # 3) Fast duplicate by fingerprint
    early = _check_fingerprint_dup(state, fingerprint, processed, occ, fp_source)
    if early:
        return early

    # 4) Respect LLM decision
    early = _check_llm_no_create(state, fingerprint, occ)
    if early:
        return early

    # 5) Duplicate in Jira (includes direct log match short-circuit)
    early = _check_jira_duplicate(title, state, win, occ, processed, fingerprint)
    if early:
        return early

    # 6) Build labels/title/payload
    try:
        norm_msg = _normalize_log_message((state.get("log_data") or {}).get("message", ""))
    except Exception:
        norm_msg = None
    etype = (state.get("error_type") or "").strip().lower()
    labels, clean_title = _build_labels_and_title(etype, title, norm_msg)

    payload = _build_payload(clean_title, full_description, state.get("severity"))
    payload["fields"]["labels"] = labels

    state["ticket_description"] = full_description
    state["ticket_title"] = clean_title
    state["jira_payload"] = payload

    # 7) Create (or simulate) with per-run cap only for real creation
    auto_create = os.getenv("AUTO_CREATE_TICKET", "false").lower() == "true"
    try:
        max_tickets = int(os.getenv("MAX_TICKETS_PER_RUN", "3") or "0")
    except Exception:
        max_tickets = 3

    if auto_create:
        state = _do_auto_create(state, clean_title, full_description, payload, fingerprint, occ, processed, max_tickets)
    else:
        state = _do_simulation(state, clean_title, full_description, payload, fingerprint, occ, processed)

    return {**state, "message": state.get("message"), "ticket_created": True}
