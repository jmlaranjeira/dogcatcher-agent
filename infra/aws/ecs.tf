resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  container_name = "${var.project_name}-agent"

  # Build secrets list, excluding empty optional ARNs
  secrets = compact([
    var.openai_api_key_arn != "" ? jsonencode({ name = "OPENAI_API_KEY", valueFrom = var.openai_api_key_arn }) : "",
    var.datadog_api_key_arn != "" ? jsonencode({ name = "DATADOG_API_KEY", valueFrom = var.datadog_api_key_arn }) : "",
    var.datadog_app_key_arn != "" ? jsonencode({ name = "DATADOG_APP_KEY", valueFrom = var.datadog_app_key_arn }) : "",
    var.jira_api_token_arn != "" ? jsonencode({ name = "JIRA_API_TOKEN", valueFrom = var.jira_api_token_arn }) : "",
    var.github_token_arn != "" ? jsonencode({ name = "GITHUB_TOKEN", valueFrom = var.github_token_arn }) : "",
  ])
}

resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.project_name}-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = local.container_name
      image     = "${aws_ecr_repository.app.repository_url}:latest"
      essential = true

      command = ["python", "main.py", "--profile", var.agent_profile]

      secrets = [for s in local.secrets : jsondecode(s)]

      environment = [
        { name = "LLM_PROVIDER", value = var.llm_provider },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "JIRA_DOMAIN", value = var.jira_domain },
        { name = "JIRA_USER", value = var.jira_user },
        { name = "JIRA_PROJECT_KEY", value = var.jira_project_key },
        { name = "DATADOG_SERVICE", value = var.datadog_service },
        { name = "DATADOG_ENV", value = var.datadog_env },
        { name = "DATADOG_SITE", value = var.datadog_site },
        { name = "CACHE_BACKEND", value = "redis" },
        { name = "REDIS_URL", value = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379" },
        { name = "AGENT_CACHE_DIR", value = "/app/.agent_cache" },
      ]

      mountPoints = [
        {
          sourceVolume  = "agent-cache"
          containerPath = "/app/.agent_cache"
          readOnly      = false
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  volume {
    name = "agent-cache"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.cache.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.cache.id
        iam             = "DISABLED"
      }
    }
  }
}
