# SSM Parameter Store for non-secret configuration.
# These are optional overrides — the agent reads them from environment variables
# injected by the ECS task definition. SSM is used here for auditability and
# easy updates without redeploying.

resource "aws_ssm_parameter" "jira_domain" {
  name  = "/${var.project_name}/${var.environment}/jira-domain"
  type  = "String"
  value = var.jira_domain
}

resource "aws_ssm_parameter" "jira_user" {
  name  = "/${var.project_name}/${var.environment}/jira-user"
  type  = "String"
  value = var.jira_user
}

resource "aws_ssm_parameter" "jira_project_key" {
  name  = "/${var.project_name}/${var.environment}/jira-project-key"
  type  = "String"
  value = var.jira_project_key
}

resource "aws_ssm_parameter" "datadog_site" {
  name  = "/${var.project_name}/${var.environment}/datadog-site"
  type  = "String"
  value = var.datadog_site
}
