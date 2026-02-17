# AWS Deployment — Dogcatcher Agent

ECS Fargate scheduled task triggered by EventBridge, managed with Terraform.

## Architecture

```
EventBridge (cron) → ECS Fargate Task (1 vCPU, 2GB)
                       ├─ AWS Bedrock (LLM via IAM role)
                       ├─ Secrets Manager (Datadog, Jira keys)
                       ├─ ElastiCache Redis (cache backend)
                       ├─ EFS (.agent_cache persistence)
                       └─ CloudWatch Logs (stdout/stderr)
```

## LLM Provider

The agent supports two LLM providers, controlled by `llm_provider` in tfvars:

| Provider | Auth | Secrets needed | Default |
|----------|------|---------------|---------|
| `bedrock` | IAM task role (automatic) | None | Yes |
| `openai` | API key via Secrets Manager | `openai_api_key_arn` | No |

**Bedrock (recommended for AWS):** No API keys needed. The ECS task role gets
`bedrock:InvokeModel` permission automatically when `llm_provider=bedrock`.

**OpenAI:** Set `llm_provider=openai` and provide `openai_api_key_arn` in tfvars.

## Prerequisites

- AWS CLI v2 configured with appropriate permissions
- Terraform >= 1.5
- Docker
- Bedrock model access enabled in your AWS account (for `bedrock` provider)
- Secrets created in AWS Secrets Manager (see `secrets.tf` for commands)

## Setup

### 1. Enable Bedrock model access

In the AWS Console, go to **Amazon Bedrock > Model access** and request access
to `anthropic.claude-3-haiku-20240307-v1:0` (or your chosen model) in your region.

### 2. Create secrets

```bash
# Required:
aws secretsmanager create-secret --name dogcatcher/prod/datadog-api-key --secret-string "..."
aws secretsmanager create-secret --name dogcatcher/prod/datadog-app-key --secret-string "..."
aws secretsmanager create-secret --name dogcatcher/prod/jira-api-token --secret-string "..."

# Optional (only if llm_provider=openai):
aws secretsmanager create-secret --name dogcatcher/prod/openai-api-key --secret-string "sk-..."

# Optional (for Patchy):
aws secretsmanager create-secret --name dogcatcher/prod/github-token --secret-string "ghp_..."
```

### 3. Configure Terraform

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your secret ARNs and config
```

### 4. Deploy infrastructure

```bash
terraform init
terraform plan -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars
```

### 5. Push container image

```bash
# Get ECR URL from terraform output
ECR_URL=$(terraform output -raw ecr_repository_url)

# Build and push
docker build -f ../../Dockerfile.aws -t $ECR_URL:latest ../../
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_URL
docker push $ECR_URL:latest
```

### 6. Test manually

```bash
aws ecs run-task \
  --cluster dogcatcher-prod \
  --task-definition dogcatcher-prod \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=DISABLED}"
```

## Cost Estimate (~$55/mo eu-west-1)

| Resource | $/mo |
|----------|------|
| NAT Gateway | ~$35 |
| ElastiCache cache.t3.micro | ~$13 |
| ECS Fargate (24 runs/day, ~5min) | ~$3 |
| Bedrock (Claude Haiku, ~1K calls/day) | ~$1 |
| Secrets + CloudWatch + ECR + EFS | ~$3 |

## CI/CD

The `.github/workflows/deploy.yml` workflow automatically builds and pushes
the Docker image to ECR on every push to `main`. Terraform apply is manual.

Required GitHub secrets:
- `AWS_DEPLOY_ROLE_ARN`: IAM role ARN for OIDC-based authentication
