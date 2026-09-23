from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, TypedDict

from .utils.audit import append_audit
from .utils.git_tools import RepoConfig, clone_repo, git_create_branch, git_commit_push
from .utils.gh_api import create_pull_request, find_existing_pr, add_labels
from .utils.llm_fix import LLMFixResult, attempt_llm_fix, worktree_diff
from agent.jira.client import (
    add_comment as jira_add_comment,
    is_configured as jira_is_configured,
)


class PatchyState(TypedDict, total=False):
    # Input
    service: str
    error_type: str
    loghash: str
    jira: str
    draft: bool
    stacktrace: str
    logger: str
    hint: str

    # Generated
    repo_dir: str
    repo_owner: str
    repo_name: str
    default_branch: str
    branch: str
    pr_url: str
    message: str
    fault_file: str
    fault_line: int
    has_valid_fault_line: bool  # True if fault_line came from stacktrace
    allowed_paths: list
    lint_cmd: str
    test_cmd: str
    test_single_cmd: str
    llm_fix: dict  # Summary of a verified LLM fix, if any


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
    base = (
        hint.strip()
        if hint
        else (error_type.replace("-", " ") if error_type else "auto fix")
    )
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
        test_single_cmd=cfg_raw.get("test_single_cmd"),
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
    append_audit(
        {"service": service, "status": "repo_cloned", "repo": f"{cfg.owner}/{cfg.name}"}
    )
    return {
        **state,
        "repo_dir": str(repo_dir),
        "repo_owner": cfg.owner,
        "repo_name": cfg.name,
        "default_branch": cfg.default_branch,
        "allowed_paths": cfg.allowed_paths,
        "lint_cmd": cfg.lint_cmd,
        "test_cmd": cfg.test_cmd,
        "test_single_cmd": cfg.test_single_cmd,
    }


def _logger_to_filepath(
    logger_name: str, repo_dir: Path
) -> tuple[str | None, int | None]:
    """Convert a Java/Kotlin logger name to a file path.

    Examples:
        com.example.myservice.controller.license.LicensePurchaseController
        -> src/main/java/com/example/myservice/controller/license/LicensePurchaseController.java
    """
    if not logger_name:
        return None, None

    # Clean up logger name (remove line numbers, method names)
    import re

    # Remove trailing method/line info like .methodName or :123
    clean = re.sub(r"[:\.][\w]+\(\)$", "", logger_name)
    clean = re.sub(r":\d+$", "", clean)
    clean = clean.strip()

    # Convert dots to path separators
    path_part = clean.replace(".", "/")

    # Try common source directories and extensions
    source_dirs = [
        "src/main/java",
        "src/main/kotlin",
        "src/main/scala",
        "src",
        "app/src/main/java",
        "app/src/main/kotlin",
    ]
    extensions = [".java", ".kt", ".scala", ".groovy"]

    for src_dir in source_dirs:
        for ext in extensions:
            candidate = repo_dir / src_dir / f"{path_part}{ext}"
            if candidate.exists():
                rel_path = str(candidate.relative_to(repo_dir))
                return rel_path, 1

    # Try to find by class name only (last part)
    class_name = clean.split(".")[-1]
    for ext in extensions:
        try:
            import subprocess

            # Use find to locate the file
            proc = subprocess.run(
                ["find", ".", "-name", f"{class_name}{ext}", "-type", "f"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                # Take the first match
                found = proc.stdout.strip().split("\n")[0]
                if found.startswith("./"):
                    found = found[2:]
                return found, 1
        except Exception:
            pass

    return None, None


def _search_with_ripgrep(hint: str, repo_dir: Path) -> tuple[str | None, int | None]:
    """Search for a hint using ripgrep and return file path and line number."""
    if not hint:
        return None, None

    try:
        import subprocess, json as _json

        # Try exact match first
        proc = subprocess.run(
            ["rg", "-n", "-F", hint, "--json", "."],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=30,
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
                    # Skip test files and non-source files
                    if "/test/" in path or "Test." in path or path.endswith(".md"):
                        continue
                    return path, lno

        # If no exact match, try searching for class/method name patterns
        if "." in hint:
            # Try last part (class name)
            class_name = hint.split(".")[-1]
            return _search_with_ripgrep(class_name, repo_dir)

    except Exception:
        pass

    return None, None


def locate_fault(state: Dict[str, Any]) -> Dict[str, Any]:
    """Locate the faulted file using multiple strategies."""
    import re

    st = (state.get("stacktrace") or "").strip()
    hint = (state.get("hint") or "").strip()
    logger = (state.get("logger") or "").strip()
    repo_dir = Path(state.get("repo_dir") or ".")
    fault_file: str | None = None
    fault_line: int | None = None

    # Strategy 1: Parse stacktrace for file:line patterns
    # More comprehensive patterns for various stacktrace formats
    patterns = [
        # Java stacktrace: at com.example.Class.method(File.java:123)
        r"at\s+[\w.$]+\(([\w]+\.java):(\d+)\)",
        # Java stacktrace: at com.example.Class.method(Class.java:123)
        r"\((\w+\.java):(\d+)\)",
        # Python traceback: File "/path/to/file.py", line 123
        r'File\s+"([^"]+\.py)",\s+line\s+(\d+)',
        # Node.js: at function (/path/to/file.js:123:45)
        r"at\s+\w+\s+\(([^:]+\.(?:js|ts|tsx)):(\d+):\d+\)",
        # Generic: file.ext:123
        r"\b([\w./\\-]+\.(?:java|py|ts|tsx|js|go|kt|scala)):(\d+)\b",
        # Kotlin: at Class.method(File.kt:123)
        r"\((\w+\.kt):(\d+)\)",
        # Go: file.go:123
        r"\b(\w+\.go):(\d+)\b",
    ]
    stacktrace_filename: str | None = None
    for pat in patterns:
        m = re.search(pat, st)
        if m:
            stacktrace_filename = m.group(1)
            fault_line = int(m.group(2))
            append_audit(
                {
                    "service": state.get("service"),
                    "status": "fault_line_from_stacktrace",
                    "file": stacktrace_filename,
                    "line": fault_line,
                    "pattern": pat,
                }
            )
            break

    # If stacktrace gave just a filename, find the full path
    if stacktrace_filename and "/" not in stacktrace_filename:
        try:
            import subprocess

            proc = subprocess.run(
                ["find", ".", "-name", stacktrace_filename, "-type", "f"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                found = proc.stdout.strip().split("\n")[0]
                if found.startswith("./"):
                    found = found[2:]
                fault_file = found
                append_audit(
                    {
                        "service": state.get("service"),
                        "status": "fault_file_found_by_search",
                        "filename": stacktrace_filename,
                        "full_path": fault_file,
                    }
                )
        except Exception:
            pass
    elif stacktrace_filename:
        fault_file = stacktrace_filename

    # Strategy 2: Convert logger name to file path (Java/Kotlin)
    if not fault_file and logger:
        fault_file, fault_line = _logger_to_filepath(logger, repo_dir)
        if fault_file:
            append_audit(
                {
                    "service": state.get("service"),
                    "status": "fault_located_by_logger",
                    "logger": logger,
                    "file": fault_file,
                }
            )

    # Strategy 3: Try to extract logger from hint if it looks like a fully qualified class name
    if not fault_file and hint and "." in hint and hint[0].islower():
        # Looks like a package name (e.g., com.example.myservice.controller.LicensePurchaseController)
        fault_file, fault_line = _logger_to_filepath(hint, repo_dir)
        if fault_file:
            append_audit(
                {
                    "service": state.get("service"),
                    "status": "fault_located_by_hint_as_logger",
                    "hint": hint,
                    "file": fault_file,
                }
            )

    # Strategy 4: Search with ripgrep
    if not fault_file and hint:
        fault_file, fault_line = _search_with_ripgrep(hint, repo_dir)
        if fault_file:
            append_audit(
                {
                    "service": state.get("service"),
                    "status": "fault_located_by_ripgrep",
                    "hint": hint,
                    "file": fault_file,
                    "line": fault_line,
                }
            )

    # Strategy 5: Try logger name with ripgrep (search for class name)
    if not fault_file and logger:
        class_name = logger.split(".")[-1]
        fault_file, fault_line = _search_with_ripgrep(f"class {class_name}", repo_dir)
        if fault_file:
            append_audit(
                {
                    "service": state.get("service"),
                    "status": "fault_located_by_class_search",
                    "class": class_name,
                    "file": fault_file,
                }
            )

    if fault_file:
        # Track if we have a real fault_line from stacktrace or just a fallback
        has_valid_fault_line = fault_line is not None and fault_line > 0
        append_audit(
            {
                "service": state.get("service"),
                "status": "fault_located",
                "file": fault_file,
                "line": fault_line,
                "has_valid_fault_line": has_valid_fault_line,
            }
        )
        return {
            **state,
            "fault_file": fault_file,
            "fault_line": fault_line if has_valid_fault_line else 0,  # 0 = unknown
            "has_valid_fault_line": has_valid_fault_line,
        }

    append_audit(
        {
            "service": state.get("service"),
            "status": "fault_not_found",
            "tried": {
                "stacktrace": bool(st),
                "logger": bool(logger),
                "hint": bool(hint),
            },
        }
    )
    return state


def _llm_fix_pr_section(llm_fix: Dict[str, Any], suite_ran: bool) -> list[str]:
    lines = [
        "2. **Diagnosis**",
        f"   - {llm_fix.get('diagnosis') or 'n/a'}",
        "",
        "3. **Fix**",
        f"   - {llm_fix.get('summary') or 'n/a'}",
        f"   - Files: {', '.join(f'`{f}`' for f in llm_fix.get('files', []))}",
        f"   - LLM confidence: `{llm_fix.get('confidence')}`"
        f" (verified on attempt {llm_fix.get('attempts')})",
        "",
        "4. **Verification**",
        f"   - Added reproducing test `{llm_fix.get('test_path')}`",
        "   - ❌ Test fails on the original code with the reported error",
        "   - ✅ Test passes with the fix applied",
    ]
    if suite_ran:
        lines.append("   - ✅ Full test suite passes")
    else:
        lines.append(
            "   - ⚠️ No full test suite configured (`test_cmd`); only the new test ran"
        )
    lines += [
        "",
        "> Generated by Patchy (🩹🤖) with an LLM. Review the diagnosis and the test carefully.",
    ]
    return lines


def _no_fix(
    state: Dict[str, Any],
    branch: str,
    message: str,
    result: LLMFixResult | None = None,
) -> Dict[str, Any]:
    """Stop without a PR; leave the diagnosis on the Jira ticket instead."""
    service = state.get("service")
    jira = (state.get("jira") or "").strip()
    append_audit(
        {"service": service, "status": "no_fix", "branch": branch, "message": message}
    )
    if jira and jira_is_configured():
        lines = [f"Patchy (🩹🤖) could not produce a verified fix: {message}"]
        if state.get("fault_file"):
            lines.append(f"Suspected file: {state['fault_file']}")
        for h in (result.history if result else [])[-3:]:
            entry = f"- Attempt {h.get('attempt')} failed at '{h.get('stage')}'"
            if h.get("diagnosis"):
                entry += f"; diagnosis: {h['diagnosis']}"
            lines.append(entry)
        try:
            jira_add_comment(jira, "\n".join(lines))
        except Exception:
            pass
    return {**state, "branch": branch, "message": message}


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
        append_audit(
            {
                "service": service,
                "status": "duplicate_pr",
                "branch": branch,
                "pr_url": url,
            }
        )
        return {
            **state,
            "branch": branch,
            "pr_url": url,
            "message": "PR already exists",
        }

    # Only a located fault file inside allowed_paths can be fixed
    fault_file = state.get("fault_file")
    allowed = state.get("allowed_paths") or []
    if not fault_file:
        return _no_fix(state, branch, "Fault file could not be located")
    if allowed and not any(str(fault_file).startswith(a) for a in allowed):
        return _no_fix(state, branch, f"Fault file {fault_file} outside allowed_paths")

    git_create_branch(repo_dir, branch)

    # Verified LLM fix: edit + reproducing test (red → green → suite)
    result = attempt_llm_fix(repo_dir, state)
    if not (result.success and result.proposal):
        append_audit(
            {
                "service": service,
                "status": "llm_fix_failed",
                "branch": branch,
                "attempts": result.attempts,
                "message": result.message,
            }
        )
        return _no_fix(state, branch, f"LLM fix failed: {result.message}", result)

    llm_fix: Dict[str, Any] = {
        "diagnosis": result.proposal.diagnosis,
        "summary": result.proposal.summary,
        "confidence": result.proposal.confidence,
        "test_path": result.proposal.test_path,
        "files": sorted({e.file for e in result.proposal.edits}),
        "attempts": result.attempts,
        "diff": worktree_diff(repo_dir),
    }
    append_audit(
        {
            "service": service,
            "status": "llm_fix_applied",
            "branch": branch,
            "attempts": result.attempts,
            "test_path": result.proposal.test_path,
            "confidence": result.proposal.confidence,
        }
    )

    title = _pr_title(hint, error_type)
    commit_msg = title
    # Optional lint (tests already passed during LLM fix verification)
    lint_cmd = (state.get("lint_cmd") or "").strip()
    if lint_cmd:
        try:
            import subprocess

            subprocess.run(
                lint_cmd, cwd=str(repo_dir), shell=True, check=True
            )  # nosec B602
        except Exception as e:
            append_audit(
                {
                    "service": service,
                    "status": "lint_failed",
                    "branch": branch,
                    "message": str(e),
                }
            )
            return {**state, "branch": branch, "message": f"Lint failed: {e}"}
    try:
        git_commit_push(repo_dir, commit_msg)
    except Exception as e:
        append_audit(
            {
                "service": service,
                "status": "commit_failed",
                "branch": branch,
                "message": str(e),
            }
        )
        return {**state, "branch": branch, "message": f"Commit failed: {e}"}

    # Build descriptive PR body template
    pr_lines = []
    pr_lines.append("1. **Change overview**")
    pr_lines.append(f"   - Service: `{service}`")
    pr_lines.append(f"   - Error type: `{error_type}`")
    if hint:
        pr_lines.append(f"   - Hint: `{hint}`")
    if state.get("fault_file"):
        pr_lines.append(f"   - Target file: `{state.get('fault_file')}`")
    pr_lines.append("")
    pr_lines.extend(_llm_fix_pr_section(llm_fix, bool(state.get("test_cmd"))))
    pr_lines.append("")
    if jira:
        from agent.jira.client import get_jira_domain

        jira_domain = get_jira_domain()
        pr_lines.append(f"Refs: [#{jira}](https://{jira_domain}/browse/{jira})")
    pr_text = "\n".join(pr_lines)

    try:
        pr = create_pull_request(
            owner, repo, head=branch, base=base, title=title, body=pr_text, draft=draft
        )
        url = pr.get("html_url")
        number = int(pr.get("number", 0) or 0)
        # Label PR
        try:
            add_labels(
                owner,
                repo,
                number,
                ["auto-fix", "patchy", "llm-fix", "verified-by-test"],
            )
        except Exception:
            pass
        # Comment on Jira if configured and key provided
        if jira and jira_is_configured():
            try:
                jira_add_comment(jira, f"Refs: ({jira}) {url}")
            except Exception:
                pass
        append_audit(
            {
                "service": service,
                "status": "draft_opened" if draft else "opened",
                "branch": branch,
                "pr_url": url,
            }
        )
        # Bump in-process cap counter
        os.environ["_PATCHY_CREATED_SO_FAR"] = str(
            int(os.getenv("_PATCHY_CREATED_SO_FAR", "0")) + 1
        )
        return {**state, "branch": branch, "pr_url": url, "llm_fix": llm_fix}
    except Exception as e:
        append_audit(
            {
                "service": service,
                "status": "pr_failed",
                "branch": branch,
                "message": str(e),
            }
        )
        return {**state, "branch": branch, "message": f"PR failed: {e}"}


def finish(state: Dict[str, Any]) -> Dict[str, Any]:
    append_audit(
        {
            "service": state.get("service"),
            "branch": state.get("branch"),
            "pr_url": state.get("pr_url"),
            "status": "done",
        }
    )
    return state
