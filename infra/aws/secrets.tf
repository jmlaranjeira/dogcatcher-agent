# Secrets are created manually in AWS Secrets Manager before terraform apply.
# This file documents the expected secrets and validates their ARNs via variables.
#
# Required secrets (create via AWS Console or CLI):
#   aws secretsmanager create-secret --name dogcatcher/prod/datadog-api-key --secret-string "..."
#   aws secretsmanager create-secret --name dogcatcher/prod/datadog-app-key --secret-string "..."
#   aws secretsmanager create-secret --name dogcatcher/prod/jira-api-token --secret-string "..."
#
# Optional secrets:
#   aws secretsmanager create-secret --name dogcatcher/prod/openai-api-key --secret-string "sk-..."  # only if llm_provider=openai
#   aws secretsmanager create-secret --name dogcatcher/prod/github-token --secret-string "ghp_..."   # only for Patchy
#
# Note: When using llm_provider=bedrock, no LLM-related secrets are needed.
# Bedrock authenticates via the ECS task IAM role automatically.
#
# Then pass the ARNs to terraform via tfvars:
#   datadog_api_key_arn = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:dogcatcher/prod/datadog-api-key-AbCdEf"
#   ...
