# Patchy (🩹🤖) – Verified Fix PR Bot

## Overview
Patchy clones the target repo for a service and locates the faulting file from the stacktrace, logger or hint. It then asks the LLM for a minimal fix **plus a new test that reproduces the error**, and opens a draft PR only when that test proves the fix. If no fix can be verified, Patchy comments its diagnosis on the Jira ticket and opens no PR.

See [`patchy/README.md`](../../patchy/README.md) for the full flow, configuration and guardrails.

## Requirements
- Python 3.11
- `GITHUB_TOKEN` with `repo` scope
- LLM credentials (`LLM_PROVIDER`, see the main configuration)
- The target repo's build/test toolchain available on the host (e.g. JDK + Maven)
- Optional: `ripgrep (rg)` for hint search

## Service configuration: `patchy/repos.json`
```json
{
  "myservice": {
    "owner": "your-org",
    "name": "example-backend-service",
    "default_branch": "develop",
    "allowed_paths": ["src/main/java/"],
    "lint_cmd": "",
    "test_cmd": "./mvnw -q -B test",
    "test_single_cmd": "./mvnw -q -B test -Dtest={test_class} -Dsurefire.failIfNoSpecifiedTests=false"
  }
}
```
- `allowed_paths`: the paths Patchy is allowed to modify.
- `test_single_cmd`: runs the new reproducing test; accepts the placeholders `{test_class}` and `{test_path}`. It's inferred for Maven, Gradle, pytest or Jest if omitted.
- `test_cmd`: the full suite, which must still pass after the fix.
- `lint_cmd`: runs before pushing the branch.

## CLI
```bash
python -m dotenv -f .env run -- \
python -m patchy.patchy_graph \
  --service myservice \
  --error-type npe \
  --loghash 4c452e2d1c49 \
  --jira DPRO-1234 \
  --draft true \
  --stacktrace "at com.acme.Foo.bar(Foo.java:123)"
```

## Behavior
- Resolve the repo via `repos.json`; shallow clone into `PATCHY_WORKSPACE/service`.
- Locate the fault file from `--stacktrace`, `--logger` or `--hint`. Stop if the file isn't found or is outside `allowed_paths`.
- Create branch `bugfix/<JIRA-KEY>-<brief>` (or `bugfix/<service>-<brief>`).
- LLM fix loop (up to `PATCHY_LLM_MAX_ATTEMPTS`):
  - the new test must fail on the original code with the expected error,
  - then pass with the edit applied,
  - and `test_cmd` must still pass.
- On success: run `lint_cmd`, push, open a draft PR labelled `auto-fix`, `patchy`, `llm-fix` and `verified-by-test`, and comment the PR link on Jira.
- On failure: comment the diagnosis and failed attempts on Jira; no PR.
- Every step is logged to `.agent_cache/audit_patchy.jsonl`.

## Docker Compose
```bash
docker compose run --rm -e GITHUB_TOKEN=$GITHUB_TOKEN patchy \
  python -m patchy.patchy_graph --service myservice --error-type npe --loghash 4c452e2d1c49 --draft true
```

## Troubleshooting
- 401 from GitHub: check the `GITHUB_TOKEN` permissions.
- "No test_single_cmd configured or inferable": add `test_single_cmd` to `repos.json`.
- "LLM fix failed": the audit log and the Jira comment show which stage failed (`red`, `green`, `suite`) on each attempt.
- No PR created: check for a duplicate branch, the allow-list, or `allowed_paths`.
