# 🧠 Datadog → LLM → Jira Agent (LangGraph)

Automated agent that reads **Datadog error logs**, analyzes them with an **LLM** (LangChain + OpenAI), performs **smart de‑duplication** against Jira, and **creates at most one ticket per run** with rich context.

---

## 🔄 End‑to‑end Flow
```mermaid
graph TD
    A[Fetch Datadog Logs] --> B[Analyze Log (LLM)]
    B -->|create ticket| C[Check Similar Issues in Jira]
    B -->|no ticket| G[Next Log]
    C -->|duplicate| D[Comment on Existing Ticket]
    C -->|unique| E[Create Jira Ticket]
    D --> G
    E --> G
```

---

## 🚀 Key Features
- **Datadog ingestion** (service/env/time window) with `logger`, `thread`, `timestamp`, `detail`.
- **LLM analysis (gpt‑4o‑mini)** → `error_type`, `severity (low|medium|high)`, `ticket_title`, `ticket_description` (markdown: *Problem summary*, *Possible Causes*, *Suggested Actions*).
- **One‑ticket guard**: creates **at most one** real Jira ticket per execution.
- **Idempotence**
  - In‑run: skip duplicated logs by `logger|thread|message`.
  - Cross‑run: fingerprint (`sha1`) cached in `.agent_cache/processed_logs.json`.
- **Advanced Jira duplicate detection**
  - Search last 180d (summary + description, `labels = datadog-log`).
  - Normalization + similarity (RapidFuzz if available, fallback to difflib) + boosts (error_type, logger).
  - If duplicate, optionally **auto‑comment** with new log context.
- **Ticket formatting**
  - Summary: **`[Datadog][<error_type>] <title>`**
  - Labels: **`datadog-log`**
  - Priority from severity: `low→Low`, `medium→Medium`, `high→High`.

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

## ⚙️ Configuration (.env)
```ini
# OpenAI
OPENAI_API_KEY=sk-...

# Datadog
DATADOG_API_KEY=...
DATADOG_APP_KEY=...
DATADOG_SITE=datadoghq.eu  # or datadoghq.com

# Jira
JIRA_DOMAIN=your-domain.atlassian.net
JIRA_USER=you@company.com
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=DPRO

# Agent behavior
AUTO_CREATE_TICKET=false   # true/1/yes => real creation
PERSIST_SIM_FP=false       # persist fingerprints in simulation
COMMENT_ON_DUPLICATE=true  # comment on matched issue
```

---

## ▶️ Run
```bash
python main.py
```
- **Simulation** (`AUTO_CREATE_TICKET=false`): analyzes logs and simulates **1** ticket.
- **Real** (`AUTO_CREATE_TICKET=true`): creates **1** real ticket max per run; on duplicates it comments and does not create.

---

## 🧪 Duplicate Matching — How it works
1. JQL over last 180d and `statusCategory != Done` (summary + description + `labels = datadog-log`).
2. Normalize text (`prePersist ≈ pre-persist ≈ pre persist`, lowercase, remove punctuation).
3. Score: `0.6*title_sim + 0.3*desc_sim` + boosts (`+0.10` error_type, `+0.05` logger, `+0.05` token overlap). Threshold **0.82**.
4. If duplicate: skip creation and (if enabled) add a comment with timestamp, logger, thread, and original message.

> Note: If the **fingerprint** (`sha1(logger|thread|message)`) already exists from previous runs, the log is skipped before querying Jira.

---

## 📦 Project Structure
```
langgraph-agent-demo/
├── main.py                # Entrypoint
├── .env                   # Secrets & config
├── agent/
│   ├── datadog.py         # Fetch & parse logs
│   ├── graph.py           # LangGraph wiring
│   ├── jira.py            # Jira API + matching + commenting
│   └── nodes.py           # LLM analysis + ticket creation + guards
└── requirements.txt
```

---

## 🛠️ Troubleshooting
- No ticket in real mode → may be fingerprint or duplicate (score printed in console).
- `response_format` warning → mitigated using `model_kwargs`.
- 401/403 Jira → check domain/user/token and project permissions.

---

MIT · Built by Juan ⚡️