resource "aws_cloudwatch_log_group" "agent" {
  name              = "/ecs/${var.project_name}-${var.environment}"
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-logs" }
}

# Alarm on task failures (exit code != 0)
resource "aws_cloudwatch_metric_alarm" "task_failures" {
  alarm_name          = "${var.project_name}-${var.environment}-task-failures"
  alarm_description   = "Dogcatcher agent task failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "RunningTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = 300
  statistic           = "Average"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
  }

  tags = { Name = "${var.project_name}-failure-alarm" }
}
