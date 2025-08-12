from __future__ import annotations

import os
from typing import Optional, Dict, Any

import requests
from dotenv import load_dotenv

load_dotenv()

def _headers() -> Dict[str, str]:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN in environment")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def find_existing_pr(owner: str, repo: str, head: str) -> Optional[Dict[str, Any]]:
    """Find PRs with given head branch (owner:branch). Return first if exists."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    params = {"state": "open", "head": f"{owner}:{head}"}
    resp = requests.get(url, headers=_headers(), params=params, timeout=20)
    resp.raise_for_status()
    items = resp.json() or []
    return items[0] if items else None


def create_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool = True,
) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
        "draft": draft,
    }
    resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


