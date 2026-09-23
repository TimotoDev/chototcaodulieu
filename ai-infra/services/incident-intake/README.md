# Staging evidence intake

Source: `/ecs/timoto-backend-fargate`, Bangkok, account 404850807717.
CloudWatch subscription -> Bangkok Lambda `timoto-ai-staging-forward` -> Singapore
SQS `timoto-ai-staging-evidence` -> Lambda `timoto-ai-staging-collect` -> S3 and
DynamoDB `timoto-ai-staging-evidence`. Production is not subscribed.

This collects evidence; it does not invoke an LLM, create PRs, or deploy repairs.
The existing incident-orchestrator and its queue remain independent.

## Guarantees and limitations

- Only schema v1 staging events for `timoto-backend` are forwarded.
- Raw exception messages, bodies, headers and arbitrary extra fields are omitted.
- Source CloudWatch event IDs deduplicate retries using a DynamoDB transaction.
- An exception fingerprint + release + fixed 15-minute window identifies an
  incident. Different windows intentionally create different incident records.
- `event_count` counts unique exception log events, not all affected requests.
- Request 5xx summaries are archived as correlation evidence; without an exception
  they currently do not create an incident. Alarm intake/fallback is future work.
- S3 writes happen before the transaction; a concurrent retry can create another
  object version but cannot increment the incident twice. No work is acknowledged
  before both durable writes succeed. Evidence is retained in the existing bucket;
  DynamoDB event/incident TTL is 30 days and queues retain failures for 14 days.
- Collector reports partial SQS failures; after five deliveries the message reaches
  the DLQ. The DLQ has a CloudWatch alarm. Forwarder retries rely on CloudWatch Logs
  subscription delivery behavior; do not treat this as an unlimited retention path.
- Incoming fingerprints/text are untrusted data. No log content is executed.
- Runtime release SHA is not proof of exact build source. The current staging image
  was built from a dirty checkout; `release_binding` remains explicitly unverified.
- No S3 object body or exception message is printed by collector error handling.

## Build and verification

From this directory:

```sh
python3 -m unittest -v test_pipeline
python3 -m zipfile -c ../../terraform/staging-intake.zip pipeline.py
cd ../../terraform
AWS_PROFILE=harry terraform plan -out=staging-intake.tfplan
AWS_PROFILE=harry terraform apply staging-intake.tfplan
```

`smoke_live_image.py` launches a one-off Fargate task pinned to the running image
digest with NO task role, secrets, migrations, or real DB. It runs a Django test
client request through the real image's DRF/middleware code and emits one controlled
500 into the staging log group. This can contribute to existing staging alarms.
It does not add a public failure endpoint and is not an external HTTP end-to-end test.

`verify_smoke.py TASK_ID --replay` reads only the smoke stream and its matching
evidence, replays identical source event IDs twice through the forwarder. Run again
without `--replay` after processing to confirm one incident with event_count=1.

## Verified 2026-09-16

- Terraform: 15 additions, no changes/deletions to existing resources.
- Local intake tests: 5 passed.
- Original staging image smoke task: `23f203790035402aa5312295d637203b`, exit 0.
- Original image returned 500 and stack frames, but correlation FAILED. This
  reproduced the DRF wrapper bug, rather than assuming the original report proved it.
- Two source log events arrived in S3; one exception incident was created.
- Replaying both events twice left event_count=1, evidence_events=2.
- Incident: `057a8d079d9c3d33db404abab2a354b496ad74021be1d0db66e93a67d7d5cd30`.
- Backend source fixes and the isolated hotfix image passed 10 Django tests using
  test-only settings. Full local project settings are blocked by an existing
  pydantic/pydantic-core version mismatch; the test container has matching runtime deps.
- Patched staging revision 537 reached COMPLETED, running=desired=1; root HTTP 200.
- Patched image smoke task: `fb574674a5064e48a2f2a2b61d834e8e`; status 500,
  correlated=true, stack_present=true, response_request_id_matches=true.
- Patched image incident: `15794e70ebc1da1020d247860f59155fd5a15c8c66fac89dca06072a003ce04a`.
- Live metric filter tests matched only 400 for 4xx and 500/503 for 5xx;
  200 was excluded. Staging evidence DLQ was empty at verification.

## Rollback

To pause intake without losing queued evidence, remove only the named staging
subscription filter through a reviewed Terraform change. Retain queues, table and
S3 for diagnosis. Do not destroy the entire AI foundation.
