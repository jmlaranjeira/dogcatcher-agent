# Plan: Sleuth Agent (Investigator)

## Summary

Create a new **Sleuth** agent that enables error investigation in Datadog through natural language queries, correlates findings with existing Jira tickets, and optionally suggests invoking Patchy for automatic fixes.

## Design Decisions

| Aspect | Decision |
|--------|----------|
| Interface | Interactive CLI (like Patchy) |
| DD Autonomy | Smart query - LLM builds the query |
| Patchy Integration | Suggest fix if correctable error detected |

## Architecture

```
sleuth/
├── __init__.py
├── sleuth_graph.py      # LangGraph definition + CLI entry point
├── sleuth_nodes.py      # Node implementations + SleuthState
└── utils/
    ├── __init__.py
    └── query_builder.py  # LLM-powered query construction
```

## Graph Flow

```
┌─────────────────┐
│  parse_query    │  ← Input: natural language description
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  build_dd_query │  ← LLM generates optimal Datadog query
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  search_logs    │  ← Execute Datadog search
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ correlate_jira  │  ← Search for related tickets
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ analyze_results │  ← LLM analyzes and summarizes findings
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ suggest_action  │  ← Offer to invoke Patchy if applicable
└────────┬────────┘
         │
         ▼
       [END]
```

## State Definition (SleuthState)

```python
class SleuthState(TypedDict, total=False):
    # Input
    query: str              # Natural language description
    service: str            # Service (optional, can be inferred)
    env: str                # Environment (optional)
    hours_back: int         # Time window (default: 24)

    # Generated
    dd_query: str           # Query built for Datadog
    logs: list[dict]        # Logs found

    # Correlation
    related_tickets: list[dict]  # Related Jira tickets

    # Analysis
    summary: str            # LLM summary
    root_cause: str         # Identified root cause (if applicable)
    suggested_fix: str      # Fix suggestion (if applicable)

    # Action
    can_auto_fix: bool      # Whether Patchy could fix it
    patchy_invoked: bool    # Whether Patchy was invoked
    patchy_result: str      # Patchy result
```

## Implementation by File

### 1. `sleuth/sleuth_graph.py`

```python
# CLI entry point
parser.add_argument("query", nargs="+", help="Description of error to investigate")
parser.add_argument("--service", default=None)
parser.add_argument("--env", default=None)
parser.add_argument("--hours", type=int, default=24)
parser.add_argument("--no-patchy", action="store_true", help="Don't suggest automatic fixes")

# Graph definition
g = StateGraph(SleuthState)
g.set_entry_point("parse_query")
g.add_node("parse_query", parse_query)
g.add_node("build_dd_query", build_dd_query)
g.add_node("search_logs", search_logs)
g.add_node("correlate_jira", correlate_jira)
g.add_node("analyze_results", analyze_results)
g.add_node("suggest_action", suggest_action)
# Linear flow with conditional edge at suggest_action
```

### 2. `sleuth/sleuth_nodes.py`

**parse_query**: Extract entities from natural language query (service, keywords, user IDs, etc.)

**build_dd_query**: Use OpenAI to build optimal query:
```python
prompt = f"""
Build a Datadog Logs query to investigate:
"{user_query}"

Context:
- Service: {service or "infer from context"}
- Environment: {env or "prod"}

Generate ONLY the query string, example:
service:myservice env:prod status:error "user registration" "role"
"""
```

**search_logs**: Reuse `agent.datadog.get_logs()` with custom query

**correlate_jira**: Reuse `agent.jira.match.find_similar_ticket()` for each unique log

**analyze_results**: LLM summarizes findings:
```python
prompt = f"""
Analyze these error logs and related tickets.
User asked: "{original_query}"

Logs found: {logs_summary}
Related tickets: {tickets_summary}

Provide:
1. Executive summary (2-3 sentences)
2. Probable root cause
3. If ticket exists, reference it
4. If no ticket exists and it's correctable, suggest fix
"""
```

**suggest_action**: If `can_auto_fix=True`, offer to invoke Patchy

### 3. `sleuth/utils/query_builder.py`

Utility for building smart queries:
- Entity extraction (emails, UUIDs, service names)
- Common query templates
- Term normalization

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `sleuth/__init__.py` | Create | Package init |
| `sleuth/sleuth_graph.py` | Create | Graph + CLI (~80 lines) |
| `sleuth/sleuth_nodes.py` | Create | Nodes + State (~200 lines) |
| `sleuth/utils/__init__.py` | Create | Utils init |
| `sleuth/utils/query_builder.py` | Create | Query builder (~60 lines) |
| `CLAUDE.md` | Edit | Document Sleuth usage |

## Code Reuse from Existing Components

- `agent.datadog.get_logs()` → Datadog search
- `agent.jira.client.search()` → Jira JQL search
- `agent.jira.match.find_similar_ticket()` → Ticket correlation
- `agent.config.get_config()` → Centralized configuration
- `agent.utils.logger` → Consistent logging
- `patchy.patchy_graph.build_graph()` → Optional Patchy invocation

## Usage Example

```bash
# Investigate user without role error
python -m sleuth "user without role after registration" --service myservice

# Expected output:
🔍 Investigating: "user without role after registration"
📊 Generated query: service:myservice env:prod status:error "role" "registration"
📋 Logs found: 12

📌 Summary:
Found 12 errors related to role assignment during user registration.
The main error is RoleAssignmentException in UserService:234 when
the role service doesn't respond.

🎫 Related tickets:
- PROJ-456: "Role assignment error during registration" (Open, 2 days ago)

🩹 Would you like Patchy to attempt an automatic fix? [y/N]
```

## Tests

Create `tests/unit/test_sleuth.py`:
- Test parse_query with different inputs
- Test build_dd_query generates valid queries
- Test correlate_jira finds tickets
- Mock OpenAI for deterministic tests

## Implementation Order

1. Create `sleuth/` directory structure
2. Implement `SleuthState` and basic nodes
3. Implement `query_builder.py`
4. Connect LangGraph
5. Add CLI
6. Unit tests
7. Update documentation
