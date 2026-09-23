# Timoto AI Infra

Production foundation for the Slack agent, knowledge/RAG service, n8n workflows, and guarded coding/SRE agent.

## Decision

- Compute: Amazon ECS with AWS Fargate.
- No EKS in this platform.
- Region: `ap-southeast-1`.
- Network: dedicated VPC with public subnets for controlled ingress/NAT and private subnets for ECS tasks and data services.
- Workflow control plane: n8n Cloud at `https://timoto.app.n8n.cloud`; ECS/Fargate remains the runtime boundary for agents and tools. Slack is handled directly by the ECS Socket Mode agent, not by an n8n Slack node.
- Event backbone: EventBridge plus SQS queues and dead-letter queues. ECS task stops and CloudWatch alarms are routed to `incident-events`.
- Artifacts: encrypted, versioned S3 bucket.
- Container registry: one ECR repository per runtime service.
- Credentials: AWS Secrets Manager and IAM task roles; no credentials are committed to this repository.

## What the foundation creates

`terraform/` creates the low-level platform only:

- dedicated VPC, public/private application/private data subnets, routing, and a configurable NAT gateway count;
- ECS cluster configured for Fargate;
- ECR repositories for the planned services;
- CloudWatch log groups;
- EventBridge custom bus and default-bus rules for ECS task stops and CloudWatch alarms;
- CloudWatch 5xx alarms for the three existing API Gateway HTTP APIs and error/throttle alarms for the seven existing Lambda functions;
- SQS queues and dead-letter queues for agent and incident events;
- encrypted/versioned S3 artifact bucket;
- ECS execution/task IAM roles with least-privilege baseline permissions;
- empty Secrets Manager slots for runtime credentials.
- an evidence-first `incident-orchestrator` worker running on ECS/Fargate; it consumes the incident queue and archives redacted events to S3 before acknowledging them.

It intentionally does not deploy a fake Slack bot, coding agent, or self-hosted n8n instance. n8n Cloud is already the control plane; the remaining services need real container images and out-of-band secret values.

## Apply

```bash
cd /Users/toannguyen/chototcaodulieu/ai-infra/terraform
cp terraform.tfvars.example terraform.tfvars
AWS_PROFILE=harry terraform init
AWS_PROFILE=harry terraform fmt -check
AWS_PROFILE=harry terraform validate
AWS_PROFILE=harry terraform plan -out=tfplan
AWS_PROFILE=harry terraform apply tfplan
```

For a first environment, `nat_gateway_count = 1` keeps cost lower. Use `2` for production Availability Zone redundancy after the workload is ready.

## Service rollout after foundation

1. Add the n8n Cloud credentials for the authenticated incident Webhook, outbound AWS/SQS, and the selected model provider; Slack and GitHub credentials belong in ECS task secrets, not n8n.
2. Build and push the Slack Socket Mode agent, Knowledge API, repair agent, and tool-runner images to ECR; the incident orchestrator evidence collector is already deployed.
3. Provision Aurora PostgreSQL and ElastiCache Redis only when the first service needs durable shared state; n8n Cloud's own state stays managed by n8n.
4. Add the read-only observability tools and wire them to the incident queue.
5. Add the isolated repair runner and Step Functions workflow.
6. Enable PR creation first; require explicit approval before merge, production deploy, IAM, network, or data changes.

The former `openclaw/` Terraform directory is retained as historical audit material only. Its AWS resources were destroyed before this foundation was started.

See [N8N-INTEGRATIONS.md](N8N-INTEGRATIONS.md) for the exact n8n connection
contract and credential boundaries.
