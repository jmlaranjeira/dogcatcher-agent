"""Tests for Patchy's LLM-generated fix with reproducing test."""

import json
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from patchy.utils.llm_fix import (
    ProposalError,
    attempt_llm_fix,
    parse_proposal,
    validate_proposal,
)

PY = shlex.quote(sys.executable)

SOURCE = "def safe_ratio(a, b):\n    return a / b\n"

EXISTING_TEST = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
    "from calc import safe_ratio\n"
    "assert safe_ratio(4, 2) == 2\n"
)

REPRO_TEST = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
    "from calc import safe_ratio\n"
    "assert safe_ratio(1, 0) == 0\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    tmp_path = tmp_path / "repo"
    tmp_path.mkdir()
    (tmp_path / "calc.py").write_text(SOURCE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(EXISTING_TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return tmp_path


def _state(**overrides):
    state = {
        "service": "calc",
        "error_type": "zero-division",
        "stacktrace": 'File "calc.py", line 2, in safe_ratio\nZeroDivisionError: division by zero',
        "fault_file": "calc.py",
        "fault_line": 2,
        "test_single_cmd": f"{PY} {{test_path}}",
        "test_cmd": f"{PY} tests/test_calc.py",
    }
    state.update(overrides)
    return state


def _proposal(replace="    return a / b if b else 0\n", test=REPRO_TEST, **extra):
    raw = {
        "diagnosis": "Division by zero when b == 0.",
        "summary": "Return 0 when the divisor is zero.",
        "confidence": "high",
        "edits": [
            {"file": "calc.py", "search": "    return a / b\n", "replace": replace}
        ],
        "test": {
            "path": "tests/test_patchy_repro_ratio.py",
            "class_name": "test_patchy_repro_ratio",
            "content": test,
            "expected_failure": "ZeroDivisionError",
        },
    }
    raw.update(extra)
    return json.dumps(raw)


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _no_audit(tmp_path, monkeypatch):
    monkeypatch.setattr("patchy.utils.audit.AUDIT_PATH", tmp_path / "audit.jsonl")


@pytest.mark.unit
class TestAttemptLlmFix:
    def test_verified_fix_is_left_in_worktree(self, repo):
        llm = FakeLLM(_proposal())
        result = attempt_llm_fix(repo, _state(), llm=llm)

        assert result.success, result.history
        assert result.attempts == 1
        assert "if b else 0" in (repo / "calc.py").read_text()
        assert (repo / "tests" / "test_patchy_repro_ratio.py").exists()

    def test_non_reproducing_test_triggers_retry_with_feedback(self, repo):
        passing_test = REPRO_TEST.replace(
            "safe_ratio(1, 0) == 0", "safe_ratio(2, 1) == 2"
        )
        llm = FakeLLM(_proposal(test=passing_test), _proposal())

        result = attempt_llm_fix(repo, _state(), llm=llm)

        assert result.success
        assert result.attempts == 2
        assert result.history[0]["stage"] == "red"
        assert "does not reproduce" in llm.calls[1][1]["content"]

    def test_fix_that_does_not_work_fails_green_stage(self, repo, monkeypatch):
        monkeypatch.setenv("PATCHY_LLM_MAX_ATTEMPTS", "1")
        llm = FakeLLM(_proposal(replace="    return a / b  # no-op\n"))

        result = attempt_llm_fix(repo, _state(), llm=llm)

        assert not result.success
        assert result.history[0]["stage"] == "green"

    def test_fix_breaking_suite_is_rejected_and_worktree_reset(self, repo, monkeypatch):
        monkeypatch.setenv("PATCHY_LLM_MAX_ATTEMPTS", "1")
        llm = FakeLLM(_proposal(replace="    return 0\n"))

        result = attempt_llm_fix(repo, _state(), llm=llm)

        assert not result.success
        assert result.history[0]["stage"] == "suite"
        assert _git(repo, "status", "--porcelain") == ""

    def test_wrong_failure_reason_is_rejected(self, repo, monkeypatch):
        monkeypatch.setenv("PATCHY_LLM_MAX_ATTEMPTS", "1")
        wrong = REPRO_TEST + "raise RuntimeError('unrelated')\n"
        wrong = wrong.replace("safe_ratio(1, 0) == 0", "True")
        llm = FakeLLM(_proposal(test=wrong))

        result = attempt_llm_fix(repo, _state(), llm=llm)

        assert not result.success
        assert "expected failure" in result.history[0]["message"]

    def test_declined_stops_immediately(self, repo):
        llm = FakeLLM(
            json.dumps({"cannot_fix": True, "reason": "needs business input"})
        )

        result = attempt_llm_fix(repo, _state(), llm=llm)

        assert not result.success
        assert "needs business input" in result.message
        assert len(llm.calls) == 1

    def test_invalid_json_is_retried(self, repo):
        llm = FakeLLM("not json at all", "```json\n" + _proposal() + "\n```")

        result = attempt_llm_fix(repo, _state(), llm=llm)

        assert result.success
        assert result.attempts == 2

    def test_refuses_without_test_command(self, repo):
        llm = FakeLLM()
        result = attempt_llm_fix(repo, _state(test_single_cmd=""), llm=llm)

        assert not result.success
        assert "test_single_cmd" in result.message
        assert llm.calls == []

    def test_requires_fault_file(self, repo):
        result = attempt_llm_fix(repo, _state(fault_file=None), llm=FakeLLM())
        assert not result.success


@pytest.mark.unit
class TestProposalValidation:
    def _validate(self, repo, allowed=None, **overrides):
        raw = json.loads(_proposal())
        for key, value in overrides.items():
            if key in ("path", "class_name"):
                raw["test"][key] = value
            else:
                raw["edits"][0][key] = value
        validate_proposal(parse_proposal(raw), repo, allowed)

    def test_valid_proposal_passes(self, repo):
        self._validate(repo)

    @pytest.mark.parametrize(
        "overrides, match",
        [
            ({"file": "../etc/passwd"}, "unsafe edit path"),
            ({"file": "tests/test_calc.py"}, "must not modify tests"),
            ({"search": "not in file"}, "exactly once"),
            ({"path": "tests/test_calc.py"}, "already exists"),
            ({"path": "calc_repro.py"}, "not in a test location"),
            ({"class_name": "x; rm -rf /"}, "unsafe test class"),
        ],
    )
    def test_guardrails(self, repo, overrides, match):
        with pytest.raises(ProposalError, match=match):
            self._validate(repo, **overrides)

    def test_allowed_paths_enforced(self, repo):
        with pytest.raises(ProposalError, match="allowed_paths"):
            self._validate(repo, allowed=["src/"])

    def test_oversized_edit_rejected(self, repo, monkeypatch):
        monkeypatch.setenv("PATCHY_LLM_MAX_DIFF_LINES", "1")
        with pytest.raises(ProposalError, match="too large"):
            self._validate(repo, replace="a\nb\nc\n")

    def test_missing_expected_failure_rejected(self):
        raw = json.loads(_proposal())
        raw["test"]["expected_failure"] = ""
        with pytest.raises(ProposalError, match="expected_failure"):
            parse_proposal(raw)


def _pr_state(repo, **overrides):
    return {
        **_state(),
        "repo_dir": str(repo),
        "repo_owner": "o",
        "repo_name": "r",
        "jira": "ABC-1",
        **overrides,
    }


@pytest.mark.unit
class TestCreatePr:
    @pytest.fixture
    def gh(self):
        from patchy import patchy_nodes

        with (
            patch.object(patchy_nodes, "find_existing_pr", return_value=None),
            patch.object(patchy_nodes, "git_commit_push") as push,
            patch.object(
                patchy_nodes,
                "create_pull_request",
                return_value={"html_url": "https://gh/pr/1", "number": 1},
            ) as create,
            patch.object(patchy_nodes, "add_labels") as labels,
            patch.object(patchy_nodes, "jira_is_configured", return_value=True),
            patch.object(patchy_nodes, "jira_add_comment") as comment,
            patch("agent.jira.client.get_jira_domain", return_value="jira.test"),
        ):
            yield {"push": push, "create": create, "labels": labels, "comment": comment}

    @staticmethod
    def _run(state, llm):
        from patchy import patchy_nodes

        real_attempt = patchy_nodes.attempt_llm_fix
        with patch.object(
            patchy_nodes,
            "attempt_llm_fix",
            side_effect=lambda d, s: real_attempt(d, s, llm=llm),
        ):
            return patchy_nodes.create_pr(state)

    def test_verified_fix_opens_pr(self, repo, gh):
        result = self._run(_pr_state(repo), FakeLLM(_proposal()))

        assert result["pr_url"] == "https://gh/pr/1"
        assert result["llm_fix"]["test_path"] == "tests/test_patchy_repro_ratio.py"
        assert "if b else 0" in result["llm_fix"]["diff"]
        gh["push"].assert_called_once()
        body = gh["create"].call_args.kwargs["body"]
        assert "Diagnosis" in body and "reproducing test" in body
        assert "verified-by-test" in gh["labels"].call_args.args[3]
        assert "https://gh/pr/1" in gh["comment"].call_args.args[1]

    def test_failed_fix_comments_diagnosis_on_jira_without_pr(
        self, repo, gh, monkeypatch
    ):
        monkeypatch.setenv("PATCHY_LLM_MAX_ATTEMPTS", "1")
        llm = FakeLLM(_proposal(replace="    return a / b  # no-op\n"))

        result = self._run(_pr_state(repo), llm)

        assert "LLM fix failed" in result["message"]
        gh["create"].assert_not_called()
        gh["push"].assert_not_called()
        key, text = gh["comment"].call_args.args
        assert key == "ABC-1"
        assert "could not produce a verified fix" in text
        assert "Division by zero when b == 0." in text
        assert "'green'" in text

    def test_declined_fix_opens_no_pr(self, repo, gh):
        llm = FakeLLM(json.dumps({"cannot_fix": True, "reason": "unclear"}))

        result = self._run(_pr_state(repo), llm)

        assert "unclear" in result["message"]
        gh["create"].assert_not_called()

    def test_missing_fault_file_skips_llm(self, repo, gh):
        llm = FakeLLM()
        result = self._run(_pr_state(repo, fault_file=None), llm)

        assert "could not be located" in result["message"]
        assert llm.calls == []
        gh["create"].assert_not_called()

    def test_fault_file_outside_allowed_paths_skips_llm(self, repo, gh):
        llm = FakeLLM()
        result = self._run(_pr_state(repo, allowed_paths=["src/"]), llm)

        assert "outside allowed_paths" in result["message"]
        assert llm.calls == []
        assert _git(repo, "branch", "--show-current").strip() != ""
        assert "bugfix/" not in _git(repo, "branch")
