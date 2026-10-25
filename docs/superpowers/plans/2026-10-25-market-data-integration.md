# Unified motorcycle market storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Chợ Tốt and public Facebook motorcycle listings in shared AWS storage and show source-aware listings and run status on the live dashboard.

**Architecture:** Independent collectors normalize to an idempotent, source-prefixed DynamoDB record. A private public-only browser worker supplies Facebook; existing Python collection supplies Chợ Tốt. Public dashboard reads sanitized fields and explicit source status.

**Tech Stack:** Python/boto3, Node.js/Playwright/AWS SDK, DynamoDB, Lambda HTTP API, S3/CloudFront.

**Spec:** `docs/superpowers/specs/2026-10-25-market-data-integration-design.md` (approved by user 2026-10-25).

## Global Constraints

- Existing raw Chợ Tốt data is retained. No Facebook sessions, environment files, databases, raw bodies or private collector code in the public repository.
- Facebook defaults to public-only. Stop at login walls/access denial/rate limiting; report saved counts independently from scraped counts.
- Unknown prices/times remain null. Facebook public titles are generated from identified bike fields.
- No synthetic records in production or live acceptance evidence. No automatic valuation updates.

## Review Focus

- Repeated/updating observations must preserve immutable first-seen order and distinct source IDs.
- DynamoDB unprocessed writes and partial mirror failures must remain recoverable and visible.
- Filtered or multi-source pagination must not skip unreturned records.
- Blocked browser access must produce an explicit failed-access run, never successful empty data.
- Scraped text, malformed URLs, private identity fields and stale aggregates must not mislead public users.

### Task 1: Shared contract and storage

**Files:** create `market_data.py`, `market_store.py`, `tests/test_market_data.py`, `tests/test_market_store.py`.
**Interfaces:** `normalize_chotot(record, observed_at)` -> sanitized dict; `MarketStore.upsert(listing)` -> durable write; `MarketStore.save_run(run)` -> persisted run evidence.
- [ ] Write failing tests for source collisions, unknown price, malformed URL, immutable first-seen, failed storage and run status.
- [ ] Implement normalized records, first-seen preservation, bounded writes, source/time index and safe status data.
- [ ] Run `python3 -m unittest discover -s tests` and commit.

### Task 2: Public Facebook collector and AWS persistence (private repo)

**Files:** create `server/src/public/collector.js`, `normalize.js`, `store.js`, `run.js` and meaningful Node tests; update private README/package manifest only.
**Interfaces:** collector returns `{status, items, reason}`; normalizer produces the shared contract; writer reads AWS SDK credential chain and writes listings/runs.
- [ ] Write failing tests for login-wall classification, URL/identity sanitization, missing price and partial-storage failures.
- [ ] Implement a bounded fresh anonymous browser with no imported sessions or evasion; preserve normal browser behavior.
- [ ] Run existing Node tests plus new tests; execute a small real anonymous pilot and retain safe output/evidence outside Git.
- [ ] Commit only explicit source/docs/manifest files, excluding all preexisting runtime artifacts.

### Task 3: Chợ Tốt mirror, backfill and unified API

**Files:** update `chotot.py`, `lambda_handler.py`, `refresh_handler.py`, `api_handler.py`; create `tools/market_backfill.py`, `tools/market_snapshot.py`, API/ingestion tests.
**Interfaces:** source-aware listing API retains legacy response fields; bounded queries use per-source consumed positions; snapshot/status endpoints expose freshness/completeness.
- [ ] Add failing regression tests for partial DynamoDB writes, cross-source pagination and filtering.
- [ ] Add opt-in normalized mirrors; preserve failed IDs/raw input for replay; resumable backfill and complete snapshot commands.
- [ ] Add unified API behind environment setting; validate input/cursors and keep legacy mode available.
- [ ] Run Python tests and commit.

### Task 4: Unified dashboard and scoped deployment

**Files:** update `dashboard/index.html`; create scoped `tools/deploy_market.py`, deployment/runbook docs and verification scripts.
**Interfaces:** sanitized listing API, snapshot stats and run statuses from Task 3.
- [ ] Verify source controls and hostile rendering with executable browser/UI checks.
- [ ] Add source badges/selector, count/freshness/completeness and collection health; remove direct scraped HTML interpolation.
- [ ] Prepare resource-specific storage/IAM/code configuration diff and private rollback artifacts. Provision only required resources under approved integration scope.
- [ ] Run real Chợ Tốt and public Facebook collection independently, ingest and read back saved IDs; backfill legacy listings with resumable checkpoints and publish complete snapshots.
- [ ] Get fresh whole-branch review, resolve important findings, create/attach linked PRs, merge passing reviewed heads, deploy merged artifacts and verify live dashboard/API.
- [ ] Record merged/deployed/collected/blocked outcomes separately. A Facebook access wall is an unresolved real-data limitation, not end-to-end success.

## Execution ledger

User explicitly requested implementation, merging and running real data today. Execute natively without another plan-approval round. If Facebook anonymous access is blocked, finish all independently useful integration work and report the concrete access requirement. Paid vendors and recurring browser infrastructure await actual pilot evidence.
