"""
refresh_handler.py — Weekly full listing refresh Lambda
========================================================
Re-fetches detail API for all list_ids in DynamoDB and upserts fresh data.
Processes in batches; chains async self-invoke until complete.

Trigger: EventBridge weekly (Sunday 01:00 VN = Saturday 18:00 UTC)
Manual:  {"dry_run": false, "batch_size": 3000}
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

DDB_TABLE = os.getenv("CHOTOT_DDB_TABLE", "chotot-xe-may")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
S3_BUCKET = os.getenv("CHOTOT_EXPORT_S3_BUCKET", "chotot-dashboard-404850807717")
CHECKPOINT_KEY = os.getenv("REFRESH_CHECKPOINT_KEY", "data/refresh_checkpoint.json")
LAMBDA_NAME = os.getenv("REFRESH_LAMBDA_NAME", "chotot-weekly-refresh")
DEFAULT_BATCH = int(os.getenv("REFRESH_BATCH_SIZE", "2500"))
WORKERS = int(os.getenv("REFRESH_WORKERS", "20"))
TIME_BUDGET_SEC = int(os.getenv("REFRESH_TIME_BUDGET_SEC", "780"))


def _load_checkpoint(s3) -> dict:
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=CHECKPOINT_KEY)
        return json.loads(obj["Body"].read())
    except Exception as e:
        if getattr(e, "response", {}).get("Error", {}).get("Code") == "NoSuchKey":
            return {}
        return {}


def _save_checkpoint(s3, state: dict) -> None:
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=CHECKPOINT_KEY,
        Body=json.dumps(state, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def _scan_list_ids(ddb, start_key: dict | None, limit: int) -> tuple[list[int], dict | None]:
    kwargs = {
        "TableName": DDB_TABLE,
        "ProjectionExpression": "list_id",
        "Limit": limit,
    }
    if start_key:
        kwargs["ExclusiveStartKey"] = start_key

    resp = ddb.scan(**kwargs)
    ids = [int(item["list_id"]["N"]) for item in resp.get("Items", []) if "list_id" in item]
    return ids, resp.get("LastEvaluatedKey")


def _chain_self(lambda_client, event: dict) -> None:
    payload = json.dumps(event).encode("utf-8")
    lambda_client.invoke(
        FunctionName=LAMBDA_NAME,
        InvocationType="Event",
        Payload=payload,
    )


def _handler(event: dict, context) -> dict:
    from chotot import _fetch_detail_one, _make_session, upsert_to_dynamodb

    dry_run = event.get("dry_run", False)
    batch_size = int(event.get("batch_size", DEFAULT_BATCH))
    reset = event.get("reset", False)

    ddb = boto3.client("dynamodb", region_name=AWS_REGION)
    s3 = boto3.client("s3")
    lam = boto3.client("lambda", region_name=AWS_REGION)

    ck = {} if reset else _load_checkpoint(s3)
    start_key = ck.get("last_evaluated_key")
    run_id = ck.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if reset or not ck:
        ck = {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "processed": 0,
            "refreshed": 0,
            "errors": 0,
            "last_evaluated_key": None,
            "status": "running",
        }

    log.info(json.dumps({"event": "refresh_start", "run_id": run_id, "processed": ck.get("processed", 0)}))

    t0 = time.time()
    session = _make_session()
    while time.time() - t0 < TIME_BUDGET_SEC:
        ids, start_key = _scan_list_ids(ddb, start_key, batch_size)
        if not ids:
            ck["status"] = "complete"
            ck["finished_at"] = datetime.now(timezone.utc).isoformat()
            ck["last_evaluated_key"] = None
            if not dry_run:
                _save_checkpoint(s3, ck)
            log.info(json.dumps({"event": "refresh_complete", "run_id": run_id, **ck}))
            return {"statusCode": 200, "body": json.dumps(ck)}

        args = [(lid, session) for lid in ids]
        records = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(_fetch_detail_one, a): a[0] for a in args}
            batch_errors = 0
            for future in as_completed(futures):
                _lid, detail = future.result()
                if detail:
                    records.append(detail)
                else:
                    batch_errors += 1

        if records and not dry_run:
            upsert_to_dynamodb(records)

        ck["processed"] = ck.get("processed", 0) + len(ids)
        ck["refreshed"] = ck.get("refreshed", 0) + len(records)
        ck["errors"] = ck.get("errors", 0) + batch_errors
        ck["last_evaluated_key"] = start_key
        if not dry_run:
            _save_checkpoint(s3, ck)

        log.info(json.dumps({
            "event": "refresh_batch",
            "run_id": run_id,
            "batch_ids": len(ids),
            "batch_ok": len(records),
            "total_processed": ck["processed"],
        }))

        if not start_key:
            ck["status"] = "complete"
            ck["finished_at"] = datetime.now(timezone.utc).isoformat()
            if not dry_run:
                _save_checkpoint(s3, ck)
            log.info(json.dumps({"event": "refresh_complete", "run_id": run_id, **ck}))
            return {"statusCode": 200, "body": json.dumps(ck)}

    # Time budget exceeded — chain next invocation
    ck["status"] = "running"
    if not dry_run:
        _save_checkpoint(s3, ck)
        _chain_self(lam, {"dry_run": dry_run, "batch_size": batch_size})

    log.info(json.dumps({"event": "refresh_chained", "run_id": run_id, "processed": ck["processed"]}))
    return {"statusCode": 200, "body": json.dumps(ck)}


def handler(event: dict, context) -> dict:
    from market_store import CollectionAttempt
    with CollectionAttempt('chotot', 'refresh', enabled=not event.get('dry_run', False)):
        return _handler(event, context)
