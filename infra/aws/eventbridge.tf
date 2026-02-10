resource "aws_cloudwatch_event_rule" "agent_schedule" {
  name                = "${var.project_name}-${var.environment}-schedule"
  description         = "Trigger dogcatcher agent on schedule"
  schedule_expression = var.schedule_expression

  tags = { Name = "${var.project_name}-schedule" }
}

resource "aws_cloudwatch_event_target" "ecs_task" {
  rule     = aws_cloudwatch_event_rule.agent_schedule.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.agent.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = aws_subnet.private[*].id
      security_groups  = [aws_security_group.ecs_tasks.id]
      assign_public_ip = false
    }
  }
}
