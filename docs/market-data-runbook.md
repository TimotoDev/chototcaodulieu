# Shared motorcycle market storage

## Components

- `motorbike-market-listings`: pay-per-request DynamoDB table, string `listing_id=source:external_id`, immutable first-seen source index, normalized public metadata. No author/contact/raw text fields.
- `motorbike-market-metadata`: source run outcomes, last-success status, backfill checkpoints, reconciliation IDs and timestamped full-scan aggregate snapshots. Run history expires after 90 days; normalized listings are retained.
- `timoto-market-archive-404850807717`: private, public-access-blocked, AES256-encrypted JSONL collector outputs; 90-day lifecycle. This is separate from the public dashboard bucket. Archive files contain sanitized collector output, not cookies or seller identities.
- Existing Chợ Tốt raw table/exports continue to retain original records. Opt-in normalized mirrors in `chotot.py` cover both daily and refresh collectors.
- Facebook is a private standalone Node worker, with a fresh anonymous browser. Optional sign-in prompts may be closed through an explicit visible Close button; required login/checkpoint/rate limiting stops collection. No stored sessions, stealth flags or private APIs.
- `motorbike-market-snapshot` scans normalized rows hourly and publishes stats; the public API reads snapshots rather than pretending a sample scan covers all records.

## Deployment

`python3 tools/deploy_market.py` prints the exact account/resources/IAM plan. Account is checked against `404850807717`. Existing bucket policy, unrelated role policies, schedules, API routes and Lambda environment entries are preserved.

After review, explicit flags perform scoped phases:

```sh
python3 tools/deploy_market.py --apply-resources --deploy-snapshot
python3 tools/deploy_market.py --deploy-collectors
python3 tools/deploy_market.py --deploy-api
```

Every existing-function update publishes an immutable pre-cutover Lambda version and prints its version ID for rollback. Dependencies already packaged with existing Chợ Tốt Lambdas are retained, and only named source modules are replaced. Snapshot Lambda uses the runtime boto3 SDK.

Backfill independently of the old collectors:

```sh
export MARKET_LISTINGS_TABLE=motorbike-market-listings
export MARKET_METADATA_TABLE=motorbike-market-metadata
export MARKET_ARCHIVE_BUCKET=timoto-market-archive-404850807717
python3 tools/market_backfill.py --checkpoint /private/path/backfill.json --segments 8 --workers 8
AWS_PROFILE=harry python3 -c 'from market_snapshot import handler; print(handler({}, None))'
```

Re-run the same backfill command to resume. Failed page writes never advance its checkpoint; replay preserves each row's first-seen order and newer observations. Check `backfill#chotot` and `reconcile#...` metadata before claiming a complete migration. Source rows remain available for recovery.

The segmented importer projects only normalized input fields, uses conditional transaction inserts in batches of 25, and falls back to atomic upsert if an existing/concurrent record cancels a batch. Keep the same segment count when resuming a checkpoint; changing segmentation requires a new checkpoint and safely replays existing IDs. New-table backup activation is retried while AWS finishes enabling its backup subsystem.

Deploy `dashboard/index.html` to the existing dashboard bucket only after API validation, then invalidate the existing CloudFront `/index.html` and `/` objects. This integration adds no public scraper control endpoint.

## Facebook collection and persistence

Use the standalone public worker command documented in the private collector repository. Set the same three `MARKET_*` variables and `AWS_PROFILE=harry` for the supervised local pilot. AWS SDK credentials never enter browser/UI code. `--collect-only --output /private/path/pilot.json` allows an anonymous collection pilot before storage writes. `--input` persists validated normalized pilot output without collecting the same pages again.

A successful anonymous local run does not prove unattended operation from AWS. No recurring browser infrastructure or paid proxy/vendor service is provisioned by this change. Public Groups remain `never_run` until public numeric group URLs are configured.

Success means usable records were read, durably written, and read back. `observed_at` is collection time, not Facebook posting time. Unknown posting times/prices stay null. Partial writes produce explicit partial outcomes and reconciliation IDs; archives and saved counts can be verified separately.

## Rollback

Restore the prior dashboard object from a private backup. Remove only the three added MARKET environment entries from the API to switch back to its legacy raw-table reader; retain other env entries. Restore published previous function versions using their AWS code packages if code rollback is necessary. Disable the new hourly snapshot rule if required. Keep both new tables and private archives for diagnosis; do not delete the original Chợ Tốt table.

## Verification

Python: `.venv/bin/python -m unittest discover -s tests` with boto3 and `moto[dynamodb,s3]` installed. Browser: `PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node tests/check_dashboard.mjs`. The browser test uses isolated synthetic fixtures only inside the test browser, never live storage.

API: inspect `/api/stats?source=all`, `/api/listings?source=facebook_marketplace`, source statuses, date/filter cursors and saved original IDs. Validate S3 public-access block and archive encryption independently. Stats may lag the feed by one hour; the dashboard displays the snapshot timestamp and backfill completeness.
