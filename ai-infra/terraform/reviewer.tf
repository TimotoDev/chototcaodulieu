resource "aws_iam_role_policy" "incident_review" {
  name = "staging-incident-review"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["dynamodb:Query"], Resource = "${aws_dynamodb_table.staging_evidence.arn}/index/review-due" },
    { Effect = "Allow", Action = ["dynamodb:UpdateItem"], Resource = aws_dynamodb_table.staging_evidence.arn }
  ] })
}
