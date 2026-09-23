# Patchy - Verified Fix PR Bot

Patchy turns a production error into a draft PR, but **only when it can prove the fix**. The LLM proposes a minimal code edit together with a new test that reproduces the error. Patchy opens a PR only if that test demonstrates the bug and the fix.

If Patchy can't produce a verified fix, it doesn't open a PR. It posts its diagnosis on the Jira ticket instead.

## Flow

```
resolve_repo → locate_fault → create_pr → finish
                                  │
                   ┌──────────────┴──────────────┐
                   │  LLM: edit + reproducing test │ ◄── feedback (test output)
                   └──────────────┬──────────────┘            ▲
                                  ▼                           │
           🔴 new test FAILS on original code with expected error
           🟢 new test PASSES with the edit applied            │
           ✅ full suite (test_cmd) still passes ── any fail ─┘ (≤ PATCHY_LLM_MAX_ATTEMPTS)
                                  │
                all passed?  yes → draft PR (+ Jira link)
                             no  → Jira comment with diagnosis, no PR
```

Patchy only fixes a file it has located, and only inside `allowed_paths`. Without a located file it stops before calling the LLM.

## Usage

```bash
python -m patchy.patchy_graph --service myservice --error-type npe --jira DDSIT-163 \
  --stacktrace "java.lang.NullPointerException at LicenseService.java:42"

# Locate the file from the logger name instead
python -m patchy.patchy_graph --service myservice --error-type npe --jira DDSIT-163 \
  --logger "org.example.myservice.service.LicenseService"
```

| Option | Description |
|--------|-------------|
| `--service` | Service name (required, must be in `repos.json`) |
| `--error-type` | Type of error (e.g., `npe`, `validation`) |
| `--stacktrace` | Stacktrace to locate the fault file/line |
| `--logger` | Java/Kotlin logger name to locate the file |
| `--hint` | Search hint (symbol/text) to locate the file |
| `--jira` | Jira key; gets the PR link, or the diagnosis if no fix is possible |
| `--loghash` | Log fingerprint (used in branch naming when no hint/type) |
| `--draft` | Create as draft PR (`true`/`false`, default `true`) |

## Configuration

`repos.json`, one entry per service:

```json
{
  "myservice": {
    "owner": "your-org",
    "name": "your-repo",
    "default_branch": "main",
    "allowed_paths": ["src/main/java/"],
    "lint_cmd": "",
    "test_cmd": "./mvnw -q -B test",
    "test_single_cmd": "./mvnw -q -B test -Dtest={test_class} -Dsurefire.failIfNoSpecifiedTests=false"
  }
}
```

- `test_single_cmd` runs a single test and accepts the placeholders `{test_class}` and `{test_path}`. If it's missing, Patchy infers one for Maven, Gradle, pytest or Jest. If no command can be found, Patchy refuses to run.
- `test_cmd` is the full suite. It's strongly recommended; without it only the new test is checked.
- `lint_cmd` runs before pushing.

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | – | Required |
| `LLM_PROVIDER` | `openai` | See `agent/llm_factory.py` |
| `REPAIR_ALLOWED_SERVICES` | all | CSV allow-list |
| `REPAIR_MAX_PRS_PER_RUN` | `1` | Per-run PR cap |
| `PATCHY_LLM_MAX_ATTEMPTS` | `3` | Retries, with test output fed back |
| `PATCHY_LLM_MAX_DIFF_LINES` | `80` | Reject larger edits |
| `PATCHY_LLM_MAX_TOKENS` | `8192` | LLM output budget |
| `PATCHY_TEST_TIMEOUT` | `900` | Seconds per test command |
| `PATCHY_WORKSPACE` | `/tmp/patchy-workspace` | Clone location |

## Safety

- **Nothing unverified ships.** A PR is opened only after the red/green/suite checks pass.
- **Exact edits:** the LLM returns search/replace blocks that must match exactly once, with a size cap.
- **Scoped:** edits stay inside `allowed_paths`, never touch existing tests, and the new test must be a new file in a test location.
- **Shell-safe:** test names and paths from the LLM are validated and shell-quoted.
- **Guardrails:** service allow-list, per-run cap, duplicate-branch check, draft PRs by default, and an audit log in `.agent_cache/audit_patchy.jsonl`.

## Integration with Dogcatcher

With `INVOKE_PATCHY=true` (and `GITHUB_TOKEN` set), the agent invokes Patchy after creating a Jira ticket. Sleuth invokes it with `--invoke-patchy`.

## Limitations

- **Business intent:** the LLM can't know intended business rules. Review the diagnosis and the test, not just the diff.
- **Single-file context:** the LLM sees the faulting file and one existing test, but not callers or related types.
- **Runs on the host:** tests run on the host, not in a sandbox. Only enable Patchy for trusted repositories.
