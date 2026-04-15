#!/usr/bin/env python3
"""Worker Lambda: consume SQS image jobs, download media, upload to S3, track state in DynamoDB."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3
import requests
from botocore.exceptions import ClientError


log = logging.getLogger()
log.setLevel(logging.INFO)

BUCKET = os.getenv("IMAGE_BUCKET", "chotot-dashboard-404850807717")
STATE_TABLE = os.getenv("STATE_TABLE", "chotot-image-sync-state")
MAX_BYTES = int(os.getenv("MAX_BYTES", str(25 * 1024 * 1024)))

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb")
state = ddb.Table(STATE_TABLE)

_http = requests.Session()
_http.headers.update({"User-Agent": "chotot-image-worker/1.0"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_done(job: dict, size_bytes: int = 0, content_type: str = "") -> None:
    state.put_item(
        Item={
            "s3_key": job["s3_key"],
            "status": "done",
            "list_id": str(job.get("list_id", "")),
            "media_type": job.get("media_type", ""),
            "url": job.get("url", ""),
            "size_bytes": int(size_bytes),
            "content_type": content_type,
            "updated_at": _utc_now(),
            "attempts": 0,
        }
    )


def _mark_error(job: dict, err: str) -> None:
    key = {"s3_key": job["s3_key"]}
    now = _utc_now()
    # increment attempts while recording latest error
    state.update_item(
        Key=key,
        UpdateExpression=(
            "SET #st=:s, #li=:li, #mt=:mt, #u=:u, #err=:e, #ts=:ts "
            "ADD #at :one"
        ),
        ExpressionAttributeNames={
            "#st": "status",
            "#li": "list_id",
            "#mt": "media_type",
            "#u": "url",
            "#err": "last_error",
            "#ts": "updated_at",
            "#at": "attempts",
        },
        ExpressionAttributeValues={
            ":s": "error",
            ":li": str(job.get("list_id", "")),
            ":mt": job.get("media_type", ""),
            ":u": job.get("url", ""),
            ":e": str(err)[:400],
            ":ts": now,
            ":one": 1,
        },
    )


def _already_done(s3_key: str) -> bool:
    r = state.get_item(Key={"s3_key": s3_key}, ConsistentRead=False)
    item = r.get("Item")
    return bool(item and item.get("status") == "done")


def _object_exists(s3_key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=s3_key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound", "403", "AccessDenied"}:
            return False
        raise


def _download(url: str) -> tuple[bytes, str]:
    r = _http.get(url, timeout=(8, 35), stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP_{r.status_code}")

    chunks = []
    total = 0
    for ch in r.iter_content(chunk_size=1024 * 64):
        if not ch:
            continue
        total += len(ch)
        if total > MAX_BYTES:
            raise RuntimeError("FILE_TOO_LARGE")
        chunks.append(ch)

    body = b"".join(chunks)
    content_type = r.headers.get("Content-Type", "application/octet-stream")
    return body, content_type


def _process(job: dict) -> tuple[bool, str]:
    for req in ("list_id", "media_type", "url", "s3_key"):
        if not job.get(req):
            return True, f"skip_missing_{req}"

    s3_key = job["s3_key"]

    if _already_done(s3_key):
        return True, "already_done"

    if _object_exists(s3_key):
        _mark_done(job, size_bytes=0, content_type="")
        return True, "already_in_s3"

    body, ctype = _download(job["url"])

    s3.put_object(
        Bucket=BUCKET,
        Key=s3_key,
        Body=body,
        ContentType=ctype,
        StorageClass="STANDARD",
    )
    _mark_done(job, size_bytes=len(body), content_type=ctype)
    return True, "uploaded"


def handler(event, context):
    records = event.get("Records", [])
    fails = []
    ok = 0
    skipped = 0

    for rec in records:
        msg_id = rec.get("messageId", "")
        try:
            job = json.loads(rec.get("body", "{}"))
            success, reason = _process(job)
            if success:
                if reason.startswith("skip") or reason.startswith("already"):
                    skipped += 1
                else:
                    ok += 1
            else:
                fails.append({"itemIdentifier": msg_id})
        except Exception as e:  # noqa: BLE001
            try:
                body = json.loads(rec.get("body", "{}"))
                if isinstance(body, dict) and body.get("s3_key"):
                    _mark_error(body, str(e))
            except Exception:
                pass
            fails.append({"itemIdentifier": msg_id})

    log.info(
        json.dumps(
            {
                "event": "worker_done",
                "received": len(records),
                "uploaded": ok,
                "skipped": skipped,
                "failed": len(fails),
            }
        )
    )
    return {"batchItemFailures": fails}
