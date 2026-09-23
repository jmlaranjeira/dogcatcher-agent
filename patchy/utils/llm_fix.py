"""LLM-generated fixes verified by a reproducing test.

Flow per attempt:
  1. Ask the LLM for a minimal code edit plus a NEW test that reproduces the error.
  2. Validate the proposal (paths, size, safe identifiers).
  3. Write the test and run it against the unpatched code -> it MUST fail with
     the expected error (proves the test reproduces the bug).
  4. Apply the edit and re-run the test -> it MUST pass.
  5. Run the full test suite (if configured) -> it MUST pass.
  Any failure resets the working tree and feeds the output back to the LLM
  for the next attempt, up to PATCHY_LLM_MAX_ATTEMPTS.

A patch is only kept when every step succeeds, so an unverified change never
reaches a PR. If no single-test command can be resolved, the LLM is not called.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .audit import append_audit

MAX_CONTEXT_FILE_LINES = 600
MAX_EXISTING_TEST_LINES = 150
OUTPUT_TAIL_CHARS = 3000

_SAFE_PATH_RE = re.compile(r"^[\w./-]+$")
_SAFE_CLASS_RE = re.compile(r"^[A-Za-z_][\w.$]*$")
_COMPILE_ERROR_MARKERS = (
    "COMPILATION ERROR",
    "Compilation failure",
    "cannot find symbol",
    "error: compilation failed",
    "SyntaxError",
    "ImportError while importing test module",
)


@dataclass
class Edit:
    file: str
    search: str
    replace: str


@dataclass
class Proposal:
    diagnosis: str
    summary: str
    confidence: str
    edits: List[Edit]
    test_path: str
    test_content: str
    test_class: str
    expected_failure: str


@dataclass
class LLMFixResult:
    success: bool
    message: str
    attempts: int = 0
    proposal: Optional[Proposal] = None
    history: List[Dict[str, Any]] = field(default_factory=list)


class ProposalError(ValueError):
    """Proposal is malformed or violates a guardrail."""


# ── Configuration helpers ────────────────────────────────────────


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


def infer_test_single_cmd(repo_dir: Path) -> Optional[str]:
    """Best-effort default command to run a single test file/class."""
    if (repo_dir / "pom.xml").exists():
        mvn = "./mvnw" if (repo_dir / "mvnw").exists() else "mvn"
        return f"{mvn} -q -B test -Dtest={{test_class}} -Dsurefire.failIfNoSpecifiedTests=false"
    if (repo_dir / "build.gradle").exists() or (repo_dir / "build.gradle.kts").exists():
        gradle = "./gradlew" if (repo_dir / "gradlew").exists() else "gradle"
        return f"{gradle} test --tests {{test_class}}"
    if any(
        (repo_dir / f).exists()
        for f in ("pyproject.toml", "setup.py", "pytest.ini", "requirements.txt")
    ):
        return f"{shlex.quote(sys.executable)} -m pytest -q {{test_path}}"
    if (repo_dir / "package.json").exists():
        return "npx jest {test_path}"
    return None


# ── Context gathering ────────────────────────────────────────────


def _language(path: str) -> str:
    return {
        ".java": "java",
        ".kt": "kotlin",
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".go": "go",
    }.get(Path(path).suffix.lower(), "unknown")


def _numbered(lines: List[str], start: int) -> str:
    return "\n".join(f"{i:>5}| {ln}" for i, ln in enumerate(lines, start=start))


def _file_excerpt(path: Path, fault_line: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= MAX_CONTEXT_FILE_LINES:
        return _numbered(lines, 1)
    center = fault_line if fault_line > 0 else 1
    half = MAX_CONTEXT_FILE_LINES // 2
    lo = max(0, center - 1 - half)
    hi = min(len(lines), lo + MAX_CONTEXT_FILE_LINES)
    return _numbered(lines[lo:hi], lo + 1)


def find_existing_test(repo_dir: Path, fault_file: str) -> Optional[str]:
    """Locate an existing test for the faulted file to show the LLM house style."""
    p = Path(fault_file)
    stem, suffix = p.stem, p.suffix
    candidates: List[Path] = []
    parts = p.parts
    if "main" in parts:
        idx = parts.index("main")
        test_dir = Path(*parts[:idx], "test", *parts[idx + 1 : -1])
        candidates += [
            test_dir / f"{stem}Test{suffix}",
            test_dir / f"{stem}Tests{suffix}",
        ]
    candidates += [
        p.with_name(f"test_{stem}{suffix}"),
        Path("tests") / f"test_{stem}{suffix}",
        p.with_name(f"{stem}.test{suffix}"),
        p.with_name(f"{stem}.spec{suffix}"),
    ]
    for c in candidates:
        if (repo_dir / c).is_file():
            return str(c)
    # Fall back to any test file in the repo with the same language
    patterns = [
        f"*{stem}Test{suffix}",
        f"test_{stem}{suffix}",
        f"*Test{suffix}",
        f"test_*{suffix}",
    ]
    for pattern in patterns:
        for found in sorted(repo_dir.rglob(pattern)):
            if ".git" not in found.parts and found.is_file():
                return str(found.relative_to(repo_dir))
    return None


def build_context(
    repo_dir: Path, fault_file: str, fault_line: int, state: Dict[str, Any]
) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "service": state.get("service"),
        "error_type": state.get("error_type"),
        "stacktrace": (state.get("stacktrace") or "")[:6000],
        "hint": state.get("hint") or "",
        "logger": state.get("logger") or "",
        "fault_file": fault_file,
        "fault_line": fault_line,
        "language": _language(fault_file),
        "source": _file_excerpt(repo_dir / fault_file, fault_line),
        "existing_test_path": None,
        "existing_test": None,
    }
    existing = find_existing_test(repo_dir, fault_file)
    if existing:
        lines = (
            (repo_dir / existing)
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        )
        ctx["existing_test_path"] = existing
        ctx["existing_test"] = "\n".join(lines[:MAX_EXISTING_TEST_LINES])
    return ctx


# ── Prompting ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Patchy, a careful senior engineer fixing a production error.

You will receive the error details and the source of the faulting file. Produce:
1. A MINIMAL code edit that fixes the root cause (not just a comment or a TODO).
2. A NEW, self-contained test file that reproduces the error. It must FAIL on the
   current code with the reported error, and PASS once your edit is applied.

Rules:
- Only edit the faulting file unless another file is strictly required.
- Never edit existing tests. The test must be a NEW file that does not exist yet.
- Follow the style, framework and mocking library of the existing test if shown.
- Each edit is an exact search/replace: "search" must be copied VERBATIM from the
  source (without the line-number prefix) and must occur exactly once in the file.
  Include enough surrounding lines to make it unique.
- Do not change public signatures unless unavoidable. Do not reformat code.
- "expected_failure" is a short substring (e.g. the exception class name) that
  will appear in the test runner output when the test fails on the current code.
- "test_class" is the test's fully-qualified class name (Java/Kotlin) or its
  module name (other languages).
- If you cannot confidently fix it, return {"cannot_fix": true, "reason": "..."}.

Respond with JSON only:
{
  "diagnosis": "root cause in 1-3 sentences",
  "summary": "what the fix changes, for the PR description",
  "confidence": "high" | "medium" | "low",
  "edits": [{"file": "relative/path", "search": "exact text", "replace": "new text"}],
  "test": {
    "path": "relative/path/to/NewReproTest.ext",
    "class_name": "fully.qualified.NewReproTest",
    "content": "full file content",
    "expected_failure": "NullPointerException"
  }
}"""


def _user_prompt(ctx: Dict[str, Any], feedback: List[str]) -> str:
    parts = [
        f"Service: {ctx['service']}",
        f"Error type: {ctx['error_type']}",
        f"Language: {ctx['language']}",
    ]
    if ctx["logger"]:
        parts.append(f"Logger: {ctx['logger']}")
    if ctx["hint"]:
        parts.append(f"Hint: {ctx['hint']}")
    if ctx["stacktrace"]:
        parts.append(f"Stacktrace / log detail:\n{ctx['stacktrace']}")
    line_note = f" (fault at line {ctx['fault_line']})" if ctx["fault_line"] else ""
    parts.append(f"Faulting file: {ctx['fault_file']}{line_note}\n{ctx['source']}")
    if ctx["existing_test"]:
        parts.append(
            f"Existing test for style reference ({ctx['existing_test_path']}):\n"
            f"{ctx['existing_test']}"
        )
    if feedback:
        parts.append(
            "Previous attempts FAILED. Learn from this feedback:\n"
            + "\n---\n".join(feedback)
        )
    return "\n\n".join(parts)


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


def parse_proposal(raw: Dict[str, Any]) -> Proposal:
    if raw.get("cannot_fix"):
        raise ProposalError(f"LLM declined: {raw.get('reason', 'no reason given')}")
    test = raw.get("test") or {}
    edits_raw = raw.get("edits") or []
    if not edits_raw:
        raise ProposalError("proposal has no edits")
    edits = []
    for e in edits_raw:
        if not isinstance(e, dict) or not e.get("file") or not e.get("search"):
            raise ProposalError("each edit needs non-empty 'file' and 'search'")
        edits.append(
            Edit(
                file=str(e["file"]),
                search=str(e["search"]),
                replace=str(e.get("replace", "")),
            )
        )
    proposal = Proposal(
        diagnosis=str(raw.get("diagnosis", "")).strip(),
        summary=str(raw.get("summary", "")).strip(),
        confidence=str(raw.get("confidence", "low")).strip().lower(),
        edits=edits,
        test_path=str(test.get("path", "")).strip(),
        test_content=str(test.get("content", "")),
        test_class=str(test.get("class_name", "")).strip(),
        expected_failure=str(test.get("expected_failure", "")).strip(),
    )
    if not proposal.test_path or not proposal.test_content.strip():
        raise ProposalError("proposal has no reproducing test")
    if not proposal.expected_failure:
        raise ProposalError("proposal has no 'expected_failure'")
    return proposal


# ── Validation & application ─────────────────────────────────────


def _is_test_path(path: str) -> bool:
    p = Path(path)
    return (
        any(part.lower() in ("test", "tests", "__tests__") for part in p.parts[:-1])
        or p.name.startswith("test_")
        or re.search(r"(Test|Tests|_test)$", p.stem) is not None
        or re.search(r"\.(test|spec)\.\w+$", p.name) is not None
    )


def _inside_repo(repo_dir: Path, rel: str) -> bool:
    if not _SAFE_PATH_RE.match(rel) or rel.startswith("/") or ".." in Path(rel).parts:
        return False
    try:
        (repo_dir / rel).resolve().relative_to(repo_dir.resolve())
        return True
    except ValueError:
        return False


def validate_proposal(
    proposal: Proposal, repo_dir: Path, allowed_paths: Optional[List[str]]
) -> None:
    max_lines = _int_env("PATCHY_LLM_MAX_DIFF_LINES", 80)
    changed = 0
    for e in proposal.edits:
        if not _inside_repo(repo_dir, e.file):
            raise ProposalError(f"unsafe edit path: {e.file}")
        if allowed_paths and not any(e.file.startswith(a) for a in allowed_paths):
            raise ProposalError(f"edit outside allowed_paths: {e.file}")
        if _is_test_path(e.file):
            raise ProposalError(f"edits must not modify tests: {e.file}")
        target = repo_dir / e.file
        if not target.is_file():
            raise ProposalError(f"edit target does not exist: {e.file}")
        count = target.read_text(encoding="utf-8", errors="replace").count(e.search)
        if count != 1:
            raise ProposalError(
                f"search text must match exactly once in {e.file} (found {count})"
            )
        changed += max(len(e.search.splitlines()), len(e.replace.splitlines()))
    if changed > max_lines:
        raise ProposalError(f"edit too large: ~{changed} lines > {max_lines}")

    if not _inside_repo(repo_dir, proposal.test_path):
        raise ProposalError(f"unsafe test path: {proposal.test_path}")
    if not _is_test_path(proposal.test_path):
        raise ProposalError(
            f"test path is not in a test location: {proposal.test_path}"
        )
    if (repo_dir / proposal.test_path).exists():
        raise ProposalError(f"test file already exists: {proposal.test_path}")
    if proposal.test_class and not _SAFE_CLASS_RE.match(proposal.test_class):
        raise ProposalError(f"unsafe test class name: {proposal.test_class}")


def apply_edits(repo_dir: Path, edits: List[Edit]) -> None:
    for e in edits:
        target = repo_dir / e.file
        content = target.read_text(encoding="utf-8")
        target.write_text(content.replace(e.search, e.replace, 1), encoding="utf-8")


def reset_worktree(repo_dir: Path) -> None:
    """Discard tracked changes and new untracked files (keeps ignored build output)."""
    subprocess.run(["git", "reset", "--hard", "-q"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "clean", "-fdq"], cwd=str(repo_dir), check=True)


def worktree_diff(repo_dir: Path) -> str:
    subprocess.run(["git", "add", "-N", "."], cwd=str(repo_dir), check=True)
    proc = subprocess.run(
        ["git", "diff"], cwd=str(repo_dir), capture_output=True, text=True, check=True
    )
    return proc.stdout


# ── Test execution ───────────────────────────────────────────────


def _run(cmd: str, repo_dir: Path) -> tuple[int, str]:
    timeout = _int_env("PATCHY_TEST_TIMEOUT", 900)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            shell=True,  # nosec B602 - command comes from repos.json; LLM values are validated + quoted
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return 124, out + f"\n[timeout after {timeout}s]"


def _format_test_cmd(template: str, proposal: Proposal) -> str:
    test_class = proposal.test_class or Path(proposal.test_path).stem
    return template.format(
        test_path=shlex.quote(proposal.test_path),
        test_class=shlex.quote(test_class),
    )


def _tail(text: str) -> str:
    return text[-OUTPUT_TAIL_CHARS:]


# ── Orchestration ────────────────────────────────────────────────


def _default_llm(messages: List[Dict[str, str]]) -> str:
    from agent.llm_factory import chat_completion

    return chat_completion(
        messages,
        temperature=0.0,
        max_tokens=_int_env("PATCHY_LLM_MAX_TOKENS", 8192),
        json_response=True,
    )


def _verify(
    proposal: Proposal,
    repo_dir: Path,
    test_single_cmd: str,
    test_cmd: Optional[str],
) -> tuple[bool, str, str]:
    """Run the red/green/suite checks. Returns (ok, stage, feedback)."""
    single = _format_test_cmd(test_single_cmd, proposal)
    test_file = repo_dir / proposal.test_path
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(proposal.test_content, encoding="utf-8")

    # RED: the new test must fail on the current code, with the expected error.
    rc, out = _run(single, repo_dir)
    if rc == 0:
        return (
            False,
            "red",
            (
                "The reproducing test PASSED on the unpatched code, so it does not "
                f"reproduce the error.\nOutput:\n{_tail(out)}"
            ),
        )
    if any(m in out for m in _COMPILE_ERROR_MARKERS):
        return (
            False,
            "red",
            (
                "The reproducing test failed to COMPILE/IMPORT on the unpatched code; "
                f"it must compile and fail at runtime.\nOutput:\n{_tail(out)}"
            ),
        )
    if proposal.expected_failure not in out:
        return (
            False,
            "red",
            (
                f"The test failed, but not with the expected failure "
                f"'{proposal.expected_failure}'.\nOutput:\n{_tail(out)}"
            ),
        )

    # GREEN: after the fix, the new test must pass.
    apply_edits(repo_dir, proposal.edits)
    rc, out = _run(single, repo_dir)
    if rc != 0:
        return (
            False,
            "green",
            (
                f"After applying the edit, the reproducing test still FAILS.\nOutput:\n{_tail(out)}"
            ),
        )

    # SUITE: nothing else may break.
    if test_cmd:
        rc, out = _run(test_cmd, repo_dir)
        if rc != 0:
            return (
                False,
                "suite",
                (
                    "The fix passes the new test but BREAKS the existing test suite."
                    f"\nOutput:\n{_tail(out)}"
                ),
            )
    return True, "verified", ""


def attempt_llm_fix(
    repo_dir: Path,
    state: Dict[str, Any],
    llm: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> LLMFixResult:
    """Generate, verify and (if verified) leave an LLM fix + test in the worktree.

    On failure the worktree is reset to a clean state.
    """
    service = state.get("service")
    fault_file = state.get("fault_file")
    if not fault_file or not (repo_dir / fault_file).is_file():
        return LLMFixResult(False, "LLM fix needs a located fault file")

    test_single_cmd = (
        state.get("test_single_cmd") or ""
    ).strip() or infer_test_single_cmd(repo_dir)
    if not test_single_cmd:
        return LLMFixResult(
            False,
            "No test_single_cmd configured or inferable; refusing unverified LLM fix",
        )
    test_cmd = (state.get("test_cmd") or "").strip() or None
    allowed = state.get("allowed_paths") or None
    max_attempts = max(1, _int_env("PATCHY_LLM_MAX_ATTEMPTS", 3))
    llm = llm or _default_llm

    ctx = build_context(repo_dir, fault_file, int(state.get("fault_line") or 0), state)
    feedback: List[str] = []
    history: List[Dict[str, Any]] = []

    for attempt in range(1, max_attempts + 1):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(ctx, feedback)},
        ]
        proposal: Optional[Proposal] = None
        try:
            proposal = parse_proposal(_extract_json(llm(messages)))
            validate_proposal(proposal, repo_dir, allowed)
        except ProposalError as e:
            stage, msg = "proposal", str(e)
            if msg.startswith("LLM declined"):
                history.append({"attempt": attempt, "stage": stage, "message": msg})
                append_audit(
                    {"service": service, "status": "llm_fix_declined", "message": msg}
                )
                return LLMFixResult(False, msg, attempt, history=history)
            ok = False
        except (json.JSONDecodeError, TypeError) as e:
            stage, msg, ok = "proposal", f"response was not valid JSON: {e}", False
        except Exception as e:  # LLM/provider errors: no point retrying blindly
            msg = f"LLM call failed: {e}"
            append_audit(
                {"service": service, "status": "llm_fix_error", "message": msg}
            )
            return LLMFixResult(False, msg, attempt, history=history)
        else:
            try:
                ok, stage, msg = _verify(proposal, repo_dir, test_single_cmd, test_cmd)
            except Exception as e:
                ok, stage, msg = False, "verify", f"verification crashed: {e}"

        history.append(
            {
                "attempt": attempt,
                "stage": stage,
                "ok": ok,
                "message": msg[:500],
                "diagnosis": proposal.diagnosis if proposal else "",
            }
        )
        append_audit(
            {
                "service": service,
                "status": "llm_fix_verified" if ok else "llm_fix_attempt_failed",
                "attempt": attempt,
                "stage": stage,
                "message": msg[:500],
            }
        )
        if ok:
            return LLMFixResult(True, "verified", attempt, proposal, history)

        reset_worktree(repo_dir)
        feedback.append(f"Attempt {attempt} failed at stage '{stage}': {msg}")

    return LLMFixResult(
        False,
        f"LLM fix not verified after {max_attempts} attempt(s)",
        max_attempts,
        history=history,
    )
