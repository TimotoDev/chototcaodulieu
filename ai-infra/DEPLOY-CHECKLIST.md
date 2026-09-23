# AI Infra deployment checklist

## Foundation

- [x] OpenClaw EC2 stack destroyed by its Terraform state.
- [x] Dedicated VPC and two-AZ subnet layout applied.
- [x] ECS Fargate cluster created.
- [x] ECR repositories created with immutable tags and scan-on-push.
- [x] CloudWatch log groups created.
- [x] EventBridge bus and SQS queues with DLQs created.
- [x] ECS task-stop and CloudWatch alarm events routed to the incident queue.
- [x] API Gateway 5xx alarms created for the existing HTTP APIs.
- [x] Lambda error and throttle alarms created for the existing functions.
- [x] Evidence-first incident-orchestrator worker deployed on Fargate.
- [x] Worker archives redacted incident events to the versioned S3 artifact bucket before deleting SQS messages.
- [x] Encrypted/versioned S3 artifact bucket created.
- [x] ECS execution and task roles created.
- [x] Empty Secrets Manager runtime slot created.

## n8n Cloud control plane

- [x] Use `https://timoto.app.n8n.cloud` as the workflow control plane.
- [ ] Create an authenticated n8n incident Webhook and add outbound AWS/SQS plus model-provider credentials.
- [ ] Keep GitHub credentials in the ECS repair-agent task, not n8n.
- [ ] Create the Slack app with Socket Mode; store its app-level and bot tokens in the ECS runtime secret, not n8n.
- [ ] Configure the ECS Slack agent's channel allowlist and approval users.
- [ ] Create the incident workflow only after the SQS credential and approval destination exist.
- [ ] Keep the reserved `n8n-main`/`n8n-worker` ECR repositories unused unless the team intentionally exits n8n Cloud.

## Before durable agent state

- [ ] Provision Aurora PostgreSQL with backups, encryption, and a private subnet group.
- [ ] Provision ElastiCache Redis in private subnets.
- [ ] Define retention, owner scope, deletion, and restore tests before storing conversations or repair evidence.

## Slack agent runtime

- [ ] Deploy a read-only agent first.
- [ ] Verify thread context, idempotency, rate limits, and redaction.

## Before repair agent

- [ ] Install a GitHub App only on explicitly allowlisted repositories.
- [ ] Start with read-only observability and repository permissions.
- [ ] Build an isolated tool runner with no production credentials.
- [ ] Run tests/security scans before creating a PR.
- [ ] Enforce branch protection and required checks.
- [ ] Require human approval for merge, production deploy, IAM, network, and data changes.

## Rollback triggers

- Any secret or token appears in logs or artifacts.
- Agent can reach production write APIs without an approval record.
- ECS service fails health checks after deployment.
- Error rate or latency exceeds the service baseline.
- Repair PR cannot reproduce the incident or tests are incomplete.
