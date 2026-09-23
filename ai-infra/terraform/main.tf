data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  azs       = slice(data.aws_availability_zones.available.names, 0, 2)
  nat_count = min(var.nat_gateway_count, length(local.azs))

  service_names = [
    "n8n-main",
    "n8n-worker",
    "slack-agent",
    "knowledge-api",
    "incident-orchestrator",
    "repair-agent",
    "tool-runner",
  ]

  queue_names = [
    "agent-events",
    "incident-events",
  ]
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project_name}-${var.environment}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-${var.environment}-igw"
  }
}

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.main.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-${var.environment}-public-${local.azs[count.index]}"
    Tier = "public"
  }
}

resource "aws_subnet" "private_app" {
  count = length(local.azs)

  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 2)

  tags = {
    Name = "${var.project_name}-${var.environment}-private-app-${local.azs[count.index]}"
    Tier = "application"
  }
}

resource "aws_subnet" "private_data" {
  count = length(local.azs)

  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 4)

  tags = {
    Name = "${var.project_name}-${var.environment}-private-data-${local.azs[count.index]}"
    Tier = "data"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  count = length(local.azs)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-${var.environment}-nat-eip-${count.index + 1}"
  }
}

resource "aws_nat_gateway" "main" {
  count = local.nat_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  depends_on = [aws_internet_gateway.main]

  tags = {
    Name = "${var.project_name}-${var.environment}-nat-${local.azs[count.index]}"
  }
}

resource "aws_route_table" "private_app" {
  count = length(local.azs)

  vpc_id = aws_vpc.main.id

  # Explicit empty routes removes the former NAT default route.
  route = []

  tags = {
    Name = "${var.project_name}-${var.environment}-private-app-rt-${local.azs[count.index]}"
  }
}

resource "aws_route_table" "private_data" {
  count = length(local.azs)

  vpc_id = aws_vpc.main.id

  # Retained private subnets are isolated; no paid NAT egress.
  route = []

  tags = {
    Name = "${var.project_name}-${var.environment}-private-data-rt-${local.azs[count.index]}"
  }
}

resource "aws_route_table_association" "private_app" {
  count = length(local.azs)

  subnet_id      = aws_subnet.private_app[count.index].id
  route_table_id = aws_route_table.private_app[count.index].id
}

resource "aws_route_table_association" "private_data" {
  count = length(local.azs)

  subnet_id      = aws_subnet.private_data[count.index].id
  route_table_id = aws_route_table.private_data[count.index].id
}

resource "aws_security_group" "ecs_tasks" {
  name        = "${var.project_name}-${var.environment}-ecs-tasks"
  description = "ECS task egress and service-to-service traffic for the AI platform"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Allow task egress through NAT or VPC endpoints"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecr_repository" "service" {
  for_each = toset(local.service_names)

  name                 = "${var.project_name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_cloudwatch_log_group" "service" {
  for_each = toset(local.service_names)

  name              = "/ecs/${var.project_name}/${var.environment}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_event_bus" "main" {
  name = "${var.project_name}-${var.environment}"
}

resource "aws_sqs_queue" "dlq" {
  for_each = toset(local.queue_names)

  name                      = "${var.project_name}-${var.environment}-${each.key}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "main" {
  for_each = toset(local.queue_names)

  name                       = "${var.project_name}-${var.environment}-${each.key}"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 5
  })
}

resource "aws_cloudwatch_event_rule" "ecs_task_state" {
  name        = "${var.project_name}-${var.environment}-ecs-task-state"
  description = "Route ECS task failures and stops into the incident queue."

  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.main.arn]
      lastStatus = ["STOPPED"]
    }
  })
}

resource "aws_cloudwatch_event_rule" "alarm_state" {
  name        = "${var.project_name}-${var.environment}-alarm-state"
  description = "Route CloudWatch alarms into the incident queue."

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      state = {
        value = ["ALARM"]
      }
    }
  })
}

resource "aws_cloudwatch_event_rule" "app_incidents" {
  name           = "${var.project_name}-${var.environment}-app-incidents"
  description    = "Route application incident and approval events into the incident queue."
  event_bus_name = aws_cloudwatch_event_bus.main.name

  event_pattern = jsonencode({
    source      = [{ prefix = "timoto.ai." }]
    detail-type = ["IncidentDetected", "AgentTaskFailed", "RepairApprovalRequested"]
  })
}

resource "aws_cloudwatch_event_target" "ecs_task_state" {
  rule      = aws_cloudwatch_event_rule.ecs_task_state.name
  target_id = "incident-queue"
  arn       = aws_sqs_queue.main["incident-events"].arn
}

resource "aws_cloudwatch_event_target" "alarm_state" {
  rule      = aws_cloudwatch_event_rule.alarm_state.name
  target_id = "incident-queue"
  arn       = aws_sqs_queue.main["incident-events"].arn
}

resource "aws_cloudwatch_event_target" "app_incidents" {
  event_bus_name = aws_cloudwatch_event_bus.main.name
  rule           = aws_cloudwatch_event_rule.app_incidents.name
  target_id      = "incident-queue"
  arn            = aws_sqs_queue.main["incident-events"].arn
}

resource "aws_sqs_queue_policy" "incident_events" {
  queue_url = aws_sqs_queue.main["incident-events"].url

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowEventBridgeToSendIncidents"
      Effect = "Allow"
      Principal = {
        Service = "events.amazonaws.com"
      }
      Action   = "sqs:SendMessage"
      Resource = aws_sqs_queue.main["incident-events"].arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = [
            aws_cloudwatch_event_rule.ecs_task_state.arn,
            aws_cloudwatch_event_rule.alarm_state.arn,
            aws_cloudwatch_event_rule.app_incidents.arn,
          ]
        }
      }
    }]
  })
}

resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  for_each = aws_sqs_queue.dlq

  alarm_name          = "${var.project_name}-${var.environment}-${each.key}-dlq-not-empty"
  alarm_description   = "A dead-letter queue contains messages that require triage."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  for_each = var.api_gateway_http_apis

  alarm_name          = "${var.project_name}-${var.environment}-api-${each.key}-5xx"
  alarm_description   = "An API Gateway HTTP API returned one or more 5xx responses in the last minute."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = each.value.api_id, Stage = each.value.stage }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = var.lambda_functions_to_monitor

  alarm_name          = "${var.project_name}-${var.environment}-lambda-${each.key}-errors"
  alarm_description   = "The Lambda function returned one or more errors in the last minute."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.key }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = var.lambda_functions_to_monitor

  alarm_name          = "${var.project_name}-${var.environment}-lambda-${each.key}-throttles"
  alarm_description   = "The Lambda function was throttled in the last minute."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = each.key }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_ecs_task_definition" "incident_orchestrator" {
  skip_destroy = true
  lifecycle {
    create_before_destroy = true
  }
  family                   = "${var.project_name}-${var.environment}-incident-orchestrator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "incident-orchestrator"
    image     = "${aws_ecr_repository.service["incident-orchestrator"].repository_url}:${var.incident_orchestrator_image_tag}"
    essential = true

    environment = [
      {
        name  = "INCIDENT_QUEUE_URL"
        value = aws_sqs_queue.main["incident-events"].url
      },
      {
        name  = "ARTIFACTS_BUCKET"
        value = aws_s3_bucket.artifacts.bucket
      },
      {
        name  = "AWS_REGION"
        value = var.aws_region
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.service["incident-orchestrator"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "incident_orchestrator" {
  name            = "${var.project_name}-${var.environment}-incident-orchestrator"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.incident_orchestrator.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }
}

resource "random_id" "artifacts" {
  byte_length = 4
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.project_name}-${var.environment}-artifacts-${random_id.artifacts.hex}"
  force_destroy = var.force_destroy_artifacts_bucket
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name = "${var.project_name}-${var.environment}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "read-platform-secrets"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.platform.arn
    }]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-${var.environment}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task_baseline" {
  name = "platform-baseline"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:AbortMultipartUpload"
        ]
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ChangeMessageVisibility",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ReceiveMessage",
          "sqs:SendMessage"
        ]
        Resource = concat([for queue in aws_sqs_queue.main : queue.arn], [for queue in aws_sqs_queue.dlq : queue.arn])
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task_observability_readonly" {
  name = "observability-readonly"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "cloudwatch:DescribeAlarms",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "ecs:DescribeServices",
        "ecs:DescribeTasks",
        "ecs:ListTasks",
        "apigateway:GET",
        "apigatewayv2:GET",
        "lambda:GetFunctionConfiguration",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "logs:FilterLogEvents",
        "logs:GetLogEvents",
        "logs:GetQueryResults",
        "logs:StartQuery"
      ]
      Resource = "*"
    }]
  })
}

resource "aws_secretsmanager_secret" "platform" {
  name                    = "${var.project_name}/${var.environment}/runtime"
  description             = "Runtime secret slots for the Timoto AI platform; values are populated out of band."
  recovery_window_in_days = 7
}
