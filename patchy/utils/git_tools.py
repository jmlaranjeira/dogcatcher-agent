from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any


@dataclass
class RepoConfig:
    owner: str
    name: str
    default_branch: str = "main"
    allowed_paths: list[str] | None = None
    lint_cmd: str | None = None
    test_cmd: str | None = None


def _token() -> str:
    tok = os.getenv("GITHUB_TOKEN")
    if not tok:
        raise RuntimeError("Missing GITHUB_TOKEN in environment")
    return tok


def _workspace() -> Path:
    base = (
        os.getenv("PATCHY_WORKSPACE", "/tmp/patchy-workspace").strip()  # nosec B108
        or "/tmp/patchy-workspace"
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True)


def _https_origin_with_token(cfg: RepoConfig) -> str:
    # Token only in URL for remote operations; do not log this.
    tok = _token()
    return f"https://{tok}@github.com/{cfg.owner}/{cfg.name}.git"


def clone_repo(service: str, cfg: RepoConfig) -> Path:
    """Shallow clone into PATCHY_WORKSPACE/service.

    If the directory exists, remove it first to ensure a clean workspace.
    """
    dest = _workspace() / service
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    origin = f"https://github.com/{cfg.owner}/{cfg.name}.git"
    # Use --depth 2 for a small history
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "2",
            "--branch",
            cfg.default_branch,
            origin,
            str(dest),
        ],
        check=True,
    )

    # Re-point origin to token-injected URL for push operations
    subprocess.run(
        ["git", "remote", "set-url", "origin", _https_origin_with_token(cfg)],
        cwd=str(dest),
        check=True,
    )
    return dest


def git_create_branch(target_dir: Path, branch_name: str) -> None:
    _git("checkout", "-b", branch_name, cwd=target_dir)


def git_commit_push(target_dir: Path, message: str) -> None:
    _git("add", "-A", cwd=target_dir)
    # Configure a generic bot identity if not set
    try:
        _git(
            "-c",
            "user.name=patchy-bot",
            "-c",
            "user.email=patchy@company.com",
            "commit",
            "-m",
            message,
            cwd=target_dir,
        )
    except subprocess.CalledProcessError:
        # Nothing to commit? Re-raise to signal to caller
        raise
    _git("push", "-u", "origin", "HEAD", cwd=target_dir)
