from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, TypedDict

from .utils.audit import append_audit
from .utils.git_tools import RepoConfig, clone_repo, git_create_branch, git_commit_push
from .utils.gh_api import create_pull_request, find_existing_pr, add_labels
from agent.jira.client import add_comment as jira_add_comment, is_configured as jira_is_configured


class PatchyState(TypedDict, total=False):
    service: str
    error_type: str
    loghash: str
    jira: str
    draft: bool

    repo_dir: str
    repo_owner: str
    repo_name: str
    default_branch: str
    branch: str
    pr_url: str
    message: str


def _allowed(service: str) -> bool:
    allow_csv = os.getenv("REPAIR_ALLOWED_SERVICES", "").strip()
    if not allow_csv:
        return True
    allow = {s.strip() for s in allow_csv.split(",") if s.strip()}
    return service in allow


def resolve_repo(state: Dict[str, Any]) -> Dict[str, Any]:
    service = state["service"]
    if not _allowed(service):
        msg = f"Service '{service}' not allowed (REPAIR_ALLOWED_SERVICES)."
        append_audit({"service": service, "status": "blocked", "message": msg})
        return {**state, "message": msg}

    repos_path = Path(__file__).resolve().parent / "repos.json"
    with repos_path.open("r", encoding="utf-8") as f:
        repos = json.load(f)
    cfg_raw = repos.get(service)
    if not cfg_raw:
        msg = f"Service '{service}' not found in repos.json"
        append_audit({"service": service, "status": "error", "message": msg})
        return {**state, "message": msg}

    cfg = RepoConfig(
        owner=cfg_raw["owner"],
        name=cfg_raw["name"],
        default_branch=cfg_raw.get("default_branch", "main"),
    )

    # Cap check per run (simple counter in env var memory)
    try:
        cap = int(os.getenv("REPAIR_MAX_PRS_PER_RUN", "1") or "1")
    except Exception:
        cap = 1
    created_count = int(os.getenv("_PATCHY_CREATED_SO_FAR", "0") or "0")
    if cap > 0 and created_count >= cap:
        msg = f"Cap reached: REPAIR_MAX_PRS_PER_RUN={cap}"
        append_audit({"service": service, "status": "cap-reached", "message": msg})
        return {**state, "message": msg}

    repo_dir = clone_repo(service, cfg)
    append_audit({"service": service, "status": "repo_cloned", "repo": f"{cfg.owner}/{cfg.name}"})
    return {
        **state,
        "repo_dir": str(repo_dir),
        "repo_owner": cfg.owner,
        "repo_name": cfg.name,
        "default_branch": cfg.default_branch,
    }


def locate_fault(state: Dict[str, Any]) -> Dict[str, Any]:
    # Placeholder: in future, collect and narrow to a specific file/line
    append_audit({
        "service": state.get("service"),
        "status": "locate_fault_placeholder",
        "loghash": state.get("loghash"),
        "error_type": state.get("error_type"),
    })
    return state


def create_pr(state: Dict[str, Any]) -> Dict[str, Any]:
    service = state.get("service")
    loghash = (state.get("loghash") or "").strip()
    error_type = (state.get("error_type") or "unknown").strip()
    jira = (state.get("jira") or "").strip()
    draft = bool(state.get("draft", True))

    repo_dir = Path(state["repo_dir"])  # must exist
    owner = state["repo_owner"]
    repo = state["repo_name"]
    base = state.get("default_branch", "main")

    short = loghash[:8] if loghash else "nohash"
    branch = f"fix/{service}/{short}"

    # Idempotency: if PR exists for same branch, skip creation
    existing = find_existing_pr(owner, repo, branch)
    if existing:
        url = existing.get("html_url")
        append_audit({
            "service": service,
            "status": "duplicate_pr",
            "branch": branch,
            "pr_url": url,
        })
        return {**state, "branch": branch, "pr_url": url, "message": "PR already exists"}

    # Create branch and minimal change
    git_create_branch(repo_dir, branch)

    touch_path = repo_dir / "PATCHY_TOUCH.md"
    body_lines = [
        "# Patchy touch file",
        f"Service: {service}",
        f"Error-Type: {error_type}",
        f"Loghash: {loghash}",
    ]
    if jira:
        body_lines.append(f"Jira: {jira}")
    touch_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")

    commit_msg = f"chore(patchy): touch for {service} [{short}]"
    try:
        git_commit_push(repo_dir, commit_msg)
    except Exception as e:
        append_audit({"service": service, "status": "commit_failed", "branch": branch, "message": str(e)})
        return {**state, "branch": branch, "message": f"Commit failed: {e}"}

    title = f"fix({service}): auto-fix for {error_type} [{loghash}]"
    pr_body = [
        f"Automated draft PR by Patchy (🩹🤖)",
        f"- Service: `{service}`",
        f"- Error-Type: `{error_type}`",
        f"- Loghash: `loghash-{loghash}`",
    ]
    if jira:
        pr_body.append(f"- Jira: {jira}")
    pr_text = "\n".join(pr_body)

    try:
        pr = create_pull_request(owner, repo, head=branch, base=base, title=title, body=pr_text, draft=draft)
        url = pr.get("html_url")
        number = int(pr.get("number", 0) or 0)
        # Label PR
        try:
            add_labels(owner, repo, number, ["auto-fix", "patchy", "low-risk"])
        except Exception:
            pass
        # Comment on Jira if configured and key provided
        if jira and jira_is_configured():
            try:
                jira_add_comment(jira, f"Refs: ({jira}) {url}")
            except Exception:
                pass
        append_audit({
            "service": service,
            "status": "draft_opened" if draft else "opened",
            "branch": branch,
            "pr_url": url,
        })
        # Bump in-process cap counter
        os.environ["_PATCHY_CREATED_SO_FAR"] = str(int(os.getenv("_PATCHY_CREATED_SO_FAR", "0")) + 1)
        return {**state, "branch": branch, "pr_url": url}
    except Exception as e:
        append_audit({"service": service, "status": "pr_failed", "branch": branch, "message": str(e)})
        return {**state, "branch": branch, "message": f"PR failed: {e}"}


def finish(state: Dict[str, Any]) -> Dict[str, Any]:
    append_audit({
        "service": state.get("service"),
        "branch": state.get("branch"),
        "pr_url": state.get("pr_url"),
        "status": "done",
    })
    return state


