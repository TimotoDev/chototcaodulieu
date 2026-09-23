# n8n Cloud integration contract

n8n Cloud is the orchestration and approval plane. Slack is intentionally not
connected to n8n: the Slack agent runs Socket Mode directly in ECS/Fargate.

## Required connections

### 1. n8n Webhook from the ECS incident orchestrator

The ECS incident orchestrator consumes `incident-events` inside AWS and sends a
redacted incident envelope to an authenticated n8n Webhook. This avoids relying
on an n8n Cloud SQS consumer, which is not available in the current node set.

### 2. AWS SQS (outbound only)

Use one least-privilege AWS credential in n8n to publish to
`timoto-ai-dev-agent-events` for approval requests and commands.

The current n8n AWS SQS node exposes `sendMessage`; it is not the consumer for
`incident-events`. Queue consumption stays in ECS where retry, visibility
timeouts, DLQ handling, and idempotency are explicit.

Do not give n8n broad administrator access. CloudWatch read access can be added
later if the workflow needs direct metric lookup; the initial source of truth is
the EventBridge-to-SQS path already running in AWS.

### 3. Model provider

Use one provider for triage and repair planning. The default is n8n Gateway
credits for the first workflow if available; otherwise use an explicit
OpenAI/Anthropic credential. The prompt must receive the incident envelope and
evidence links, not raw secrets or unredacted transcripts.

### 4. Mintlify knowledge source (optional in n8n)

Connect the source of truth, not the Mintlify presentation layer:

- preferred: the GitHub repository/path that contains the Mintlify docs;
- fallback: the published docs URL through an authenticated/allowlisted HTTP
  fetcher.

The workflow may retrieve documentation for context, but it must not treat
generated docs as permission to modify infrastructure or merge code.

## Deliberately not connected to n8n

- Slack: handled by ECS `slack-agent` through outbound Socket Mode.
- Slack tokens: stored in AWS Secrets Manager and injected only into the ECS
  task.
- GitHub: handled by the ECS `repair-agent` using a GitHub App installed only on
  allowlisted repositories. n8n does not need the GitHub node.
- Private ECS HTTP endpoints: no public ingress; use SQS as the handoff.
- Production deploy credentials: held by the deployment system and exposed only
  after an approval record exists.

## Data flow

```text
CloudWatch / EventBridge -> incident-events SQS -> ECS incident-orchestrator
ECS incident-orchestrator -> authenticated n8n Webhook -> triage workflow
n8n -> agent-events SQS -> ECS slack-agent / repair-agent
ECS repair-agent -> GitHub App -> PR -> human approval -> deploy workflow
Mintlify GitHub source -> ECS knowledge-api (or n8n HTTP fetch) -> agent context
```

## Credentials still needed before live workflow creation

- an AWS IAM credential restricted to `SendMessage` on `agent-events`;
- an n8n Webhook authentication secret shared with the ECS incident orchestrator;
- the selected model provider credential or confirmation that Gateway credits
  may be used;
- the Mintlify source repository/path or published URL;
- the Slack App tokens for the ECS task (these do not go into n8n);
- a GitHub App installed on the selected repositories (these do not go into
  n8n).
