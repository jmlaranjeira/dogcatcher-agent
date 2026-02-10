# AWS Deployment — Dogcatcher Agent

ECS Fargate scheduled task triggered by EventBridge, managed with Terraform.

## Architecture

```
EventBridge (cron) → ECS Fargate Task (1 vCPU, 2GB)
                       ├─ Secrets Manager (API keys)
                       ├─ ElastiCache Redis (cache backend)
                       ├─ EFS (.agent_cache persistence)
                       └─ CloudWatch Logs (stdout/stderr)
```

## Prerequisites

- AWS CLI v2 configured with appropriate permissions
- Terraform >= 1.5
- Docker
- Secrets created in AWS Secrets Manager (see `secrets.tf` for commands)

## Setup

### 1. Create secrets

```bash
aws secretsmanager create-secret --name dogcatcher/prod/openai-api-key --secret-string "sk-..."
aws secretsmanager create-secret --name dogcatcher/prod/datadog-api-key --secret-string "..."
aws secretsmanager create-secret --name dogcatcher/prod/datadog-app-key --secret-string "..."
aws secretsmanager create-secret --name dogcatcher/prod/jira-api-token --secret-string "..."
# Optional:
aws secretsmanager create-secret --name dogcatcher/prod/github-token --secret-string "ghp_..."
```

### 2. Configure Terraform

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your secret ARNs and config
```

### 3. Deploy infrastructure

```bash
terraform init
terraform plan -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars
```

### 4. Push container image

```bash
# Get ECR URL from terraform output
ECR_URL=$(terraform output -raw ecr_repository_url)

# Build and push
docker build -f ../../Dockerfile.aws -t $ECR_URL:latest ../../
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_URL
docker push $ECR_URL:latest
```

### 5. Test manually

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
| Secrets + CloudWatch + ECR + EFS | ~$4 |

## CI/CD

The `.github/workflows/deploy.yml` workflow automatically builds and pushes
the Docker image to ECR on every push to `main`. Terraform apply is manual.

Required GitHub secrets:
- `AWS_DEPLOY_ROLE_ARN`: IAM role ARN for OIDC-based authentication
