# AWS Deployment Plan

## 1. Prerequisites

- AWS account with permissions: ECS, ECR, Secrets Manager, CloudWatch, VPC, Bedrock
- Terraform installed locally (`brew install hashicorp/tap/terraform`)
- AWS CLI configured (`aws configure` or SSO)
- Bedrock model access enabled (see step 1b)

## 1b. Enable Bedrock Model Access

In the AWS Console: **Amazon Bedrock > Model access > Manage model access**

Request access to `anthropic.claude-3-haiku-20240307-v1:0` in your deployment region.
This is a one-time step per account/region.

## 2. Configure Secrets in AWS

Upload API keys to AWS Secrets Manager before deploying infrastructure:
- `DATADOG_API_KEY` / `DATADOG_APP_KEY`
- `JIRA_API_TOKEN`

**Note:** When using `llm_provider=bedrock` (default), no LLM API keys are needed.
Bedrock authenticates via the ECS task IAM role. Only set `OPENAI_API_KEY` if
using `llm_provider=openai`.

## 3. Configure Terraform Variables

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars
# Fill in with real values
```

Key LLM settings in `terraform.tfvars`:
```hcl
llm_provider     = "bedrock"                                  # or "openai"
bedrock_model_id = "anthropic.claude-3-haiku-20240307-v1:0"
openai_api_key_arn = ""                                       # only if llm_provider=openai
```

## 4. Deploy Infrastructure

```bash
terraform init
terraform plan -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars
```

## 5. Build and Push Docker Image

```bash
ECR_URL=$(terraform output -raw ecr_repository_url)
docker build -f Dockerfile.aws -t $ECR_URL:latest ../../
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_URL
docker push $ECR_URL:latest
```

## 6. Verify Deployment

- Run a manual ECS task to validate
- Check CloudWatch logs for correct execution
- Confirm EventBridge schedule triggers as expected

## 7. Post-Deploy

- Monitor first automated runs
- Set up Datadog dashboard (see `datadog-dashboard-plan.md`)
- Fine-tune per-team thresholds if needed

## Infrastructure Overview

All Terraform files live in `infra/aws/`:

| File | Purpose |
|---|---|
| `ecs.tf` | Fargate cluster + task definition (includes LLM env vars) |
| `ecr.tf` | Docker image registry |
| `eventbridge.tf` | Scheduled execution |
| `vpc.tf` | Private network |
| `iam.tf` | Roles and permissions (includes Bedrock policy) |
| `secrets.tf` | Secrets Manager |
| `efs.tf` | Persistent storage (cache/audit) |
| `elasticache.tf` | Redis (deduplication) |
| `cloudwatch.tf` | Logs and monitoring |
