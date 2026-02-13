# Refactoring Plan: Complexity Hotspots

**Date**: February 10, 2026
**Last updated**: February 13, 2026
**Status**: In Progress
**Scope**: Structural refactoring without changing external behavior

---

## Context

During a review of the E2E flow of the Dogcatcher agent, we identified four complexity hotspots that affect maintainability, testability, and future scalability. This document proposes a plan to address each one, ordered by impact and priority.

### Current Pain Points

1. **Duplicate detection logic scattered across 3 files** with overlapping responsibilities
2. **Multi-tenant config relies on mutating `os.environ`** (fragile, blocks parallelization)
3. **Jira payload building mixed with orchestration** in `ticket.py` (~200 lines of formatting)
4. **LLM prompt operates in isolation** without contextual enrichment that could improve quality

---

## Refactor A: Unified DuplicateDetector ✅

**Priority**: High
**Estimated effort**: 2-3 days
**Status**: **COMPLETED** (February 13, 2026 — branch `refactor/unified-duplicate-detector`)
**Files affected**: `agent/nodes/ticket.py`, `agent/graph.py`, `agent/jira/match.py` (new: `agent/dedup/`)

### Problem

Duplicate detection currently lives in three places:

| Location | What it does |
|----------|-------------|
| `graph.py:analyze_log_wrapper` | In-memory `seen_logs` set (per-run local dedup) |
| `ticket.py:_check_duplicates` | Fingerprint cache + error_type label search + delegates to `find_similar_ticket` |
| `match.py:find_similar_ticket` | Loghash label fast-path + JQL token search + composite scoring |

This makes it hard to:
- Understand the full dedup chain at a glance
- Test each strategy in isolation
- Change the ordering or add new strategies
- Reason about which strategy caught a duplicate in audit logs

Additionally, the LLM's `create_ticket=False` decision is treated as "level 2" of dedup inside `_check_duplicates`, mixing two distinct concepts: *"this log is not actionable"* vs *"this log is a duplicate"*.

### Proposed Solution

Extract all dedup logic into a single `DuplicateDetector` class with a chain-of-responsibility pattern, ordered by cost:

```
agent/dedup/
  __init__.py
  detector.py        # DuplicateDetector orchestrator
  strategies.py      # Individual strategy implementations
  result.py          # DuplicateCheckResult dataclass
```

```python
# agent/dedup/detector.py

class DuplicateDetector:
    """Orchestrates duplicate detection through a chain of strategies,
    ordered from cheapest to most expensive."""

    def __init__(self, strategies: list[DedupStrategy]):
        self.strategies = strategies

    def check(self, log_data: dict, state: dict) -> DuplicateCheckResult:
        for strategy in self.strategies:
            result = strategy.check(log_data, state)
            if result.is_duplicate:
                return result
        return DuplicateCheckResult(is_duplicate=False)
```

Strategies in order:

| # | Strategy | Cost | Current location |
|---|----------|------|-----------------|
| 1 | `InMemorySeenLogs` | O(1), free | `graph.py:analyze_log_wrapper` |
| 2 | `FingerprintCache` | O(1), disk read | `ticket.py:_check_duplicates` (lines 179-209) |
| 3 | `LoghashLabelSearch` | 1 API call | `match.py:find_similar_ticket` (lines 122-142) |
| 4 | `ErrorTypeLabelSearch` | 1 API call | `ticket.py:_check_duplicates` (lines 219-267) |
| 5 | `SimilaritySearch` | 1+ API calls | `match.py:find_similar_ticket` (lines 144-224) |

The LLM's `create_ticket=False` decision should be handled **separately** in the graph routing logic (conditional edge), not inside the dedup chain. This cleanly separates *"not actionable"* from *"duplicate"*.

### Migration Steps

1. Create `agent/dedup/result.py` with the `DuplicateCheckResult` dataclass (already exists in `ticket.py`, move it)
2. Create `agent/dedup/strategies.py` with one class per strategy, each implementing a `check(log_data, state) -> DuplicateCheckResult` interface
3. Create `agent/dedup/detector.py` with the orchestrator
4. Update `graph.py:analyze_log_wrapper` to use `detector.check()` instead of inline set logic
5. Update `ticket.py:_check_duplicates` to delegate to `detector.check()` (or remove it entirely if the graph node handles it)
6. Move the LLM `create_ticket=False` check to the conditional edge in `graph.py` (it already partially does this)
7. Update audit logging to include `strategy_name` from the result
8. Add unit tests per strategy in `tests/unit/test_dedup_strategies.py`

### Risks

- **Low**: Pure refactoring, no behavior change. Each strategy is well-understood.
- The `find_similar_ticket` function in `match.py` can remain as-is internally; the `SimilaritySearch` strategy just wraps it.
- Ensure `created_fingerprints` set (in-run) and persisted fingerprints (disk) stay synchronized.

### Completion Notes

**Delivered artifacts:**

| File | Description |
|------|-------------|
| `agent/dedup/__init__.py` | Package init exposing `DuplicateCheckResult` and `DuplicateDetector` |
| `agent/dedup/result.py` | `DuplicateCheckResult` dataclass (moved from `ticket.py`) |
| `agent/dedup/strategies.py` | 5 strategy classes: `InMemorySeenLogs`, `FingerprintCache`, `LoghashLabelSearch`, `ErrorTypeLabelSearch`, `SimilaritySearch` |
| `agent/dedup/detector.py` | `DuplicateDetector` orchestrator with chain-of-responsibility pattern |
| `tests/unit/test_dedup_strategies.py` | 30 new tests covering all strategies + orchestrator |

**Modified files:**

- `agent/graph.py` — Uses `DuplicateDetector(strategies=[InMemorySeenLogs()])` instead of inline `seen_logs` logic
- `agent/nodes/ticket.py` — `_check_duplicates()` delegates to `_ticket_dedup` (strategies 2-5); audit logs include `strategy_name`
- `tests/unit/test_ticket_creation.py` — Updated mocks to target `_ticket_dedup` instead of individual function imports

**Test results:** 584 passed, 2 failed (pre-existing in `test_team_loader.py`), 5 skipped. Zero regressions from this refactor.

**Key design decisions:**
- LLM `create_ticket=False` decision is handled by the graph's conditional edge, **not** inside the dedup chain (clean separation of concerns)
- Strategy 1 runs at graph level (pre-LLM); strategies 2-5 run inside `create_ticket` node (post-LLM)
- `find_similar_ticket` in `match.py` remains unchanged; `SimilaritySearch` wraps it

---

## Refactor B: Eliminate Global Env Var Mutation (Multi-Tenant)

**Priority**: High
**Estimated effort**: 3-4 days
**Files affected**: `main.py`, `agent/config.py`, `agent/graph.py`, `agent/nodes/*.py`, `agent/jira/client.py`

### Problem

In multi-tenant mode (`main.py`, lines 247-266), the agent loops through teams and mutates `os.environ` before each run:

```python
for team_id in team_ids:
    for service in team.datadog_services:
        os.environ["JIRA_PROJECT_KEY"] = team.jira_project_key
        os.environ["DATADOG_SERVICE"] = service
        os.environ["DATADOG_ENV"] = team.datadog_env
        reload_config()  # invalidates global singleton
        _run_for_service(graph, team_id, team_service)
```

This is fragile because:
- If `_run_for_service` raises, env vars remain in the wrong state for subsequent teams
- `reload_config()` invalidates a global singleton that may be referenced by other code
- Parallelizing teams (e.g., with `asyncio.gather`) would cause race conditions
- Testing requires mocking `os.environ` globally

### Proposed Solution

Introduce a `RunConfig` object that is passed through the graph state instead of relying on globals.

**Phase 1 (incremental, safe)**: Wrap the env override in a context manager that guarantees restoration:

```python
# agent/utils/env_context.py

@contextmanager
def team_env_override(team: TeamConfig, service: str):
    """Temporarily override env vars for a team/service run.
    Guarantees restoration even on exceptions."""
    original = {}
    overrides = {
        "JIRA_PROJECT_KEY": team.jira_project_key,
        "DATADOG_SERVICE": service,
        "DATADOG_ENV": team.datadog_env,
        # ... other team-specific vars
    }
    try:
        for key, value in overrides.items():
            original[key] = os.environ.get(key)
            os.environ[key] = value
        reload_config()
        yield
    finally:
        for key, prev in original.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        reload_config()
```

Usage in `main.py`:
```python
for team_id in team_ids:
    for service in team.datadog_services:
        with team_env_override(team, service):
            _run_for_service(graph, team_id, team_service)
```

**Phase 2 (full solution)**: Replace the global config singleton with an injectable `RunConfig`:

```python
# agent/run_config.py

@dataclass(frozen=True)
class RunConfig:
    """Immutable per-run configuration. Replaces global env var reads."""
    jira_project_key: str
    datadog_service: str
    datadog_env: str
    max_tickets_per_run: int
    auto_create_ticket: bool
    similarity_threshold: float
    team_id: str | None = None
    team_service: str | None = None
    # ... other run-scoped settings

    @classmethod
    def from_team(cls, team: TeamConfig, service: str, base: AppConfig) -> "RunConfig":
        """Build a RunConfig from team + base config."""
        ...

    @classmethod
    def from_env(cls) -> "RunConfig":
        """Build a RunConfig from current environment (single-tenant)."""
        ...
```

This `RunConfig` would be passed into the graph via LangGraph's `config` parameter (or added to `GraphState`), and each node would read from it instead of calling `get_config()`.

### Migration Steps

**Phase 1** (do first, immediate safety improvement):
1. Create `agent/utils/env_context.py` with `team_env_override`
2. Update `main.py` loop to use the context manager
3. Add test that verifies env restoration after exception

**Phase 2** (do after Phase 1 is stable):
1. Create `agent/run_config.py` with `RunConfig` dataclass
2. Add `run_config` field to `GraphState` (in `agent/state.py`)
3. Update each node to read from `state["run_config"]` instead of `get_config()` for run-scoped values
4. Update `main.py` to build `RunConfig` per team/service and inject it into the graph state
5. Deprecate `reload_config()` — it should no longer be needed
6. Add integration test that runs two teams sequentially and verifies config isolation

### Risks

- **Phase 1**: Very low risk. Context manager is additive, doesn't change behavior.
- **Phase 2**: Medium risk. Requires touching every node. Mitigate by keeping `get_config()` for truly global settings (API keys, model name) and only moving run-scoped settings to `RunConfig`.

---

## Refactor C: Extract Jira Payload Builder

**Priority**: Medium
**Estimated effort**: 1-2 days
**Files affected**: `agent/nodes/ticket.py` (new: `agent/jira/payload.py`)

### Problem

`ticket.py` is ~870 lines with two distinct responsibilities:
1. **Orchestration**: validate, check duplicates, decide, execute, audit
2. **Formatting**: build description, build labels, build Datadog links, clean title

Functions like `_build_enhanced_description` (55 lines), `_build_datadog_links` (30 lines), `_build_labels` (27 lines), and `_clean_title` (19 lines) are pure formatting functions that don't depend on orchestration state. They're hard to test in isolation because they live in a module with many dependencies.

### Proposed Solution

Extract formatting functions to `agent/jira/payload.py`:

```
agent/jira/
  payload.py    # NEW: JiraPayloadBuilder (pure formatting, easy to test)
  client.py     # existing: API calls
  match.py      # existing: similarity search
  utils.py      # existing: normalization utilities
```

```python
# agent/jira/payload.py

class JiraPayloadBuilder:
    """Builds Jira ticket payloads. Pure functions, no side effects."""

    def __init__(self, config: AppConfig):
        self.config = config

    def build(self, state: dict, title: str, description: str) -> TicketPayload:
        fingerprint = compute_fingerprint(state.get("error_type", "unknown"),
                                          state.get("log_data", {}).get("message", ""))
        return TicketPayload(
            payload=self._build_fields(state, title, description, fingerprint),
            title=self.clean_title(title, state.get("error_type")),
            description=self.build_enhanced_description(state, description),
            labels=self.build_labels(state, fingerprint),
            fingerprint=fingerprint,
        )

    def build_enhanced_description(self, state: dict, description: str) -> str: ...
    def build_datadog_links(self, log_data: dict) -> str: ...
    def build_labels(self, state: dict, fingerprint: str) -> list[str]: ...
    def clean_title(self, title: str, error_type: str | None) -> str: ...
```

`ticket.py` becomes a clean orchestrator:
```python
def create_ticket(state):
    validation = _validate_ticket_fields(state)
    duplicate_check = detector.check(state)  # from Refactor A
    payload = JiraPayloadBuilder(config).build(state, validation.title, validation.description)
    return _execute_ticket_creation(state, payload)
```

### Migration Steps

1. Create `agent/jira/payload.py` and move the 4 formatting functions
2. Create `JiraPayloadBuilder` class wrapping them
3. Update `ticket.py:_build_jira_payload` to delegate to the builder
4. Move `TicketPayload` dataclass to `payload.py`
5. Add unit tests in `tests/unit/test_payload_builder.py` — these tests become trivial since the functions are pure

### Risks

- **Very low**: Pure extraction, no logic changes. All functions are already free of side effects.

---

## Refactor D: Contextual LLM Prompt Enrichment

**Priority**: Medium (evaluate with data first)
**Estimated effort**: 1 day for implementation, 1 week for A/B evaluation
**Files affected**: `agent/nodes/analysis.py`

### Problem

The LLM prompt (lines 48-64 of `analysis.py`) gives the model zero context beyond the raw log:

```
System: You are a senior support engineer. Analyze the input log...
Human:
[Logger]: com.app.service.UserService
[Thread]: http-nio-8080-exec-3
[Message]: User not found after registration
[Detail]: NullPointerException at line 42...
```

It does **not** receive:
- Occurrence count (50+ occurrences might warrant `high` severity)
- Environment (`prod` vs `dev` matters for severity)
- Service name (context for the error)
- Team-specific severity overrides (e.g., `email-not-found` is always `low` for team X)

This means:
- Severity calibration depends entirely on the LLM's interpretation of the log text
- Error types may be inconsistent across similar logs
- The LLM cannot distinguish a one-off error from a recurring pattern

### Proposed Solution

Selectively enrich the prompt with low-cost contextual signals. Keep it minimal to avoid token bloat (remember: `gpt-4.1-nano` works best with concise prompts).

**Enriched human prompt**:
```
[Service]: vega-api
[Environment]: prod
[Logger]: com.app.service.UserService
[Thread]: http-nio-8080-exec-3
[Message]: User not found after registration
[Detail]: NullPointerException at line 42...
[Occurrences in last 24h]: 47
[Severity hints]: email-not-found=low (team rule)
```

**Additional ~50 tokens per call. At nano pricing this is negligible.**

### What NOT to do

- Do NOT pass full Jira ticket lists to the LLM (expensive, unnecessary)
- Do NOT ask the LLM to do duplicate detection (the dedup chain is better at this)
- Do NOT pass previous error_types for "consistency" (the LLM should classify independently)

### Implementation Steps

1. Add `occurrences`, `service`, `env` to the contextual log string in `analyze_log()`
2. Load team severity overrides (if multi-tenant) and append as hints
3. Run in `--dry-run` mode for 1 week with the enriched prompt
4. Compare audit logs: check if severity distribution and `create_ticket` decisions improve
5. If positive, merge. If neutral/negative, revert.

### Evaluation Criteria

| Metric | How to measure | Target |
|--------|---------------|--------|
| Severity accuracy | Manual review of 50 audit log entries | > 85% agreement with human judgment |
| Error type consistency | Same log message across runs should produce same `error_type` | > 95% consistency |
| `create_ticket` precision | Tickets created that were actually actionable | > 90% precision |
| Token cost increase | Compare monthly OpenAI spend | < 10% increase |

### Risks

- **Low**: Additive change to the prompt. Worst case: revert.
- Watch for prompt length — if contextual info pushes nano into lower quality, the enrichment is counterproductive.

---

## Implementation Order

```
Week 1-2:  Refactor A (DuplicateDetector)               ✅ DONE (Feb 13, 2026)
              Reason: highest complexity reduction, enables cleaner audit logging

Week 2-3:  Refactor B Phase 1 (context manager for env vars)    ⏳ NEXT
              Reason: quick safety win, eliminates env var corruption risk

Week 3-4:  Refactor C (Payload builder extraction)
              Reason: low effort, high testability improvement
              Can be done in parallel with Refactor B Phase 1

Week 4-5:  Refactor D (LLM prompt enrichment)
              Reason: requires A/B evaluation period
              Start implementation, then monitor for 1 week

Week 6-8:  Refactor B Phase 2 (RunConfig injection)
              Reason: highest effort, depends on Phase 1 being stable
              Enables future parallelization of multi-tenant runs
```

### Dependency Graph

```
Refactor A ──────────────────────────┐
                                     ├──> Full integration test
Refactor B Phase 1 ──> Refactor B Phase 2 ──┘
                                     │
Refactor C (independent) ────────────┘

Refactor D (independent, A/B eval) ──> Decision: merge or revert
```

### Success Criteria

- All existing tests pass after each refactor (zero regressions)
- `python run_tests.py` green at every step
- Dry-run produces identical audit logs before/after (except for new fields like `strategy_name`)
- No change in Jira API call volume (Refactors A, B, C)
- Measurable improvement in code coverage for dedup and payload logic
