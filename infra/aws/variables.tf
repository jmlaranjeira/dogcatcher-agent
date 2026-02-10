variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "eu-west-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "dogcatcher"
}

# --- Networking ---

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "azs" {
  description = "Availability zones"
  type        = list(string)
  default     = ["eu-west-1a", "eu-west-1b"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (NAT gateway)"
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24"]
}

# --- ECS ---

variable "task_cpu" {
  description = "CPU units for the ECS task (1024 = 1 vCPU)"
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Memory in MiB for the ECS task"
  type        = number
  default     = 2048
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for the agent"
  type        = string
  default     = "cron(0 * * * ? *)"
}

variable "agent_profile" {
  description = "Configuration profile passed to --profile flag"
  type        = string
  default     = "production"
}

# --- ElastiCache ---

variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.t3.micro"
}

# --- Secrets (ARNs provided after manual creation) ---

variable "openai_api_key_arn" {
  description = "Secrets Manager ARN for OPENAI_API_KEY"
  type        = string
}

variable "datadog_api_key_arn" {
  description = "Secrets Manager ARN for DATADOG_API_KEY"
  type        = string
}

variable "datadog_app_key_arn" {
  description = "Secrets Manager ARN for DATADOG_APP_KEY"
  type        = string
}

variable "jira_api_token_arn" {
  description = "Secrets Manager ARN for JIRA_API_TOKEN"
  type        = string
}

variable "github_token_arn" {
  description = "Secrets Manager ARN for GITHUB_TOKEN (optional, for Patchy)"
  type        = string
  default     = ""
}

# --- Non-secret config ---

variable "jira_domain" {
  description = "Jira domain (e.g. mycompany.atlassian.net)"
  type        = string
}

variable "jira_user" {
  description = "Jira user email"
  type        = string
}

variable "jira_project_key" {
  description = "Default Jira project key"
  type        = string
}

variable "datadog_service" {
  description = "Default Datadog service filter"
  type        = string
  default     = ""
}

variable "datadog_env" {
  description = "Default Datadog environment filter"
  type        = string
  default     = "prod"
}

variable "datadog_site" {
  description = "Datadog site (e.g. datadoghq.eu)"
  type        = string
  default     = "datadoghq.eu"
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 7
}
