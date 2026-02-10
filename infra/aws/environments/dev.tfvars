environment         = "dev"
schedule_expression = "cron(0 9 * * ? *)"  # Once daily at 09:00 UTC
task_cpu            = 512
task_memory         = 1024
redis_node_type     = "cache.t3.micro"
log_retention_days  = 3
agent_profile       = "development"
datadog_env         = "dev"
