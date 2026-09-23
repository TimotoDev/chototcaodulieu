# Isolated staging observability hotfix

Built from the exact staging revision 536 image, not the developer's dirty checkout.
Only `observability.py`, `exceptions.py`, `middleware.py` are overlaid.

Changes:
- Omit raw exception text (use classified error code).
- Store DRF error context on underlying HttpRequest for correlation.
- Implement Django process_exception; preserve expected 404/403/bad-request handling.

Base image digest: `sha256:ce6070f0e15743665387026fde24559de2af914c3126103067156e3878c79894`.
Patched digest: `sha256:dab629f3a81181f49cbb4778e0aea799cca89f7e7bfea7b2af32fcde598fe5eb`.
ECR tag: `obsfix-20260916-correlation-v1` in Bangkok `timoto-backend`.
New task definition: `timoto-backend:537`. Deployment disables migrations.
Verified rollout COMPLETED (1/1 running), root HTTP 200. An isolated Fargate
smoke using this deployed digest produced correlated 500 request/exception logs,
which reached the Singapore evidence pipeline and created one incident.
Runtime RELEASE_SHA remains the original base label; OBSERVABILITY_PATCH records
the overlay. Do NOT claim that the unchanged SHA fully describes this image.

Equivalent fixes are present in `/Users/toannguyen/timoto-mobile/backend/apps/bikes/`.
They must be included in the next normal backend build, or that build would regress.
There is no new git commit or push from this task.

To roll back, register a copy of revision 536 pinned to the BASE digest above,
set RUN_MIGRATIONS=false, then update only staging `timoto-backend` in cluster
`timoto-backend-fargate`. Do not rely on the mutable `latest` tag in revision 536.

The isolated container passes 10 tests (including actual Django/DRF request chains)
with network disabled and SQLite in-memory. No production service is changed.
