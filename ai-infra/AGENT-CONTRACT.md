# Timoto agent contracts

This file is the boundary between n8n Cloud and the ECS/Fargate runtime. It is
deliberately provider-neutral so the Slack and GitHub credentials can be added
without changing the event model.

## Runtime boundaries

- n8n Cloud owns incident triggers, routing, human approval, and workflow audit history.
- ECS/Fargate owns the Slack Socket Mode process, Slack thread context, knowledge API, incident
  orchestrator, isolated repair runner, and read-only observability adapters.
- The Slack agent talks outbound to Slack over Socket Mode. It does not require
  an n8n Slack node, public webhook, inbound port, or public dashboard.
- ECS tasks are private-subnet workloads. They consume SQS and call approved
  external APIs through NAT; no task receives a public IP.
- `incident-events` is the durable handoff from AWS monitoring to triage.
- `agent-events` is the durable handoff for asynchronous agent work.

## Incident event envelope

Every producer should send one JSON object to `incident-events`:

```json
{
  "schemaVersion": "1",
  "eventId": "uuid",
  "source": "aws.ecs|aws.cloudwatch|grafana|manual",
  "occurredAt": "2026-01-01T00:00:00Z",
  "severity": "sev1|sev2|sev3|info",
  "dedupeKey": "stable-key",
  "summary": "short human-readable summary",
  "evidence": {
    "rawEventS3Uri": "s3://..."
  },
  "owner": {
    "team": "team-name",
    "requestedBy": "user-or-system"
  },
  "requestedAction": "observe|triage|propose_fix|apply_fix"
}
```

The raw event belongs in the encrypted/versioned artifacts bucket. Messages
must not contain tokens, credentials, or full chat transcripts.

## Repair state machine

`detected -> triaged -> evidence_collected -> fix_proposed -> review_pending ->
approved -> test_passed -> pr_opened -> merge_pending -> deployed -> verified`

Any failed or rejected step transitions to `blocked` with an explicit reason.
Merge, production deploy, IAM, network, and data changes require an approval
record. A repair run must be idempotent on `dedupeKey` and retain its patch,
test output, reviewer, and rollback reference.

## Current implementation status

- AWS foundation, API/Lambda alarms, monitoring-to-SQS path, and the evidence-first incident-orchestrator worker are live in `ap-southeast-1`.
- n8n Cloud is the selected control plane, but it currently has no Slack,
  GitHub, AWS/SQS, or model-provider credentials.
- The incident orchestrator is intentionally read-only: it archives redacted
  evidence and does not change code or infrastructure.
- Slack and repair-agent images/services are not deployed yet. Do not publish a
  repair workflow until GitHub App permissions, a target repository, and an
  approval path exist.
