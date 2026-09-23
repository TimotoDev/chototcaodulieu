provider "aws" {
  alias  = "bangkok"
  region = "ap-southeast-7"
}

resource "aws_sqs_queue" "staging_evidence_dlq" {
  name                      = "timoto-ai-staging-evidence-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "staging_evidence" {
  name                       = "timoto-ai-staging-evidence"
  visibility_timeout_seconds = 180
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.staging_evidence_dlq.arn, maxReceiveCount = 5 })
}

resource "aws_dynamodb_table" "staging_evidence" {
  name         = "timoto-ai-staging-evidence"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "review_partition"
    type = "S"
  }
  attribute {
    name = "review_due"
    type = "N"
  }
  global_secondary_index {
    name            = "review-due"
    hash_key        = "review_partition"
    range_key       = "review_due"
    projection_type = "KEYS_ONLY"
  }
  attribute {
    name = "pk"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_iam_role" "staging_intake" {
  for_each           = toset(["forward", "collect"])
  name               = "timoto-ai-staging-intake-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_cloudwatch_log_group" "staging_forward" {
  provider          = aws.bangkok
  name              = "/aws/lambda/timoto-ai-staging-forward"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "staging_collect" {
  name              = "/aws/lambda/timoto-ai-staging-collect"
  retention_in_days = 14
}

resource "aws_iam_role_policy" "staging_forward" {
  role = aws_iam_role.staging_intake["forward"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.staging_forward.arn}:*" },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.staging_evidence.arn }
  ] })
}

resource "aws_iam_role_policy" "staging_collect" {
  role = aws_iam_role.staging_intake["collect"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.staging_collect.arn}:*" },
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.staging_evidence.arn },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.staging_evidence.arn },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.artifacts.arn}/agent-evidence/staging/*" }
  ] })
}

resource "aws_lambda_function" "staging_forward" {
  provider         = aws.bangkok
  function_name    = "timoto-ai-staging-forward"
  role             = aws_iam_role.staging_intake["forward"].arn
  filename         = "${path.module}/staging-intake.zip"
  source_code_hash = filebase64sha256("${path.module}/staging-intake.zip")
  runtime          = "python3.12"
  handler          = "pipeline.forward"
  timeout          = 60
  memory_size      = 256
  environment {
    variables = { QUEUE_URL = aws_sqs_queue.staging_evidence.url, SOURCE_ACCOUNT = data.aws_caller_identity.current.account_id }
  }
  depends_on = [aws_iam_role_policy.staging_forward]
}

resource "aws_lambda_function" "staging_collect" {
  function_name    = "timoto-ai-staging-collect"
  role             = aws_iam_role.staging_intake["collect"].arn
  filename         = "${path.module}/staging-intake.zip"
  source_code_hash = filebase64sha256("${path.module}/staging-intake.zip")
  runtime          = "python3.12"
  handler          = "pipeline.collect"
  timeout          = 30
  memory_size      = 256
  environment {
    variables = { TABLE_NAME = aws_dynamodb_table.staging_evidence.name, BUCKET = aws_s3_bucket.artifacts.bucket }
  }
  depends_on = [aws_iam_role_policy.staging_collect]
}

resource "aws_lambda_permission" "staging_logs" {
  provider       = aws.bangkok
  statement_id   = "StagingCloudWatchLogs"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.staging_forward.function_name
  principal      = "logs.ap-southeast-7.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn     = "arn:aws:logs:ap-southeast-7:${data.aws_caller_identity.current.account_id}:log-group:/ecs/timoto-backend-fargate:*"
}

resource "aws_cloudwatch_log_subscription_filter" "staging_errors" {
  provider        = aws.bangkok
  name            = "timoto-ai-staging-errors"
  log_group_name  = "/ecs/timoto-backend-fargate"
  filter_pattern  = "{ ($.event = \"api_exception\") || (($.event = \"api_request\") && ($.status >= 500) && ($.status < 600)) }"
  destination_arn = aws_lambda_function.staging_forward.arn
  depends_on      = [aws_lambda_permission.staging_logs]
}

resource "aws_lambda_event_source_mapping" "staging_evidence" {
  event_source_arn        = aws_sqs_queue.staging_evidence.arn
  function_name           = aws_lambda_function.staging_collect.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}

resource "aws_cloudwatch_metric_alarm" "staging_evidence_dlq" {
  alarm_name          = "timoto-ai-staging-evidence-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.staging_evidence_dlq.name }
}
