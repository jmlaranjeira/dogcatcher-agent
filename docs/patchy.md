# Patchy (🩹🤖) – Self-healing Draft PR Bot

## Overview
Patchy clones the target repo for a service, creates a small, safe change, and opens a draft PR with guardrails. It can optionally locate a fault via stacktrace/hint and comment the PR link in a Jira ticket.

## Requirements
- Python 3.11
- `GITHUB_TOKEN` with `repo` scope
- Optional: `ripgrep (rg)` installed for hint search

## Environment Variables
- `GITHUB_TOKEN` (required)
- `PATCHY_WORKSPACE` (default `/tmp/patchy-workspace`)
- `REPAIR_ALLOWED_SERVICES` (CSV allow-list; empty = all)
- `REPAIR_MAX_PRS_PER_RUN` (default `1`)

## Service configuration: `patchy/repos.json`
Per service:
```json
{
  "dehnproject": {
    "owner": "dehn",
    "name": "dehn-project-service",
    "default_branch": "develop",
    "allowed_paths": ["README.md"],
    "lint_cmd": "",
    "test_cmd": ""
  }
}
```
- `allowed_paths`: Paths Patchy is allowed to modify.
- `lint_cmd` / `test_cmd`: Commands run before pushing the branch.

## CLI
```bash
# Load .env automatically
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph \
  --service dehnproject \
  --error-type npe \
  --loghash 4c452e2d1c49 \
  --jira DPRO-1234 \
  --draft true \
  --mode note \
  --stacktrace "at src/main/java/com/acme/Foo.java:123" \
  --hint "NullPointerException"
```

Flags relevant to editing mode:
- `--mode` (default: `note`): one of `touch | note | fix`.
  - `touch`: create (or overwrite) a file with Patchy metadata. Safer when using a dedicated file (e.g., `PATCHY_TOUCH.md`).
  - `note`: append a small Patchy note to the chosen file if it exists; otherwise create a new metadata file.
  - `fix`: attempt a minimal, language‑aware placeholder fix (currently prepends guidance comments). Intended as v0 scaffold; real auto‑fix rules can be added per language.

## Behavior
- Resolve repo via `repos.json`; shallow clone to `PATCHY_WORKSPACE/service`.
- Create branch `fix/{service}/{loghash[:8]}`.
- Locate fault from `--stacktrace`/`--hint` (best‑effort). Prefer editing that file if allowed.
- Apply change based on `--mode`:
  - `touch`: create/overwrite target with Patchy metadata
  - `note`: append Patchy note if file exists; else create metadata file
  - `fix`: prepend guidance comment (language‑aware placeholder)
- Run `lint_cmd`/`test_cmd` if configured; abort on failure.
- Create draft PR; label `auto-fix`, `patchy`, `low-risk`.
- If `--jira`, comment in the Jira issue with `Refs: (KEY) <PR_URL>`.
- Audit to `.agent_cache/audit_patchy.jsonl`.

## Naming and PR conventions
- Branch name:
  - With Jira: `bugfix/<JIRA-KEY>-<brief>` (e.g., `bugfix/DPRO-2491-priceMissing`)
  - Without Jira: `bugfix/<service>-<brief>`
  - `<brief>` is camelCase from `--hint` or `--error-type`.
- PR title: starts with `fix: ...` (same as commit message).
- PR body:
  - Short human-readable summary of what is improved/changed.
  - Ends with Jira reference when provided: `Refs: [#DPRO-1234](https://<your-jira>/browse/DPRO-1234)`.

## Docker Compose
```bash
# env file is used for secrets/config
docker compose run --rm -e GITHUB_TOKEN=$GITHUB_TOKEN patchy \
  python -m patchy.patchy_graph --service dehnproject --error-type npe --loghash 4c452e2d1c49 --draft true
```

## Troubleshooting
- 401 from GitHub: check `GITHUB_TOKEN` permissions.
- Lint/test failures abort the PR creation: see console and audit log.
- No PR created: check duplicate branch or allow-list restrictions.
