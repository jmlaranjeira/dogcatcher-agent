# Datadog → LLM → Jira Agent (Watcher)

## Overview
Reads Datadog error logs, analyzes them with an LLM, de-duplicates, and creates Jira tickets with guardrails.

## Key Env (.env)
- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TEMPERATURE`, `OPENAI_RESPONSE_FORMAT`
- Datadog: `DATADOG_API_KEY`, `DATADOG_APP_KEY`, `DATADOG_SITE`, `DATADOG_SERVICE`, `DATADOG_ENV`, `DATADOG_HOURS_BACK`, `DATADOG_LIMIT`, `DATADOG_MAX_PAGES`, `DATADOG_TIMEOUT`, `DATADOG_QUERY_EXTRA`, `DATADOG_QUERY_EXTRA_MODE`, `DATADOG_STATUSES`
- Jira: `JIRA_DOMAIN`, `JIRA_USER`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`
- Behavior: `AUTO_CREATE_TICKET`, `PERSIST_SIM_FP`, `COMMENT_ON_DUPLICATE`, `MAX_TICKETS_PER_RUN`, `SEVERITY_RULES_JSON`, `COMMENT_COOLDOWN_MINUTES`
- Optional aggregation: `AGGREGATE_EMAIL_NOT_FOUND`, `AGGREGATE_KAFKA_CONSUMER`
- Optional severity escalation: `OCC_ESCALATE_ENABLED`, `OCC_ESCALATE_THRESHOLD`, `OCC_ESCALATE_TO`

## Run
```bash
# Dry run
python main.py --dry-run --env dev --service dehnlicense --hours 24 --limit 50

# Real (creates tickets; cap applies)
python main.py --real --env prod --service dehnlicense --hours 48 --limit 100 --max-tickets 5
```

## Priority mapping
- severity `low|medium|high` → Jira priority `Low|Medium|High` (unified helper)
- Defaults to `low` when not provided
- Optional: `SEVERITY_RULES_JSON` can override per `error_type`

## Duplicates & audit
- Fingerprint cache across runs, loghash label seeding, JQL similarity search
- Audit JSONL at `.agent_cache/audit_logs.jsonl`

## Docker Compose
```bash
# Dry run service example
# Update env in .env and run

docker compose up --build
```
