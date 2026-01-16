# 🧠 Datadog → LLM → Jira Agent (LangGraph)

Automated agent that reads **Datadog error logs**, analyzes them with an **LLM** (LangChain + OpenAI), performs **smart de‑duplication** against Jira, and creates tickets with a **configurable per‑run cap** (default **3**).

---

## 🔄 End‑to‑end Flow
```mermaid
graph TD;
    A[Fetch Datadog Logs] --> B[Deduplicate In-Run];
    B --> C[Analyze Log / LLM];
    C -->|no ticket| G[Next Log];
    C -->|create ticket| D[Check Similar Issues in Jira];
    D -->|duplicate| E[Comment on Existing Ticket];
    D -->|unique| F[Create Jira Ticket];
    E --> G;
    F --> G;
```

---

## 🚀 Key Features
- **Datadog ingestion** (service/env/time window) with `logger`, `thread`, `timestamp`, `detail`.
- **LLM analysis (gpt‑4o‑mini)** → `error_type`, `severity (low|medium|high)`, `ticket_title`, `ticket_description` (markdown: *Problem summary*, *Possible Causes*, *Suggested Actions*).
- **Ticket guard**: configurable per‑run cap via `MAX_TICKETS_PER_RUN` or `--max-tickets` (**0 = no cap**, default **3**).
- **Idempotence & de‑dup**
  - In‑run: skip duplicated logs by `logger|thread|message`.
  - Cross‑run: fingerprint (`sha1`) cached in `.agent_cache/processed_logs.json`.
  - Jira short‑circuit via `labels = loghash-<sha1(normalized message)[:12]>`.
- **Advanced Jira duplicate detection**
  - **Direct log match first**: compare the **normalized current log** with the ticket’s **Original Log** (extracted from description). If similarity ≥ **0.90** → duplicate immediately.
  - Otherwise, similarity by **title + description** with boosts (error_type, logger, token overlap). Window **365d**, filtering `statusCategory != Done` and `labels = datadog-log`.
  - When a duplicate is detected, optionally **auto‑comment** with new context and **retro‑seed** the `loghash-…` label on the existing issue.
- **Ticket formatting**
  - Summary: **`[Datadog][<error_type>] <title>`** (title auto‑truncated to 120 chars)
  - Labels: **`datadog-log`** (+ `loghash-…` where applicable)
  - Description includes **Original Log**, logger/thread/timestamp, detail, and **occurrence count** within the window.
- **Noise‑aware context**
  - The agent aggregates **occurrence counts** per fingerprint and shows: `Occurrences in last <hours>h: <N>` in descriptions and duplicate comments.

---

## 🧰 Requirements
- Python **3.11**.
- Install deps:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
*(Optional) `rapidfuzz` improves matching — already listed in `requirements.txt`.*

---

## ⚙️ Configuration

### Environment Variables (.env)

The project uses environment variables for configuration. You can override these with:
- **Configuration profiles** (YAML files in `config/profiles/`) - **Recommended**
- **Environment variables** (system-level overrides)
- **CLI arguments** (runtime overrides)

```ini
# OpenAI
OPENAI_API_KEY=sk-...                # API key for OpenAI access
OPENAI_MODEL=gpt-4.1-nano             # OpenAI model to use for analysis
OPENAI_RESPONSE_FORMAT=json_object   # Response format: json_object (default) or text
OPENAI_TEMPERATURE=0                 # Controls randomness (0 = deterministic)

# Datadog
DATADOG_API_KEY=...                  # Datadog API key
DATADOG_APP_KEY=...                  # Datadog application key
DATADOG_SITE=datadoghq.eu            # Datadog site to query (e.g., datadoghq.com or datadoghq.eu)
DATADOG_SERVICE=dehnlicense          # Datadog service name filter
DATADOG_ENV=dev                      # Datadog environment filter (e.g., dev or prod)
DATADOG_HOURS_BACK=48                # Time window in hours to fetch logs from
DATADOG_LIMIT=50                     # Number of logs per page to fetch
DATADOG_MAX_PAGES=3                  # Maximum pages to paginate through
DATADOG_TIMEOUT=20                   # Timeout in seconds for Datadog API requests
DATADOG_STATUSES=error,critical      # Comma-separated list of log statuses to filter
DATADOG_QUERY_EXTRA=                 # Extra query terms for Datadog search (optional)
DATADOG_QUERY_EXTRA_MODE=AND         # Logical operator for extra query terms (AND or OR)

# Jira
JIRA_DOMAIN=company.atlassian.net  # Jira instance domain
JIRA_USER=you@company.com               # Jira user email
JIRA_API_TOKEN=...                      # Jira API token for authentication
JIRA_PROJECT_KEY=DPRO                   # Jira project key for ticket creation

# Agent behavior
AUTO_CREATE_TICKET=false             # true/1/yes to create real tickets; false to simulate
PERSIST_SIM_FP=false                 # Persist fingerprints even in simulation mode
COMMENT_ON_DUPLICATE=true            # Add comments on detected duplicate issues
MAX_TICKETS_PER_RUN=3                # Per-run cap on real ticket creation (0 = no cap)
SEVERITY_RULES_JSON=                 # JSON mapping error_type→severity, e.g. {"blob-not-found":"medium"}
COMMENT_COOLDOWN_MINUTES=120         # Minutes to wait before re-commenting the same Jira issue (0 disables)
AGGREGATE_EMAIL_NOT_FOUND=false      # Aggregate email-not-found into one parent ticket (adds label aggregate-email-not-found)
AGGREGATE_KAFKA_CONSUMER=false       # Aggregate kafka-consumer into one parent ticket (adds label aggregate-kafka-consumer)

# Optional occurrence-based escalation (disabled by default)
OCC_ESCALATE_ENABLED=false           # Enable escalation based on occurrences in time window
OCC_ESCALATE_THRESHOLD=10            # Escalate when occurrences >= threshold
OCC_ESCALATE_TO=high                 # Target severity when escalating (low|medium|high)
```

Notes:
- The agent prints the **exact Datadog query** for transparency.
- If a query with `DATADOG_QUERY_EXTRA` yields 0 results, it will **retry once without** the extra clause and report findings to help tuning.

---

## ▶️ Run

### Basic Usage
```bash
python main.py
```
- **Early exit** if no logs are available in the selected window.
- **Simulation** (`AUTO_CREATE_TICKET=false`): analyzes logs and simulates ticket creation.
- **Real** (`AUTO_CREATE_TICKET=true`): creates **up to 3** real tickets per run; on duplicates it comments and does not create.

### With Configuration Profiles (Recommended)

Use pre-configured environment profiles for easier management:

```bash
# Development (safe, no tickets, file cache, DEBUG)
python main.py --profile development

# Staging (limited tickets, file cache, INFO)
python main.py --profile staging --service myservice

# Production (auto-create, Redis cache, WARNING)
python main.py --profile production

# Testing (minimal, memory cache)
python main.py --profile testing
```

**Available profiles:** `development` | `staging` | `production` | `testing`
**Profile files:** `config/profiles/*.yaml`
**Precedence:** `.env` → Profile YAML → Environment Variables → CLI Arguments

### With CLI Arguments

You can also run with direct CLI arguments:
```bash
python main.py --dry-run --env dev --service dehnlicense --hours 24 --limit 50
```
```bash
python main.py --real --env prod --service dehnlicense --hours 48 --limit 100 --max-tickets 5
```

**CLI Arguments:**
- `--profile`: configuration profile (`development|staging|production|testing`)
- `--dry-run`: run in simulation mode without creating Jira tickets
- `--real`: (alternative to --dry-run) run in real mode and create tickets
- `--env`: Datadog environment to query (`dev` or `prod`)
- `--service`: Datadog service name to filter logs
- `--hours`: time window in hours for logs to fetch
- `--limit`: maximum logs per page from Datadog
- `--max-tickets`: per-run cap on real ticket creation (0 = no cap)

---

## Patchy v0 – Draft PR flow (🩹🤖)

Patchy is a minimal self-healing PR bot that, given a `service`, `error_type` and `loghash`, clones the target repo, creates a tiny change, and opens a draft PR with guardrails.

### CLI
```bash
# env
export GITHUB_TOKEN=ghp_xxx
export PATCHY_WORKSPACE=/tmp/patchy-workspace

python -m patchy.patchy_graph \
  --service dehnlicense \
  --error-type npe \
  --loghash 4c452e2d1c49 \
  --draft true
```

### Env
- `GITHUB_TOKEN` (required)
- `PATCHY_WORKSPACE` (default `/tmp/patchy-workspace`)
- `REPAIR_ALLOWED_SERVICES` (CSV allow-list; empty = all)
- `REPAIR_MAX_PRS_PER_RUN` (default 1)

### Behavior
- Nodes: `resolve_repo` → `locate_fault` (placeholder) → `create_pr` → `finish`.
- Shallow clone with token-injected remote, new branch `fix/{service}/{loghash[:8]}`.
- Touch file `PATCHY_TOUCH.md` with metadata; commit & push; open draft PR.
- PR title: `fix({service}): auto-fix for {error_type} [{loghash}]`.
- PR body includes Jira (if provided) and `loghash-<sha>` tag.
- Guardrails: allow-list, per-run cap, duplicate PR check by branch.
- Audit JSONL: `.agent_cache/audit_patchy.jsonl`.

### Docker (optional)
Add a service to `docker-compose.yml` (example):
```yaml
  patchy:
    build:
      context: .
      dockerfile: Dockerfile
    image: patchy-agent:latest
    environment:
      - GITHUB_TOKEN=${GITHUB_TOKEN}
      - PATCHY_WORKSPACE=/workspace
    volumes:
      - ./_patchy_workspace:/workspace
    command: ["python","-m","patchy.patchy_graph","--service","dehnlicense","--error-type","npe","--loghash","4c452e2d1c49"]
```

## 📈 Reporting (tools/report.py)
The agent writes an audit trail to `.agent_cache/audit_logs.jsonl`. You can summarize recent runs with:

```bash
python tools/report.py --since-hours 24
```

**What you get** (key fields):
- **Total decisions**
- **Tickets created** (real mode)
- **Duplicates / no-create** (skipped)
- **Simulated (dry-run)** and **Would create (simulated true)**
- Optional: **Cap reached (limit)** counts
- Breakdowns: **By error_type**, **By severity**, **By decision**
- Top **fingerprints** and **Jira issues**

Example output snippet:

```
=== Audit Summary ===
Total decisions: 42
Tickets created: 3
Duplicates / no-create: 28
Simulated (dry-run): 11
Would create (simulated true): 8
By decision:
  created: 3
  duplicate-fingerprint: 18
  duplicate-jira: 7
  simulated: 11
  no-create: 3
```

**Notes**
- The report reads from `.agent_cache/audit_logs.jsonl`. To start fresh, rotate or delete that file.
- `--since-hours` filters by time window; omit it to include all history.

## 🐳 Docker
You can run the agent in a container (handy for CI or a long‑running service).

### Build
```bash
docker build -t dd-jira-agent:latest .
```

### Dry‑run (safe)
```bash
docker run --rm \
  --env-file ./.env \
  -e AUTO_CREATE_TICKET=false \
  -v $(pwd)/.agent_cache:/app/.agent_cache \
  dd-jira-agent:latest \
  python main.py --dry-run --env dev --service dehnlicense --hours 24 --limit 50
```

### Real mode (creates Jira tickets — be cautious)
```bash
docker run --rm \
  --env-file ./.env \
  -e AUTO_CREATE_TICKET=true \
  -v $(pwd)/.agent_cache:/app/.agent_cache \
  dd-jira-agent:latest \
  python main.py --real --env prod --service dehnlicense --hours 48 --limit 100 --max-tickets 5
```

### docker‑compose (optional)
`docker-compose.yml` includes a ready‑to‑run service:
```bash
docker compose up --build
```
It mounts `.agent_cache` as a volume and loads variables from `.env`. Override CLI flags via `command:` in the compose file.

---

## 🧪 Duplicate Matching — Details
1. **Fingerprints**: skip if `logger|thread|message` seen in current run; persist across runs in `.agent_cache/processed_logs.json`.
2. **Exact log match**: normalize current log and compare to issue’s **Original Log** (from description). If similarity ≥ **0.90**, return that issue.
3. **Similarity scoring**: `0.6*title_sim + 0.3*desc_sim` with boosts (`+0.10` error_type, `+0.05` logger, `+0.05` token overlap). Threshold **0.82**.
4. **Labels**: on duplicate, add `loghash-<…>` to the existing issue to enable O(1) future matches by label.

Normalization removes email addresses, URLs/tokens, UUIDs, timestamps, long hashes, and collapses whitespace to make matches robust.

---

## 📦 Project Structure
```
dogcatcher-agent/
├── main.py                # Entrypoint & CLI
├── .env                   # Secrets & config
├── Dockerfile             # Container image
├── docker-compose.yml     # Optional compose service
├── .dockerignore
├── agent/
│   ├── datadog.py         # Fetch & parse logs
│   ├── graph.py           # LangGraph wiring
│   ├── state.py           # Shared state types
│   └── nodes/             # Agent nodes (split from nodes.py)
│       ├── __init__.py    # Node registry / exports
│       ├── analysis.py    # LLM analysis node
│       ├── ticket.py      # Ticket creation node
│       └── ...            # (other agent nodes as needed)
│   └── jira/              # Jira integration (modular)
│       ├── __init__.py    # Public API: create_ticket, comment_on_issue, find_similar_ticket
│       ├── client.py      # HTTP helpers (search/create/comment/labels)
│       ├── match.py       # Similarity, JQL, Original Log extraction
│       └── utils.py       # Normalization, loghash, fingerprint & comment caches
├── tools/
│   └── report.py          # Audit report generator
└── requirements.txt
```

---

## 🛠️ Troubleshooting
- No real ticket created → likely a fingerprint or a duplicate (the console prints the reason and any similarity score).
- 0 results from Datadog with `DATADOG_QUERY_EXTRA` → check the printed query; the agent will probe without extra and report.
- Jira 401/403 → verify domain/user/token and project permissions.
- Recursion guard → `recursion_limit` is raised to handle longer runs; ensure your environment matches `main.py`.

---

MIT · Built by Juan ⚡️