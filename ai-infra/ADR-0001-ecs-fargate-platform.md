# ADR-0001: ECS + Fargate as the AI platform runtime

**Status:** Accepted  
**Date:** 2026-09-15  
**Deciders:** Timoto platform owner

## Context

The former OpenClaw runtime was a single EC2 instance. The new platform needs a Slack agent, n8n workflows, a knowledge service, an incident orchestrator, and a guarded coding/repair agent. The platform must remain AWS-native, isolate workloads, support per-service IAM, and avoid adopting Kubernetes operational overhead before it is justified. n8n Cloud is already available at `timoto.app.n8n.cloud`.

## Decision

Use Amazon ECS with AWS Fargate in a dedicated VPC. Use n8n Cloud as the workflow/control plane, not as the privileged runtime for every tool. Persist only agent state that needs durable retention in managed data services and run repair/build jobs in isolated tasks or CodeBuild. Do not use EKS for this platform.

## Options considered

### EC2-only

| Dimension | Assessment |
|---|---|
| Complexity | Low initially, high during scaling and patching |
| Cost | Predictable but pays for idle capacity |
| Isolation | Host/process boundaries require careful hardening |
| Fit | Rejected for the production agent platform |

### ECS + Fargate

| Dimension | Assessment |
|---|---|
| Complexity | Low to medium |
| Cost | Pay per task resources; NAT and managed data services are additional |
| Isolation | Per-task boundary with task-scoped IAM |
| Fit | Accepted |

### EKS

| Dimension | Assessment |
|---|---|
| Complexity | High; Kubernetes APIs, add-ons, upgrades, and cluster operations |
| Cost | Control-plane and compute costs plus operational overhead |
| Portability | Highest |
| Fit | Deferred until there is a demonstrated Kubernetes-specific need |

## Consequences

- Service deployment, autoscaling, IAM, logging, and networking use AWS-native ECS concepts.
- We must define task definitions and image pipelines for each service.
- Kubernetes portability is not a first-class goal.
- Database, Redis, secrets, and artifacts remain outside task ephemeral storage.
- Production-changing tools require approval and a separate role.

## Action items

1. [x] Destroy the OpenClaw EC2 Terraform stack.
2. [x] Apply the ECS/Fargate foundation in `ai-infra/terraform`.
3. [x] Select n8n Cloud as the control plane; keep the reserved n8n ECR repositories unused.
4. [ ] Provision encrypted PostgreSQL and Redis only for agent state that requires durable retention.
5. [ ] Build and push real service images.
6. [ ] Configure Slack Socket Mode, Mintlify source ingestion, GitHub App, and approval policies.
7. [ ] Enable guarded PR, staging deploy, canary, and rollback flows.
