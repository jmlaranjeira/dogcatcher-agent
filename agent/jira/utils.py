"""Utilities for normalization, fingerprinting, and local fingerprint cache."""

from __future__ import annotations
import hashlib
import json
import pathlib
import re
from typing import Iterable, Set

_CACHE_DIR = pathlib.Path(".agent_cache")


def _get_cache_dir(team_id: str | None = None) -> pathlib.Path:
    """Return the cache directory, scoped to team when in multi-tenant mode."""
    if team_id:
        return _CACHE_DIR / "teams" / team_id
    return _CACHE_DIR


def _cache_path(team_id: str | None = None) -> pathlib.Path:
    return _get_cache_dir(team_id) / "processed_logs.json"


def _comment_cache_path(team_id: str | None = None) -> pathlib.Path:
    return _get_cache_dir(team_id) / "jira_comments.json"


_RE_WS = re.compile(r"\s+")
_RE_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    t = t.replace("pre persist", "pre-persist").replace("prepersist", "pre-persist")
    t = _RE_PUNCT.sub(" ", t)
    t = _RE_WS.sub(" ", t).strip()
    return t


def extract_text_from_description(desc):
    if not desc:
        return ""
    if isinstance(desc, str):
        return desc
    # Jira ADF to plain text
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


def normalize_log_message(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    # Normalize variable blob/file references commonly seen in messages
    # Example: "failed to get file size by name <uuid>_<name>.dpplan, cause: status code 404, (blobnotfound)"
    try:
        # Collapse the variable segment after "by name" up to the next comma
        t = re.sub(r"(failed to get file size by name)\s+[^,]+", r"\1 <blob>", t)
        # Replace dpplan-like filenames with a placeholder
        t = re.sub(r"\b[a-z0-9._-]+\.dpplan\b", " <file>", t)
    except Exception:
        pass
    t = re.sub(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", " <email> ", t)
    t = re.sub(r"\bhttps?://[^\s]+", " <url> ", t)
    t = re.sub(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", " <token> ", t)
    t = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", " ", t
    )
    t = re.sub(r"\b[0-9a-f]{24}\b", " ", t)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}[tT ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b", " ", t)
    t = re.sub(r"\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?\]?", " ", t)
    t = re.sub(r"\b\d{5,}\b", " ", t)
    t = re.sub(r"\b[a-f0-9]{10,}\b", " ", t)
    # Collapse SQL duplicate-entry values (Hibernate binary key representations)
    # The binary value between 'duplicate entry' and 'for key' varies per row;
    # it may contain escaped hex (\xHH), embedded quotes, and short ASCII fragments.
    # e.g. "duplicate entry 'e\xBB\xB2'\x97lC[...' for key" → "duplicate entry for key"
    t = re.sub(r"(duplicate entry\s+).*?(for key)", r"\1\2", t)
    # Collapse remaining escaped hex byte sequences (\xHH) that may appear elsewhere
    t = re.sub(r"\\x[0-9a-f]{2}", " ", t)
    t = _RE_PUNCT.sub(" ", t)
    t = _RE_WS.sub(" ", t).strip()
    return t


def sanitize_for_jira(text: str) -> str:
    """Sanitize a log message before injecting it into Jira content.

    Unlike normalize_log_message (which also lowercases and strips punctuation
    for hashing), this function preserves readability while masking PII:
    emails, URLs, UUIDs, long hex strings, JWTs, and IP addresses.
    """
    if not text:
        return ""
    t = text
    t = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<email>", t)
    t = re.sub(r"\bhttps?://[^\s]+", "<url>", t)
    # JWTs and similar dot-separated tokens (3+ segments of base64-like chars)
    t = re.sub(
        r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b",
        "<token>",
        t,
    )
    t = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "<uuid>",
        t,
    )
    t = re.sub(r"\b[0-9a-fA-F]{24,}\b", "<hex>", t)
    # IPv4 addresses
    t = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<ip>", t)
    return t


def compute_loghash(raw_message: str, error_stack: str = "") -> str:
    """Compute a 12-char loghash from a raw log message.

    Normalizes the message first, then hashes. Used as a Jira label
    for fast duplicate lookup.

    When *error_stack* is provided the exception class name (last segment
    of the dotted type, e.g. ``IllegalArgumentException``) is appended to
    the normalized text before hashing so that identical messages with
    different root-cause exceptions produce distinct loghashes.
    """
    norm = normalize_log_message(raw_message)
    if not norm:
        return ""
    if error_stack:
        first_line = error_stack.strip().split("\n")[0]
        exc_type = first_line.split(":")[0].strip().split(".")[-1]
        if exc_type:
            norm = f"{norm}|{exc_type.lower()}"
    return hashlib.sha1(norm.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def compute_fingerprint(error_type: str, raw_message: str) -> str:
    """Compute a 12-char fingerprint for a log entry.

    Combines error_type (from LLM analysis) with normalized message
    to group similar errors regardless of which logger produced them.
    """
    norm = normalize_log_message(raw_message)
    source = f"{error_type}|{norm or raw_message}"
    return hashlib.sha1(source.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def load_processed_fingerprints(team_id: str | None = None) -> Set[str]:
    try:
        path = _cache_path(team_id)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_processed_fingerprints(fps: Iterable[str], team_id: str | None = None) -> None:
    path = _cache_path(team_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(set(fps))), f, ensure_ascii=False, indent=2)


# --- Severity/Priority helper ---
def priority_name_from_severity(sev: str | None) -> str:
    """Map internal severity (low|medium|high) to Jira priority name.

    Defaults to Low when missing/unknown.
    """
    s = (sev or "low").strip().lower()
    if s == "high":
        return "High"
    if s == "medium":
        return "Medium"
    return "Low"


# --- Comment cool-down helpers ---
import datetime as _dt


def _load_comment_cache(team_id: str | None = None) -> dict:
    try:
        path = _comment_cache_path(team_id)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_comment_cache(data: dict, team_id: str | None = None) -> None:
    path = _comment_cache_path(team_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def should_comment(
    issue_key: str, cooldown_minutes: int = 120, team_id: str | None = None
) -> bool:
    """Return True if we should post a duplicate comment now (based on per-issue cool-down)."""
    if cooldown_minutes <= 0:
        return True
    cache = _load_comment_cache(team_id)
    last = cache.get(issue_key)
    if not last:
        return True
    try:
        last_dt = _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return True
    delta = _dt.datetime.utcnow() - last_dt.replace(tzinfo=None)
    return (delta.total_seconds() / 60.0) >= cooldown_minutes


def update_comment_timestamp(issue_key: str, team_id: str | None = None) -> None:
    cache = _load_comment_cache(team_id)
    cache[issue_key] = _dt.datetime.utcnow().isoformat() + "Z"
    _save_comment_cache(cache, team_id)
