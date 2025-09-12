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
    fault_file: str
    fault_line: int


def _allowed(service: str) -> bool:
    allow_csv = os.getenv("REPAIR_ALLOWED_SERVICES", "").strip()
    if not allow_csv:
        return True
    allow = {s.strip() for s in allow_csv.split(",") if s.strip()}
    return service in allow


def _to_camel(text: str) -> str:
    import re
    tokens = re.split(r"[^A-Za-z0-9]+", text or "")
    tokens = [t for t in tokens if t]
    if not tokens:
        return "change"
    first = tokens[0].lower()
    rest = [t.capitalize() for t in tokens[1:]]
    return first + "".join(rest)


def _pr_title(hint: str, error_type: str) -> str:
    base = hint.strip() if hint else (error_type.replace("-", " ") if error_type else "auto fix")
    base = base.strip().rstrip(".")
    if not base:
        base = "auto fix"
    # Ensure lowercase start after 'fix: '
    return f"fix: {base}"


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
        allowed_paths=cfg_raw.get("allowed_paths"),
        lint_cmd=cfg_raw.get("lint_cmd"),
        test_cmd=cfg_raw.get("test_cmd"),
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
        "allowed_paths": cfg.allowed_paths,
        "lint_cmd": cfg.lint_cmd,
        "test_cmd": cfg.test_cmd,
    }


def locate_fault(state: Dict[str, Any]) -> Dict[str, Any]:
    # V1: try to parse stacktrace `... at path/File.java:123` or `File.py:45`
    st = (state.get("stacktrace") or "").strip()
    hint = (state.get("hint") or "").strip()
    repo_dir = Path(state.get("repo_dir") or ".")
    fault_file: str | None = None
    fault_line: int | None = None

    import re
    patterns = [
        r"\b([\w./\\-]+\.(?:java|py|ts|tsx|js|go|kt|scala)):(\d+)\b",
        r"\bat\s+([\w./\\-]+\.(?:java|py|ts|tsx|js|go|kt|scala)):(\d+)\b",
    ]
    for pat in patterns:
        m = re.search(pat, st)
        if m:
            fault_file, fault_line = m.group(1), int(m.group(2))
            break

    # If stacktrace not provided or no match, try a simple ripgrep for hint
    if not fault_file and hint:
        try:
            import subprocess, json as _json
            proc = subprocess.run(
                ["rg", "-n", "-F", hint, "--json", "."], cwd=str(repo_dir), capture_output=True, text=True, check=True
            )
            for line in proc.stdout.splitlines():
                try:
                    obj = _json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "match":
                    path = obj.get("data", {}).get("path", {}).get("text")
                    lno = obj.get("data", {}).get("line_number")
                    if path and isinstance(lno, int):
                        fault_file, fault_line = path, lno
                        break
        except Exception:
            pass

    if fault_file:
        append_audit({
            "service": state.get("service"),
            "status": "fault_located",
            "file": fault_file,
            "line": fault_line,
        })
        return {**state, "fault_file": fault_file, "fault_line": fault_line or 1}

    append_audit({
        "service": state.get("service"),
        "status": "fault_not_found",
    })
    return state


def create_pr(state: Dict[str, Any]) -> Dict[str, Any]:
    service = state.get("service")
    loghash = (state.get("loghash") or "").strip()
    error_type = (state.get("error_type") or "unknown").strip()
    jira = (state.get("jira") or "").strip()
    draft = bool(state.get("draft", True))
    hint = (state.get("hint") or "").strip()

    repo_dir = Path(state["repo_dir"])  # must exist
    owner = state["repo_owner"]
    repo = state["repo_name"]
    base = state.get("default_branch", "main")

    short = loghash[:8] if loghash else "nohash"
    brief = _to_camel(hint or error_type or short)
    if jira:
        branch = f"bugfix/{jira}-{brief}"
    else:
        branch = f"bugfix/{service}-{brief}"

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

    # Create branch and minimal change (restricted to allowed_paths if provided)
    git_create_branch(repo_dir, branch)

    # Decide target file: prefer fault_file if inside allowed_paths (or no restriction), else fallback
    fault_file = state.get("fault_file")
    allowed = state.get("allowed_paths") or []
    chosen_rel: str
    if fault_file and (not allowed or any(str(fault_file).startswith(a) for a in allowed)):
        chosen_rel = fault_file
    else:
        chosen_rel = (allowed or ["PATCHY_TOUCH.md"])[0]
    touch_path = repo_dir / chosen_rel
    touch_path.parent.mkdir(parents=True, exist_ok=True)
    mode = (state.get("mode") or "note").strip().lower()
    if mode in ("touch", "note"):
        body_lines = [
            "# Patchy touch file",
            f"Service: {service}",
            f"Error-Type: {error_type}",
            f"Loghash: {loghash}",
            f"Target: {chosen_rel}",
        ]
        if jira:
            body_lines.append(f"Jira: {jira}")
        if touch_path.exists() and mode == "note":
            with touch_path.open("a", encoding="utf-8") as f:
                f.write("\n\n# Patchy note\n")
                for ln in body_lines[1:]:
                    f.write(f"{ln}\n")
        else:
            touch_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    elif mode == "fix":
        # Minimal safe fix attempt (language-aware v0)
        suffix = touch_path.suffix.lower()
        try:
            if suffix == ".java":
                from .utils.fix_java import apply_java_npe_guard  # type: ignore
                changed, msg = apply_java_npe_guard(touch_path, int(state.get("fault_line") or 0))
                if not changed:
                    append_audit({"service": service, "status": "fix_skipped", "branch": branch, "message": msg})
            elif suffix in (".py",):
                # v0: prepend guidance comment
                content = touch_path.read_text(encoding="utf-8") if touch_path.exists() else ""
                new_content = "# Patchy: add None checks/guard clauses as needed\n" + content
                touch_path.write_text(new_content, encoding="utf-8")
            elif suffix in (".ts", ".tsx", ".js"):
                content = touch_path.read_text(encoding="utf-8") if touch_path.exists() else ""
                new_content = "// Patchy: add optional chaining/guard clauses as needed\n" + content
                touch_path.write_text(new_content, encoding="utf-8")
            else:
                # Fallback: append a note
                with touch_path.open("a", encoding="utf-8") as f:
                    f.write("\n# Patchy: low-risk placeholder change\n")
        except Exception as e:
            append_audit({"service": service, "status": "fix_apply_failed", "branch": branch, "message": str(e)})
            return {**state, "branch": branch, "message": f"Fix apply failed: {e}"}

    title = _pr_title(hint, error_type)
    commit_msg = title
    # Optional lint/tests
    lint_cmd = (state.get("lint_cmd") or "").strip()
    test_cmd = (state.get("test_cmd") or "").strip()
    if lint_cmd:
        try:
            import subprocess
            subprocess.run(lint_cmd, cwd=str(repo_dir), shell=True, check=True)
        except Exception as e:
            append_audit({"service": service, "status": "lint_failed", "branch": branch, "message": str(e)})
            return {**state, "branch": branch, "message": f"Lint failed: {e}"}
    if test_cmd:
        try:
            import subprocess
            subprocess.run(test_cmd, cwd=str(repo_dir), shell=True, check=True)
        except Exception as e:
            append_audit({"service": service, "status": "tests_failed", "branch": branch, "message": str(e)})
            return {**state, "branch": branch, "message": f"Tests failed: {e}"}

    try:
        git_commit_push(repo_dir, commit_msg)
    except Exception as e:
        append_audit({"service": service, "status": "commit_failed", "branch": branch, "message": str(e)})
        return {**state, "branch": branch, "message": f"Commit failed: {e}"}

    # Build descriptive PR body template
    pr_lines = []
    pr_lines.append("This update introduces an automated fix to improve reliability and maintainability:")
    pr_lines.append("")
    pr_lines.append("1. **Change overview**")
    pr_lines.append(f"   - Service: `{service}`")
    pr_lines.append(f"   - Error type: `{error_type}`")
    if hint:
        pr_lines.append(f"   - Hint: `{hint}`")
    if state.get("fault_file"):
        pr_lines.append(f"   - Target file: `{state.get('fault_file')}`")
    pr_lines.append("")
    pr_lines.append("2. **Reasoning**")
    pr_lines.append("   - Small, low-risk change generated by Patchy (🩹🤖) to address the detected issue.")
    pr_lines.append("   - Guardrails: allow-list, per-run cap, duplicate branch check, and pre-push lint/tests.")
    pr_lines.append("")
    pr_lines.append("These improvements aim to keep the system stable and provide safer, incremental fixes.")
    pr_lines.append("")
    if jira:
        from agent.jira.client import JIRA_DOMAIN
        pr_lines.append(f"Refs: [#{jira}](https://{JIRA_DOMAIN}/browse/{jira})")
    pr_text = "\n".join(pr_lines)

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


