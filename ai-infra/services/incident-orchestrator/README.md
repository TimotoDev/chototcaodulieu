# Incident orchestrator + staging reviewer

NETWORK UPDATE (2026-09-17 UTC): the worker uses the existing public subnets,
assignPublicIp enabled, and its existing security group has zero inbound rules.
NAT count defaults to zero; private app/data route tables explicitly have no
internet default routes. Those unused private subnets remain isolated.
Before NAT removal, the new task successfully consumed a synthetic SQS message
and wrote `incidents/2026/09/17/network-public-smoke-20260916.json` to S3.
Restoring private-subnet egress would require recreating NAT plus default routes;
it is not a free rollback and must not be done without authorization. The old
Elastic IP is released, so its original address is not guaranteed recoverable.

CURRENT CONFIGURATION: Nova / automatic model review DISABLED at the user's
request. REVIEW_TABLE and REVIEW_MODEL are removed from the ECS environment,
and Bedrock invocation permission is removed from the task role. Evidence intake
and the archive consumer remain enabled. Historical reports are retained.
The reviewer description below documents the previous implementation, not an
enabled capability. Do not re-enable or substitute another paid model without
the user's authorization. Existing AWS infrastructure still incurs charges.

The existing ARM64 Fargate service now also polls the staging incident review
index every 60 seconds (up to 10 due groups per poll). No additional always-on
service is needed. The original SQS archive consumer remains active.

Flow: Bangkok structured errors → staging intake → deduplicated DynamoDB incident
and S3 evidence → Singapore Fargate reviewer → Bedrock → S3 review report.

## What the reviewer does

- Groups remain service/environment/release/fingerprint/fixed 15-minute window.
- Reads the latest sanitized exception sample and unique exception count.
- Calls `apac.amazon.nova-micro-v1:0` using task IAM via Singapore's Bedrock endpoint.
  The APAC inference profile may process sanitized evidence in Singapore, Sydney,
  Tokyo, Seoul, Osaka or Mumbai. No arbitrary exception text/headers/bodies are sent.
- Stores observed facts, summary, hypotheses, confidence and next read-only checks.
- The summary is deterministic from evidence, not LLM prose. Overall confidence
  is low until further evidence exists; model confidence is separately labelled.
  Hypotheses always carry `hypotheses_verified: false`.
- Marks impact unknown and severity UNASSESSED; model confidence is not calibrated.
- Stores reports at `agent-reviews/staging/<incident-id>/<event-count>.json`.
- Updates incident `review_status`, `reviewed_count`, `report_key`, `last_review_at`.
- Smoke reports carry `synthetic: true`. Release/source binding remains unverified.

No source-code checkout, live diagnostic tools, GitHub PR, Slack notification,
merge, deployment or automatic remediation is implemented in this reviewer.
It is a bounded model review pipeline, not an autonomous repair agent.
Existing generic alarms still use the archive-only consumer; 5xx summaries
without an exception do not yet create reviewable incidents.

## Controls and failure behavior

- Staging/service/prefix checks, strict output schema, no model tools or execution.
- DynamoDB 180-second lease prevents concurrent processing of the same incident.
- No model invocation if event count has not changed. Five-minute review cooldown.
- Maximum 3 attempts per incident group, including malformed responses/timeouts.
- Atomic daily UTC budget: 50 invocation attempts, max output 900 tokens/call,
  input evidence capped at 12,000 characters. SDK model retries disabled.
- Budget exhaustion defers; failures retry after 5 minutes; evidence is retained.
- Reports are advisory/untrusted; humans must validate hypotheses before action.
- This is not exactly-once model invocation: a crash after a call can consume a
  second attempt; the budget and per-incident cap bound this exposure.
- Reviewer errors log only exception class and incident ID, not payloads.
- Old incidents without review-index fields are not automatically backfilled.
- Existing IAM baseline is shared and broader than this reviewer needs (artifact
  writes and observability reads); this change adds only staging DynamoDB access
  and invocation of one specific model. A dedicated role is a future hardening step.

## Operations

Required extra environment: `REVIEW_TABLE`, `REVIEW_MODEL`.
Without `REVIEW_TABLE`, only the original archiver runs.

Inspect a report (read-only, operator credentials):

```sh
python3 read_review.py <incident-id>
```

Pause model review by deploying the original immutable image tag
`incident-orchestrator-20260915` via the Terraform
`incident_orchestrator_image_tag` variable; ingestion and evidence stay intact.
Rolling deployment may have two tasks temporarily; leases and shared budgets
apply across both. Do not delete the DynamoDB table or S3 evidence for rollback.

Tests: `python3 -m unittest -v test_reviewer.py` (11 tests).

Deployment checklist: local and built-container tests passed; Terraform plan
reviewed; image pushed with immutable tag. No Git commit/push, PR review or CI
run performed for this change. No production deployment is included.
Rollback triggers: repeated reviewer errors, unexpected model call volume,
invalid/advisory output handling, or failure of the existing archive consumer.
Use the original image tag above to disable review, then verify service steady
state and continued evidence intake. Never interpret task RUNNING alone as
successful review; require a report object and DynamoDB REVIEWED status.

## Verification — 2026-09-16

- Image tag `incident-reviewer-20260916-v2`, digest
  `sha256:ddfbe7cf159d9984d6f99690048605e2555597b5ac61170f6e354c0ef86166b1`.
- Task definition `timoto-ai-dev-incident-orchestrator:4`.
- 11 reviewer tests passed inside the built image; 6 intake tests passed locally.
- Isolated live-backend-image smoke task `b9c1877e60134f7a9988d8a91f816d2a`
  emitted correlated request/exception records; this is not a public HTTP failure.
- Incident `dcbad0698cabb246d9dd6f722b86d802a6a6855406ec7760542cc07c27fbffd7`
  was reviewed by Fargate with Nova. Report version `v2-factual-summary`, 573
  total tokens in that verification call, factual summary + unverified hypotheses.
- Final report path:
  `s3://timoto-ai-dev-artifacts-1d316272/agent-reviews/staging/dcbad0698cabb246d9dd6f722b86d802a6a6855406ec7760542cc07c27fbffd7/1.json`.
- Replayed both source events twice: incident count and reviewed count remained
  1; status REVIEWED, DLQ empty, smoke task STOPPED with exit code 0.
- Initial Anthropic run was blocked by account use-case onboarding; switched to
  Nova without submitting account declarations. Failed attempts remain counted.
- Initial Nova output invented causal wording in the summary; v2 replaces that
  headline with deterministic evidence. Prior S3 versions remain for audit.
- Terraform validate passed; post-apply plan without refresh showed no changes.
  That plan is not a fresh AWS drift audit. Source changes are not committed/pushed.

## Original archive behavior

This worker consumes the encrypted `incident-events` SQS queue, redacts obvious
secret fields, archives evidence in the versioned S3 artifacts bucket, and only
deletes a message after the archive succeeds. It is intentionally read-only;
repair, GitHub changes, and deploys are separate approval-gated stages.

Required environment variables:

- `INCIDENT_QUEUE_URL`
- `ARTIFACTS_BUCKET`
- `AWS_REGION` (defaults to `ap-southeast-1`)
