# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the **Dogcatcher Agent**, an automated LangGraph-powered agent that:
- Fetches error logs from Datadog
- Analyzes them with LLM (OpenAI gpt-4.1-nano)
- Performs intelligent duplicate detection against Jira
- Creates Jira tickets with configurable per-run caps
- Includes a self-healing PR bot (Patchy)

## Key Commands

### Development & Testing
```bash
# Run tests
python run_tests.py                    # All tests
python run_tests.py unit              # Unit tests only
python run_tests.py config            # Configuration tests
python run_tests.py ticket            # Ticket creation tests

# Run the agent
python main.py --dry-run              # Safe simulation mode
python main.py --real                 # Real mode (creates tickets)
python main.py --dry-run --env dev --service myservice --hours 24

# Run with configuration profiles (recommended)
python main.py --profile development  # Dev environment (safe, no tickets)
python main.py --profile staging      # Staging environment
python main.py --profile production   # Production (Redis cache, auto-create)
python main.py --profile testing      # Test environment

# Multi-tenant mode (requires config/teams.yaml)
python main.py --profile production                  # Loops all teams
python main.py --profile production --team team-vega  # Single team only
python main.py --dry-run --team team-vega             # Dry-run one team

# LangGraph Studio (for debugging workflows)
source .venv-studio/bin/activate
langgraph-studio start --host 127.0.0.1 --port 8123
# In another terminal:
langgraph dev --studio-url http://127.0.0.1:8123

# Generate audit reports
python tools/report.py --since-hours 24

# Run Patchy (self-healing PR bot)
python -m patchy.patchy_graph --service myservice --error-type npe --loghash 4c452e2d1c49

# Run Sleuth (error investigator)
python -m sleuth "user without role after registration"
python -m sleuth "timeout errors in payment service" --service payment-api
python -m sleuth "database connection errors" --hours 48 --env prod
python -m sleuth "authentication failures" --no-patchy
python -m sleuth "NullPointerException in UserService" --invoke-patchy
```

### Code Quality
```bash
# Format code (project uses black)
black .

# Type checking (project uses mypy)
mypy .

# Linting
flake8 .
```

### Docker
```bash
# Build and run (local)
docker build -t dd-jira-agent:latest .
docker compose up --build

# Build AWS image
docker build -f Dockerfile.aws -t dogcatcher-agent:latest .
```

### AWS Deployment
```bash
# Deploy infrastructure (one-time setup)
cd infra/aws
cp terraform.tfvars.example terraform.tfvars  # fill in values
terraform init
terraform plan -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars

# Push image to ECR
ECR_URL=$(terraform output -raw ecr_repository_url)
docker build -f ../../Dockerfile.aws -t $ECR_URL:latest ../../
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_URL
docker push $ECR_URL:latest

# Manual test run
aws ecs run-task --cluster dogcatcher-prod --task-definition dogcatcher-prod --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=DISABLED}"
```

See `infra/aws/README.md` for full setup guide and cost breakdown.

## Architecture Overview

### Core Components

1. **LangGraph Pipeline** (`agent/graph.py`)
   - Stateful multi-step processing: fetch_logs → analyze_log → create_ticket → next_log
   - Handles duplicate detection and error recovery
   - Built on LangGraph with customizable state management

2. **Configuration System** (`agent/config.py`)
   - Pydantic-based with validation and type safety
   - Environment-based configuration with `.env` support
   - Validates API keys, ranges, and configuration consistency

3. **Duplicate Detection** (`agent/jira/match.py`)
   - Multi-strategy approach: fingerprint cache, direct log matching, similarity scoring
   - Normalizes logs to handle emails, UUIDs, timestamps for robust matching
   - Performance-optimized with TTL caching

4. **Performance System** (`agent/performance.py`)
   - Intelligent caching with configurable TTLs
   - Performance metrics and optimization recommendations
   - Dynamic parameter tuning based on usage patterns

### Key Modules

- **`agent/datadog.py`**: Datadog API client with pagination and filtering
- **`agent/jira/`**: Complete Jira integration (client, matching, utilities)
- **`agent/nodes/`**: LangGraph processing nodes (analysis, ticket creation, audit)
- **`patchy/`**: Self-healing PR bot for automated fixes
- **`sleuth/`**: Error investigator with natural language queries
- **`tools/report.py`**: Audit trail analysis and reporting

### Data Flow

```
Datadog Logs → Log Fetching → LLM Analysis → Duplicate Detection
                                              ↓
                                          Ticket Exists?
                                          ↓        ↓
                                    Create Ticket  Comment
                                          ↓        ↓
                                      Jira Ticket ←
```

## Configuration

The project uses environment variables loaded from `.env`:

### Required Settings
- `OPENAI_API_KEY`: OpenAI API access
- `DATADOG_API_KEY` & `DATADOG_APP_KEY`: Datadog API access
- `JIRA_DOMAIN`, `JIRA_USER`, `JIRA_API_TOKEN`: Jira integration

### Key Behavior Controls
- `AUTO_CREATE_TICKET`: `true` for real mode, `false` for simulation
- `MAX_TICKETS_PER_RUN`: Per-run ticket creation cap (default: 3)
- `COMMENT_ON_DUPLICATE`: Auto-comment on duplicate tickets
- `JIRA_SIMILARITY_THRESHOLD`: Duplicate detection sensitivity (default: 0.82)

### Configuration Profiles (Recommended)

The project supports YAML-based configuration profiles for easy environment management:

**Available Profiles:**
- `development` - Safe defaults for local dev (no ticket creation, file cache, DEBUG logging)
- `staging` - Pre-production settings (limited tickets, file cache, INFO logging)
- `production` - Full capabilities (Redis cache, auto-create tickets, WARNING logging)
- `testing` - Automated test environment (minimal limits, memory cache)

**Profile Locations:** `config/profiles/*.yaml`

**Configuration Precedence:** `.env` → Profile YAML → Environment Variables → CLI Arguments

**Usage Examples:**
```bash
# Development (safe, no tickets created)
python main.py --profile development --service myservice

# Staging with custom filters
python main.py --profile staging --env staging --hours 12

# Production mode
python main.py --profile production
```

### Multi-Tenant Configuration

When `config/teams.yaml` exists, the agent runs in **multi-tenant mode**: it loops over teams sequentially, each with its own Jira project, Datadog services, cache directory, and audit log.

**Setup:** Copy `config/teams.yaml.example` to `config/teams.yaml` and customize.

**Key files:**
- `agent/team_config.py` - Pydantic models (`TeamConfig`, `TeamsConfig`)
- `agent/team_loader.py` - YAML loader with module-level cache
- `config/teams.yaml.example` - Example with 4 teams

**Behavior:**
- Without `teams.yaml`: single-tenant mode (100% backward compatible)
- With `teams.yaml`: loops all teams and their services, overriding config per iteration
- `--team <team-id>`: process only one specific team
- Cache isolation: `.agent_cache/teams/{team_id}/` per team
- Audit logs include `team_id` and `team_service` fields
- Jira team custom field injected from `TeamsConfig.jira_team_field_id` + `TeamConfig.jira_team_field_value`

## Development Guidelines

### Testing Strategy
- **Unit tests**: Core business logic (`tests/unit/`)
- **Configuration tests**: Pydantic validation
- **Performance tests**: Caching and optimization
- Use pytest markers: `@pytest.mark.unit`, `@pytest.mark.config`, etc.

### Code Patterns
- **Error handling**: Structured logging with `agent.utils.logger`
- **Configuration**: Always use Pydantic models from `agent.config`
- **API calls**: Use provided client wrappers with retry logic
- **Performance**: Leverage caching system for expensive operations

### Common Tasks

#### Adding New Error Types
1. Update severity rules in `agent/config.py`
2. Add aggregation logic in `agent/nodes/ticket.py`
3. Add tests in `tests/unit/test_ticket_creation.py`

#### Tuning Duplicate Detection
Adjust in `.env`:
- `JIRA_SIMILARITY_THRESHOLD`: Higher = fewer duplicates detected
- `JIRA_SEARCH_WINDOW_DAYS`: Reduce for high-volume projects
- `JIRA_SEARCH_MAX_RESULTS`: Limit for performance

#### Performance Optimization
The system provides automatic recommendations. Monitor logs for:
- "Performance configuration"
- "Similarity cache statistics"
- "Performance metrics summary"

## Important Notes

### Safety Features
- **Dry-run mode**: Default behavior for safe testing
- **Per-run caps**: Configurable limits on ticket creation
- **Duplicate prevention**: Multi-level deduplication (fingerprints, similarity)
- **Audit logging**: Complete trail in `.agent_cache/audit_logs.jsonl` (or `.agent_cache/teams/{team_id}/audit_logs.jsonl` in multi-tenant mode)

### LangGraph Integration
- Graph definition in `agent/graph.py`
- State management via `agent/state.py`
- Node implementations in `agent/nodes/`
- Studio integration for visual debugging

### Sleuth Agent (Error Investigator)
Sleuth is an interactive CLI tool for investigating errors through natural language:

```bash
python -m sleuth "describe the error you want to investigate"
```

**Features:**
- Natural language queries for error investigation
- LLM-powered Datadog query generation
- Correlation with existing Jira tickets
- Root cause analysis and fix suggestions
- Optional Patchy integration for automatic fixes

**Workflow:**
1. `parse_query`: Extract entities from natural language
2. `build_dd_query`: LLM generates optimal Datadog query
3. `search_logs`: Execute Datadog search
4. `correlate_jira`: Find related Jira tickets
5. `analyze_results`: LLM analyzes and summarizes findings
6. `suggest_action`: Offer Patchy fix if applicable

**CLI Options:**
- `--service`: Filter by service name
- `--env`: Filter by environment (default: from config)
- `--hours`: Time window in hours (default: 24)
- `--no-patchy`: Disable automatic fix suggestions
- `--invoke-patchy`: Auto-invoke Patchy if fix is suggested
- `--json`: Output raw JSON instead of formatted text

### Performance Considerations
- Similarity cache reduces API calls by 50-80%
- Configurable search windows and thresholds
- Intelligent parameter optimization based on usage
- Performance metrics and recommendations

When working on this codebase, always test in dry-run mode first and monitor the audit logs for insights into agent behavior.