# Bedrock Migration Plan

## Goal

Replace OpenAI with AWS Bedrock (Claude Haiku) as the LLM provider. This eliminates the external OpenAI dependency and keeps everything within AWS.

## Current State

| Location | Current Usage | Library |
|---|---|---|
| `agent/nodes/analysis.py` | Sync log analysis | LangChain `ChatOpenAI` |
| `agent/nodes/analysis_async.py` | Async log analysis | LangChain `ChatOpenAI` |
| `sleuth/utils/query_builder.py` | Datadog query generation | Direct `openai` SDK |
| `agent/healthcheck.py` | Connection verification | Direct `openai` SDK |

Model: `gpt-4.1-nano` | Config: `agent/config.py` (`OpenAIConfig`)

## Target State

| Location | New Usage | Library |
|---|---|---|
| `agent/nodes/analysis.py` | Sync log analysis | LangChain `ChatBedrockConverse` |
| `agent/nodes/analysis_async.py` | Async log analysis | LangChain `ChatBedrockConverse` |
| `sleuth/utils/query_builder.py` | Datadog query generation | `boto3` bedrock-runtime |
| `agent/healthcheck.py` | Connection verification | `boto3` bedrock-runtime |

Model: `anthropic.claude-3-haiku-*` (verify exact ID in Bedrock console)

## Dependencies

### Add
- `langchain-aws` (provides `ChatBedrockConverse`)
- `boto3` (AWS SDK, likely already available)

### Remove (eventually)
- `langchain-openai`
- `openai`

## Steps

### 1. Verify Bedrock model access
- Check which Claude models are enabled in `dds-team-vega-dev` account
- Confirm the exact model ID for Claude Haiku

### 2. Update configuration (`agent/config.py`)
- Rename/replace `OpenAIConfig` with `BedrockConfig` (or a generic `LLMConfig`)
- New settings: `AWS_REGION`, `BEDROCK_MODEL_ID`
- Remove: `OPENAI_API_KEY`
- Keep: `temperature`, `response_format` (adapt to Bedrock format)

### 3. Migrate analysis nodes
- `agent/nodes/analysis.py`: Replace `ChatOpenAI` with `ChatBedrockConverse`
- `agent/nodes/analysis_async.py`: Same change, verify async support
- Prompt templates remain unchanged
- Circuit breaker wrappers remain unchanged

### 4. Migrate Sleuth query builder
- `sleuth/utils/query_builder.py`: Replace `OpenAI()` client with `boto3` bedrock-runtime `invoke_model()`
- Adapt request/response format to Bedrock's Converse API

### 5. Migrate healthcheck
- `agent/healthcheck.py`: Replace OpenAI ping with Bedrock `list_foundation_models()` or a small `invoke_model()` call

### 6. Update requirements
- Add `langchain-aws`, `boto3` to `requirements.txt`
- Remove `langchain-openai`, `openai`

### 7. Authentication
- **Local development**: AWS CLI credentials (`aws configure` or SSO session)
- **ECS Fargate**: IAM task role with `bedrock:InvokeModel` permission (no API keys needed)

### 8. Update tests
- Mock `ChatBedrockConverse` instead of `ChatOpenAI`
- Mock `boto3` bedrock-runtime calls in Sleuth and healthcheck tests

## Notes

- The Bedrock proxy at `bedrock.shared-architecture.dds-cloud.ninja` is experimental and not intended for general use. We use Bedrock directly instead.
- Consider keeping OpenAI as a fallback provider (configurable via env var) for flexibility.
- JSON response format: Bedrock Converse API supports tool use which can enforce structured output. Alternatively, keep the current prompt-based JSON enforcement.
