# Chợ Tốt + Facebook motorcycle data integration

Status: proposed design; user selected independent collectors with shared data/dashboard. Awaiting approval of the detailed design. No implementation or AWS changes yet.

## Intent and success

Run Chợ Tốt and Facebook collection independently and show motorcycle listings in the existing live dashboard, with source filters and truthful collection status. Retain original source IDs, original URLs and observation times. A Facebook failure must not stop Chợ Tốt or appear as a successful empty dataset.

The user authorized PR creation/updates, merging and live data checks. No matching existing integration PR was found; implementation will need new PRs. End-to-end Facebook success requires actual publicly accessible listings, not synthetic examples or the mere existence of a browser session.

## Existing system and boundaries

- `TimotoDev/chototcaodulieu` is public. Python Lambda collectors write raw Chợ Tốt data into `chotot-xe-may`; its numeric `list_id` key cannot safely represent identifiers from different platforms. S3/CloudFront host the public dashboard, and a Lambda HTTP API reads the table.
- `TimotoDev/fb-scraping-data` is private. It contains an Express/Playwright collector, React UI and SQLite data. Its Marketplace scraper can start without a stored session; its group scraper currently refuses to start without one. Its basic scheduler exists despite older documentation saying otherwise.
- Keep Facebook implementation in the private repository. Do not copy its committed environment, browser storage state, SQLite files or dependencies into the public repository or deployment artifacts. Do not reuse committed Facebook sessions.
- This integration concerns observed asking prices and listings. It does not change TiMoto valuation anchors or push prices into the valuation engine.

## Approaches

1. **Chosen by user: independent collectors, shared data/dashboard.** Preserve each platform's runtime, normalize observations and isolate failures. Requires a shared contract and ingestion changes in both repositories.
2. Dashboard federation over both existing APIs. Smaller initial storage change, but creates inconsistent pagination, filters and stats and depends on a separately exposed Facebook server. Not selected.

Builder decisions below are proposals, not additional user decisions.

## Facebook access without login

Default proposal: a `public-only` mode, with no Facebook account, cookies or saved session. Read only content delivered to an anonymous browser through ordinary navigation. Stop on login requirements, checkpoint, access denial or rate limiting; do not remove access barriers, use private endpoints or add detection-evasion measures.

Public group visibility does not guarantee anonymous feed access or complete pagination. Private groups are unavailable in this mode. Anonymous Marketplace access must be measured in the actual runtime; it is not promised by this design. Existing group scraper's session requirement needs an explicit public-only path, rather than falsely reporting authenticated access.

The user has not supplied target group URLs. Marketplace pilot targets motorcycle search in Hanoi and Ho Chi Minh City with a small bounded item/page/time budget; require actual returned location evidence before labeling a listing with the requested city. Group collection stays disabled until public target URLs are configured.

Persist a run result even when blocked: source, mode, run ID, start/end, status, observed/saved/rejected counts, safe error code and last successful run. Zero records plus a login wall is `login_required`, not `success`. Do not retry blocked pages indefinitely. Ordinary network failures get bounded backoff.

## Shared storage and contract

Add a dedicated DynamoDB table, proposed name `motorbike-market-listings`, with string key `listing_id = source + ':' + external_id`. Supported sources are `chotot`, `facebook_marketplace` and `facebook_group`. Upserts are idempotent within a source; identical numeric IDs from different sources remain different records.

Contract fields: schema version, listing ID, source, external ID, source URL, sanitized title, price in VND, brand/model when identified, manufacture/registration year with provenance, odometer when identified, coarse region, source-posted time when available, first/last observation time and parser version. Unknown fields stay null. Observation time must not be presented as posting time. Missing/ambiguous prices are not converted to zero.

No Facebook author names, phone numbers, browser state or raw post bodies are published into shared/public dashboard data. Facebook display titles are generated from canonical brand/model/year, with a neutral `Motorcycle listing` fallback; no free-form source titles enter the public table or its exports. Raw Facebook persistence is off by default. Existing public Chợ Tốt bucket must never store browser sessions or private Facebook raw content. Display escaped text and validate URL schemes/domains; never interpolate scraped HTML into the DOM.

Use a source/time index ordered by immutable `first_seen_at + listing_id` to query newest discovered records without scanning the entire dataset for every HTTP request. Upserts preserve first-seen time; last-seen time is a separate displayed field. All-sources pagination merges source results and encodes per-source continuation positions without dropping unreturned records. Apply bounded read budgets for filters; cursors include filter context and are rejected if malformed or reused with different filters. Return whether further scan work remains even if a bounded page has no matches. This is an eventually consistent live feed, not a transactional snapshot; the dashboard deduplicates IDs, and a refresh discovers late index propagation. Static-dataset pagination must have no gaps or duplicates.

Store run metadata and per-source status separately from listing records. Compute dashboard aggregates in an explicit background snapshot with timestamp, coverage and completeness metadata; do not present a first-page sample as a whole-dataset total. Incomplete backfill remains visibly incomplete.

## Ingestion

Chợ Tốt: retain existing raw storage/export. Add normalized writes behind an environment setting in daily and refresh paths. Retry DynamoDB `UnprocessedItems` with bounded backoff and surface exhausted writes. Raw success plus normalized failure is a partial ingestion failure: retain failed IDs/checkpoints for reconciliation from saved raw records, without requiring another scrape or discarding raw successes. Backfill existing listings through a resumable, rate-limited command; retain checkpoints and do not delete the source table. Replays must preserve first observation time and not invent original posting times.

Facebook: keep collection and SQLite UI private; add a normalized exporter/writer using the AWS SDK credential chain. Write shared data only after validation. Use local developer credentials for the supervised pilot; any later scheduled worker receives a role limited to the listings/run tables. AWS credentials never enter browser code. AWS write failures make the run unsuccessful; do not report scraped count as saved count.

Collectors run in separate processes/services. The current Chợ Tốt AWS schedule continues independently. First Facebook proof runs locally without login. If that proof succeeds, schedule options and additional runtime costs can be finalized from measured duration/memory and access reliability; do not provision a permanently running browser service before that result.

## API and dashboard

Extend the existing API contract with `source=all|chotot|facebook_marketplace|facebook_group`, stable source-aware cursors and collection-status data. Keep legacy API behavior available behind an explicit cutover setting until backfill and candidate checks pass.

Dashboard: rename for multi-source motorcycle data, add a source selector/badges, newest observation time, source counts, backfill/aggregate freshness and a status panel. Clearly distinguish `never_run`, `running`, `success`, `partial`, `login_required`, `blocked` and `failed`. Keep the read API public only for sanitized listing metadata. Do not expose scraper controls or the private Facebook server publicly.

## Rollout and rollback

1. Create integration branches and two linked PRs in the relevant repositories after design approval. Reference the shared contract and keep private implementation references appropriate to repository visibility.
2. Test schema validation, source ID collisions, idempotent writes, partial-write retry, pagination continuity, hostile listing text, blocked Facebook access and accurate source status. Run existing relevant tests/builds.
3. Execute a bounded real-data Chợ Tốt read and anonymous Facebook collection pilot concurrently, producing validated local output before AWS provisioning. Record actual IDs, timestamps and sanitized counts; no demo data in live evidence. If Facebook is blocked, report that result and do not claim successful two-source ingestion. Local access success does not establish unattended access from AWS.
4. Prepare and review a resource-scoped AWS plan for the table/index/run metadata and least-privilege role changes, provision those resources, then execute and read back the shared ingestion pilot. Capture existing Lambda code/config and dashboard object for rollback. Do not run the broad legacy deployment script that also rewrites IAM and bucket policy.
5. Merge only reviewable, passing changes under the user's existing merge authorization. Deploy the reviewed artifact revisions, backfill with checkpoints, validate API/filter/pagination/status and then cut over the dashboard. Report separately what merged, deployed and collected real data.
6. Roll back API/dashboard settings and artifacts if validation fails. Preserve the original table and exports; no destructive migration. A failed anonymous Facebook pilot leaves its source disabled/blocked with an explanation, not fabricated data.

## Decisions still requiring evidence

- Whether anonymous Facebook returns usable Vietnamese motorcycle listings in the actual runtime. This is an empirical blocker for claiming Facebook data collection works without login.
- Public group target URLs if group collection is required.
- Facebook ongoing runtime/schedule and cost after the pilot. Paid data providers are outside this initial proposal and require a separate provider/budget decision.
- Current platform access/collection permissions must be assessed independently of whether a page is publicly visible.

## Reference checks

- Meta's public-group visibility explanation: https://about.fb.com/news/2025/11/facebook-update-group-admins-flexibility-protecting-member-privacy/ . New public posts can be visible outside Facebook; historical posts in a converted group can remain restricted.
- Meta's distinction between authorized and unauthorized automation: https://about.fb.com/news/2021/04/how-we-combat-scraping/amp/ . Public visibility alone is not collection permission.
- A web-fetch attempt on anonymous Hanoi Marketplace search did not return usable content because the research tool reported throttling. This is not evidence that a local Playwright run succeeds or that Facebook specifically denied that local runtime.
