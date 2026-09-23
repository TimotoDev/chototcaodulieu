output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_app_subnet_ids" {
  value = aws_subnet.private_app[*].id
}

output "private_data_subnet_ids" {
  value = aws_subnet.private_data[*].id
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_task_security_group_id" {
  value = aws_security_group.ecs_tasks.id
}

output "ecr_repository_urls" {
  value = { for name, repo in aws_ecr_repository.service : name => repo.repository_url }
}

output "log_group_names" {
  value = { for name, group in aws_cloudwatch_log_group.service : name => group.name }
}

output "event_bus_name" {
  value = aws_cloudwatch_event_bus.main.name
}

output "incident_event_rules" {
  value = {
    ecs_task_state = aws_cloudwatch_event_rule.ecs_task_state.name
    alarm_state    = aws_cloudwatch_event_rule.alarm_state.name
    app_incidents  = aws_cloudwatch_event_rule.app_incidents.name
  }
}

output "dlq_alarm_names" {
  value = { for name, alarm in aws_cloudwatch_metric_alarm.dlq_messages : name => alarm.alarm_name }
}

output "api_alarm_names" {
  value = { for name, alarm in aws_cloudwatch_metric_alarm.api_5xx : name => alarm.alarm_name }
}

output "lambda_alarm_names" {
  value = {
    errors    = { for name, alarm in aws_cloudwatch_metric_alarm.lambda_errors : name => alarm.alarm_name }
    throttles = { for name, alarm in aws_cloudwatch_metric_alarm.lambda_throttles : name => alarm.alarm_name }
  }
}

output "incident_orchestrator_service_name" {
  value = aws_ecs_service.incident_orchestrator.name
}

output "incident_orchestrator_task_definition" {
  value = aws_ecs_task_definition.incident_orchestrator.arn
}

output "queue_urls" {
  value = { for name, queue in aws_sqs_queue.main : name => queue.url }
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "platform_secret_arn" {
  value = aws_secretsmanager_secret.platform.arn
}

output "ecs_execution_role_arn" {
  value = aws_iam_role.ecs_execution.arn
}

output "ecs_task_role_arn" {
  value = aws_iam_role.ecs_task.arn
}
