# Chotot Data Pipeline Handoff

Last verified: 2026-06-07

This repo powers a Chotot motorcycle data pipeline. It scrapes daily listings from Chotot, stores raw listing data in DynamoDB, exports daily CSV files to S3, syncs listing images to S3 through Lambda/SQS, and serves a dashboard through CloudFront.

Do not paste AWS secrets into chat or commit them. The AWS key was exposed earlier in the old conversation, so it should be rotated when there is time. The working local AWS profile is `harry`, stored in `~/.aws/credentials`, and points to AWS account `404850807717` (`arn:aws:iam::404850807717:user/harry.nguyen`). Use `AWS_PROFILE=harry`.

## Current Git State

Remote repo: `https://github.com/TimotoDev/chototcaodulieu.git`

`main` is pushed and current as of commit:

```text
5c09fc1 Merge branch 'claude/trusting-raman'
```

Important commits now on `main`:

```text
5c09fc1 Merge branch 'claude/trusting-raman'
934a88a Preserve local scrape checkpoint changes
35ca80b Add Lambda-only image sync pipeline and daily CSV export
b22e2fa Add Chotot AWS pipeline, daily scraper, and dashboard
6c578cc initial upload of chotot scripts and data
```

If push fails with `Permission to TimotoDev/chototcaodulieu.git denied to HarryNguyen2662`, log into GitHub with an account that has write access, then run:

```bash
cd /Users/toannguyen/chototcaodulieu
git push origin main
```

## AWS Resources

Region: `ap-southeast-1`

Main S3 bucket:

```text
chotot-dashboard-404850807717
```

Main DynamoDB tables:

```text
chotot-xe-may
chotot-image-sync-state
```

Main Lambda functions:

```text
chotot-daily-scrape
chotot-dashboard-api
chotot-image-sync-producer
chotot-image-sync-worker
```

Queues:

```text
chotot-image-sync-queue
chotot-image-sync-dlq
```

EventBridge schedules:

```text
chotot-daily-0000          # daily scrape, 00:00 Vietnam time
chotot-image-sync-daily    # daily image sync producer, 00:20 Vietnam time
```

Dashboard:

```text
https://d1cwchl0xo05n0.cloudfront.net
```

Useful S3 console links:

```text
https://s3.console.aws.amazon.com/s3/buckets/chotot-dashboard-404850807717?region=ap-southeast-1&prefix=data/
https://s3.console.aws.amazon.com/s3/buckets/chotot-dashboard-404850807717?region=ap-southeast-1&prefix=data/daily/
https://s3.console.aws.amazon.com/s3/buckets/chotot-dashboard-404850807717?region=ap-southeast-1&prefix=images/
```

Verified approximate live counts on 2026-06-07:

```text
chotot-xe-may: approx 83,011 items
chotot-image-sync-state: approx 344,394 items
```

These are approximate DynamoDB table metadata counts, not a fresh full scan.

## Repo File Map

Core daily scrape:

```text
chotot.py
lambda_handler.py
setup_aws.sh
requirements.txt
```

`chotot.py` scrapes yesterday's listings from Chotot, fetches full detail JSON, and upserts raw records to DynamoDB. `lambda_handler.py` is the Lambda entrypoint and also exports `data/daily/chotot_daily_YYYY-MM-DD.csv` to S3 after a successful scrape.

Dashboard:

```text
api_handler.py
dashboard/index.html
deploy_dashboard.sh
```

The dashboard reads from DynamoDB through API Gateway/Lambda and supports cursor pagination for browsing many records. The dashboard is hosted through S3/CloudFront.

Initial/full scrape and research tools:

```text
init_scrape.py
fetch_raw_details.py
scrape_by_seller.py
upload_to_aws.py
upload_to_ddb.py
chotot_areas.json
chotot_aws_pipeline_en.svg
```

Image sync:

```text
image_sync_producer.py
image_sync_worker.py
deploy_image_sync_lambda.py
sync_images_to_s3.py
```

`image_sync_producer.py` reads listing image URLs from DynamoDB and pushes jobs to SQS. `image_sync_worker.py` consumes SQS jobs, downloads image URLs, writes to S3, and tracks status in `chotot-image-sync-state`. `sync_images_to_s3.py` is a local resumable fallback script; the preferred deployed path is Lambda/SQS.

## Data Layout

DynamoDB `chotot-xe-may`:

```text
Primary key: list_id (Number)
GSI: date-index
GSI partition key: date_added (String)
GSI sort key: list_id (Number)
```

Each bike item stores parsed top-level Chotot fields plus `_raw_json`, which contains the full raw detail payload from Chotot.

S3 layout:

```text
s3://chotot-dashboard-404850807717/
├── index.html
├── data/
│   ├── chotot_all_xe.csv
│   ├── chotot_all_xe.json
│   ├── chotot_full_raw_init.csv
│   └── daily/
│       └── chotot_daily_YYYY-MM-DD.csv
└── images/
    └── <list_id>/
        ├── images/
        ├── image/
        ├── thumbnail/
        └── webp/
```

Image keys use:

```text
images/<list_id>/<media_type>/<sha1(url)>.<ext>
```

This is intentional. To fetch all images for a bike, list S3 with:

```text
Prefix = images/<list_id>/
```

## Chotot API Findings

Primary list endpoint:

```text
https://gateway.chotot.com/v1/public/ad-listing
```

Detail endpoint:

```text
https://gateway.chotot.com/v2/public/ad-listing/{list_id}
```

Category for motorcycles:

```text
cg=2020
```

Important API behavior:

```text
limit maxes out around 50/page
st=s,k is the useful newest-first sort
old r= province parameter is ignored
new region parameter is region=1..13
price filter format is price=min-max
public API has pagination/window limits
website SEO count around 53k was stale/cached, not active public API count
```

Initial scrape eventually collected the practical public API maximum. The team validated count discrepancies through count endpoints, type-sum checks, region/price/model filters, area filters, seller filters, and detail endpoint checks.

## Operational Commands

Check AWS identity:

```bash
AWS_PROFILE=harry python3 - <<'PY'
import boto3
print(boto3.Session(profile_name='harry').client('sts').get_caller_identity())
PY
```

Deploy daily scrape Lambda:

```bash
cd /Users/toannguyen/chototcaodulieu
AWS_PROFILE=harry bash setup_aws.sh
```

Deploy dashboard:

```bash
cd /Users/toannguyen/chototcaodulieu
AWS_PROFILE=harry bash deploy_dashboard.sh
```

Deploy image sync Lambda/SQS stack:

```bash
cd /Users/toannguyen/chototcaodulieu
AWS_PROFILE=harry python3 deploy_image_sync_lambda.py
```

Trigger daily scrape manually:

```bash
AWS_PROFILE=harry python3 - <<'PY'
import boto3, json
s=boto3.Session(profile_name='harry', region_name='ap-southeast-1')
l=s.client('lambda')
r=l.invoke(
    FunctionName='chotot-daily-scrape',
    InvocationType='RequestResponse',
    Payload=json.dumps({'dry_run': False}).encode()
)
print(r['Payload'].read().decode())
PY
```

Trigger full image sync producer:

```bash
AWS_PROFILE=harry python3 - <<'PY'
import boto3, json
s=boto3.Session(profile_name='harry', region_name='ap-southeast-1')
l=s.client('lambda')
r=l.invoke(
    FunctionName='chotot-image-sync-producer',
    InvocationType='Event',
    Payload=json.dumps({'mode': 'full', 'dry_run': False, 'page_limit': 200}).encode()
)
print(r['StatusCode'])
PY
```

Check image sync queue:

```bash
AWS_PROFILE=harry python3 - <<'PY'
import boto3
s=boto3.Session(profile_name='harry', region_name='ap-southeast-1')
sqs=s.client('sqs')
for q in ['chotot-image-sync-queue','chotot-image-sync-dlq']:
    url=sqs.get_queue_url(QueueName=q)['QueueUrl']
    attrs=sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible']
    )['Attributes']
    print(q, attrs)
PY
```

Count image objects in S3:

```bash
AWS_PROFILE=harry python3 - <<'PY'
import boto3
s=boto3.Session(profile_name='harry', region_name='ap-southeast-1')
s3=s.client('s3')
count=0
size=0
for page in s3.get_paginator('list_objects_v2').paginate(
    Bucket='chotot-dashboard-404850807717',
    Prefix='images/'
):
    objs=page.get('Contents', [])
    count += len(objs)
    size += sum(o.get('Size', 0) for o in objs)
print(count, size)
PY
```

## Known Issues And Sharp Edges

1. Do not commit AWS credentials. The old conversation exposed a key; rotate it if this system will keep running.

2. `chotot.py` on `main` is now the Lambda daily scraper, not the old region-mode full scraper. The old local full-scrape checkpoint changes are preserved in git history at `934a88a`.

3. Daily CSV export was missing at first because S3 export was manual. This is now fixed in `lambda_handler.py`: after every successful daily scrape, it writes `data/daily/chotot_daily_YYYY-MM-DD.csv`.

4. `date_added` should be treated as Vietnam-day grouping. There was a UTC drift bug earlier; keep VN timezone behavior when editing.

5. Image sync successes and failures are tracked by `chotot-image-sync-state`. Most image failures are `HTTP_404` from Chotot CDN URLs that no longer exist. That is expected for expired/changed listings.

6. The DLQ may contain stale failed image jobs. Before blindly replaying it, inspect whether failures are still mostly `HTTP_404`. A useful future improvement is to call the detail API again for the `list_id`, refresh image URLs, and requeue only live URLs.

7. S3 image storage is large. An earlier run uploaded around 339k image objects and roughly 40 GiB. Watch S3 cost before repeatedly re-running full image sync.

8. DynamoDB table counts from `describe_table` are approximate. Use paginated scans/queries for exact counts when correctness matters.

9. Avoid deleting `chotot-dashboard-404850807717`; it contains dashboard assets, exports, and images. The old legacy bucket `chotot-xe-may-data` was deleted earlier.

10. `docker` may not be in PATH in Codex shells. If needed, call `/usr/local/bin/docker` directly.

## What A New Agent Should Do Next

First, run:

```bash
cd /Users/toannguyen/chototcaodulieu
git status --short
git log --oneline -n 5 --decorate
```

Then verify live AWS only if the task needs it:

```bash
AWS_PROFILE=harry python3 - <<'PY'
import boto3
s=boto3.Session(profile_name='harry', region_name='ap-southeast-1')
print(s.client('sts').get_caller_identity())
PY
```

If asked about data freshness, check CloudWatch logs for `chotot-daily-scrape` and S3 prefix `data/daily/`.

If asked about images, check SQS queue/DLQ and table `chotot-image-sync-state`.

If asked to deploy, prefer the existing scripts in this repo. Do not invent a new AWS architecture unless there is a clear reason.
