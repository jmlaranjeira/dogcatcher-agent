# Dogcatcher-Agent Review & Improvement Plan

The repository is now called **dogcatcher-agent** (previously `langgraph-agent-demo`).  
Please replace any outdated references with the correct name in code, docs, and configs.

You already created a review in `docs/review-120925.md`.  
Now we want you to start improving the project step by step.

---

## 🛠️ Step-by-Step Improvements

### 1. Logging & Security (High)
- Remove or sanitize sensitive outputs (tokens, domains, payload dumps).  
- Introduce a proper logger with levels (`DEBUG/INFO/WARN/ERROR`).  
- Add a helper `safe_json(obj)` to anonymize emails/UUIDs/tokens before logging.  
- Deliverable: PR `chore(logging): sanitize sensitive outputs`.

### 2. Refactor `create_ticket` (High)
- Extract helpers:
  - `_validate_ticket_fields(state)`
  - `_check_duplicates(state)`
  - `_build_jira_payload(state)`
  - `_execute_ticket_creation(state)`  
- Keep `create_ticket` as a short orchestrator.  
- Deliverable: PR `refactor(ticket): extract creation workflow into helpers`.

### 3. Configuration Schema (Medium)
- Use **Pydantic BaseSettings** to validate `.env` at startup.  
- Define types and ranges (e.g., `DATADOG_LIMIT >= 10`).  
- Move **magic numbers** (max title length, thresholds) into config.  
- Deliverable: PR `feat(config): pydantic settings + validated defaults`.

### 4. Minimal Test Coverage (High)
- Add `tests/` with at least:
  - `test_create_ticket_success`
  - `test_create_ticket_duplicate`
  - `test_create_ticket_validation_error`
  - Normalization + threshold checks  
- Deliverable: PR `test(core): minimal unit tests for ticket flow`.

### 5. Performance & DX (Medium)
- Tune Jira search window and `max_results`.  
- Add an in-run cache (e.g. LRU) for repeated similarity checks.  
- Expose thresholds in `.env` and log their values at startup.  
- Deliverable: PR `perf(jira): tune search + similarity cache`.

### 6. Developer Onboarding Docs (Medium)
- Add `README-DEV.md` with:
  - Prerequisites
  - How to run dry-run vs real
  - How to configure `.env`
  - How to run tests
  - How to generate reports (`tools/report.py`)  
- Deliverable: PR `docs: developer onboarding + contribution tips`.

### 7. Rename Consistency (High)
- Replace any leftover `langgraph-agent-demo` references with `dogcatcher-agent` across:
  - `README.md`, `agent.md`, `patchy.md`, `commands.md`  
  - Dockerfiles, compose, scripts, etc.  
- Deliverable: PR `chore(rename): replace legacy repo name`.

---

## 📄 Output

For each step, update or create a Markdown note under `docs/` (e.g. `docs/progress-logging.md`, `docs/progress-refactor.md`) summarizing:
- What was changed
- Why it was needed
- How to test or validate it

This will create a living changelog of improvements.

---

## Extra Credit (Optional)
- Retry logic with **tenacity** on external API calls.  
- Performance metrics decorator to benchmark log analysis.  
- End-to-end integration test for dry-run mode.

---

**Next Action:**  
Start with Step 1 (Logging & Security) and produce the PR + doc update.  
Then proceed sequentially.
