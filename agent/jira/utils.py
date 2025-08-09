

"""Utilities for normalization and local fingerprint cache."""
from __future__ import annotations
import json
import pathlib
import re
from typing import Iterable, Set

_CACHE_PATH = pathlib.Path(".agent_cache/processed_logs.json")
_COMMENT_CACHE_PATH = pathlib.Path(".agent_cache/jira_comments.json")

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
    t = re.sub(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", " <email> ", t)
    t = re.sub(r"\bhttps?://[^\s]+", " <url> ", t)
    t = re.sub(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", " <token> ", t)
    t = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", " ", t)
    t = re.sub(r"\b[0-9a-f]{24}\b", " ", t)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}[tT ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b", " ", t)
    t = re.sub(r"\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?\]?", " ", t)
    t = re.sub(r"\b\d{5,}\b", " ", t)
    t = re.sub(r"\b[a-f0-9]{10,}\b", " ", t)
    t = _RE_PUNCT.sub(" ", t)
    t = _RE_WS.sub(" ", t).strip()
    return t


def load_processed_fingerprints() -> Set[str]:
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_processed_fingerprints(fps: Iterable[str]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(set(fps))), f, ensure_ascii=False, indent=2)


# --- Comment cool-down helpers ---
import datetime as _dt

def _load_comment_cache() -> dict:
    try:
        with open(_COMMENT_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_comment_cache(data: dict) -> None:
    _COMMENT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_COMMENT_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def should_comment(issue_key: str, cooldown_minutes: int = 120) -> bool:
    """Return True if we should post a duplicate comment now (based on per-issue cool-down)."""
    if cooldown_minutes <= 0:
        return True
    cache = _load_comment_cache()
    last = cache.get(issue_key)
    if not last:
        return True
    try:
        last_dt = _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return True
    delta = _dt.datetime.utcnow() - last_dt.replace(tzinfo=None)
    return (delta.total_seconds() / 60.0) >= cooldown_minutes


def update_comment_timestamp(issue_key: str) -> None:
    cache = _load_comment_cache()
    cache[issue_key] = _dt.datetime.utcnow().isoformat() + "Z"
    _save_comment_cache(cache)