#!/usr/bin/env python3
"""Producer Lambda: read bike records from DynamoDB and enqueue image sync jobs to SQS.

Supports:
- daily mode (default): query date-index by date_added (yesterday VN)
- full mode: scan whole table
- continuation: reinvoke itself when near timeout using cursor
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer


log = logging.getLogger()
log.setLevel(logging.INFO)

DDB_TABLE = os.getenv("SOURCE_TABLE", "chotot-xe-may")
DDB_INDEX = os.getenv("SOURCE_INDEX", "date-index")
QUEUE_URL = os.environ["QUEUE_URL"]
S3_PREFIX = os.getenv("S3_PREFIX", "images")
PAGE_LIMIT = int(os.getenv("PAGE_LIMIT", "200"))
STOP_BEFORE_MS = int(os.getenv("STOP_BEFORE_MS", "45000"))


ddb = boto3.resource("dynamodb")
queue_client = boto3.client("sqs")
lambda_client = boto3.client("lambda")

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _vn_yesterday_str() -> str:
    tz_vn = timezone(timedelta(hours=7))
    now_vn = datetime.now(tz_vn)
    return (now_vn - timedelta(days=1)).strftime("%Y-%m-%d")


def _b64_encode_dict(d: Optional[dict]) -> Optional[str]:
    if not d:
        return None
    typed = {k: _serializer.serialize(v) for k, v in d.items()}
    raw = json.dumps(typed, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _b64_decode_dict(s: Optional[str]) -> Optional[dict]:
    if not s:
        return None
    raw = base64.urlsafe_b64decode(s.encode("utf-8"))
    typed = json.loads(raw.decode("utf-8"))
    return {k: _deserializer.deserialize(v) for k, v in typed.items()}


def _normalize_list_id(v) -> Optional[str]:
    if v is None:
        return None
    try:
        return str(int(v))
    except Exception:
        s = str(v).strip()
        return s or None


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = PurePosixPath(path).suffix.lower()
    if ext and len(ext) <= 8:
        return ext
    return ".jpg"


def _build_s3_key(list_id: str, media_type: str, url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"{S3_PREFIX.rstrip('/')}/{list_id}/{media_type}/{h}{_ext_from_url(url)}"


def _extract_urls(item: dict) -> List[Tuple[str, str]]:
    urls: List[Tuple[str, str]] = []

    images = item.get("images")
    if isinstance(images, list):
        for u in images:
            if isinstance(u, str) and u.startswith("http"):
                urls.append(("images", u))

    for key, media_type in (
        ("image", "image"),
        ("thumbnail_image", "thumbnail"),
        ("webp_image", "webp"),
    ):
        u = item.get(key)
        if isinstance(u, str) and u.startswith("http"):
            urls.append((media_type, u))

    # per-item dedupe by URL
    seen = set()
    out: List[Tuple[str, str]] = []
    for media_type, u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append((media_type, u))
    return out


def _send_jobs(jobs: Iterable[dict], dry_run: bool = False) -> int:
    jobs = list(jobs)
    if not jobs:
        return 0
    if dry_run:
        return len(jobs)

    sent = 0
    for i in range(0, len(jobs), 10):
        chunk = jobs[i : i + 10]
        entries = [
            {
                "Id": str(i + idx),
                "MessageBody": json.dumps(j, separators=(",", ":")),
            }
            for idx, j in enumerate(chunk)
        ]
        resp = queue_client.send_message_batch(QueueUrl=QUEUE_URL, Entries=entries)
        sent += len(resp.get("Successful", []))
        failed = resp.get("Failed", [])
        if failed:
            log.warning("SQS batch failed count=%s", len(failed))
    return sent


def _fetch_page(mode: str, date: str, cursor: Optional[dict], limit: int) -> Tuple[List[dict], Optional[dict]]:
    table = ddb.Table(DDB_TABLE)
    projection = "list_id, images, image, thumbnail_image, webp_image"

    if mode == "full":
        kwargs = {"ProjectionExpression": projection, "Limit": limit}
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        resp = table.scan(**kwargs)
    else:
        kwargs = {
            "IndexName": DDB_INDEX,
            "KeyConditionExpression": Key("date_added").eq(date),
            "ProjectionExpression": projection,
            "Limit": limit,
        }
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        resp = table.query(**kwargs)

    return resp.get("Items", []), resp.get("LastEvaluatedKey")


def handler(event, context):
    mode = (event or {}).get("mode", "daily")
    if mode not in {"daily", "full"}:
        mode = "daily"

    date = (event or {}).get("date") or _vn_yesterday_str()
    page_limit = int((event or {}).get("page_limit", PAGE_LIMIT))
    stop_after = int((event or {}).get("stop_after", 0))
    dry_run = bool((event or {}).get("dry_run", False))
    cursor = _b64_decode_dict((event or {}).get("cursor"))

    unique_keys = set()
    queued = 0
    records_seen = 0
    pages = 0
    next_cursor = cursor

    while True:
        if context.get_remaining_time_in_millis() <= STOP_BEFORE_MS:
            break

        items, next_cursor = _fetch_page(mode, date, next_cursor, page_limit)
        pages += 1
        if not items:
            next_cursor = None
            break

        jobs = []
        for item in items:
            list_id = _normalize_list_id(item.get("list_id"))
            if not list_id:
                continue
            records_seen += 1
            for media_type, url in _extract_urls(item):
                s3_key = _build_s3_key(list_id, media_type, url)
                if s3_key in unique_keys:
                    continue
                unique_keys.add(s3_key)
                jobs.append(
                    {
                        "list_id": list_id,
                        "media_type": media_type,
                        "url": url,
                        "s3_key": s3_key,
                    }
                )

        queued += _send_jobs(jobs, dry_run=dry_run)

        if stop_after and queued >= stop_after:
            next_cursor = None
            break

        if not next_cursor:
            break

    continuation_invoked = False
    if next_cursor:
        payload = {
            "mode": mode,
            "date": date,
            "page_limit": page_limit,
            "stop_after": 0,
            "dry_run": dry_run,
            "cursor": _b64_encode_dict(next_cursor),
        }
        lambda_client.invoke(
            FunctionName=context.invoked_function_arn,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        continuation_invoked = True

    result = {
        "mode": mode,
        "date": date,
        "pages": pages,
        "records_seen": records_seen,
        "queued_jobs": queued,
        "continuation": continuation_invoked,
        "dry_run": dry_run,
    }
    log.info(json.dumps({"event": "producer_done", **result}))
    return {"statusCode": 200, "body": json.dumps(result)}
