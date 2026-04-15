"""
lambda_handler.py — AWS Lambda entry point
==========================================
Trigger: EventBridge 00:00 VN (17:00 UTC) mỗi ngày
Flow: scrape listings HÔM QUA → full detail → upsert DynamoDB → export daily CSV lên S3
"""

import csv
import json
import logging
import os
from io import StringIO
from datetime import datetime, timezone, timedelta

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

S3_BUCKET = os.getenv("CHOTOT_EXPORT_S3_BUCKET", "").strip()
S3_PREFIX = os.getenv("CHOTOT_EXPORT_S3_PREFIX", "data/daily").strip().strip("/")


def _flatten_row(record: dict) -> dict:
    ad = record.get("ad", record) if isinstance(record, dict) else {}
    imgs = ad.get("images", [])
    if not isinstance(imgs, list):
        imgs = []

    return {
        "list_id": ad.get("list_id"),
        "ad_id": ad.get("ad_id"),
        "subject": ad.get("subject"),
        "price": ad.get("price"),
        "price_string": ad.get("price_string"),
        "motorbikebrand": ad.get("motorbikebrand"),
        "motorbikemodel": ad.get("motorbikemodel"),
        "motorbiketype": ad.get("motorbiketype"),
        "regdate": ad.get("regdate"),
        "mileage_v2": ad.get("mileage_v2"),
        "condition_ad_name": ad.get("condition_ad_name"),
        "region_name": ad.get("region_name_v3") or ad.get("region_name"),
        "area_name": ad.get("area_name"),
        "ward_name": ad.get("ward_name"),
        "list_time": ad.get("list_time"),
        "date": ad.get("date"),
        "account_name": ad.get("account_name"),
        "account_oid": ad.get("account_oid"),
        "thumbnail_image": ad.get("thumbnail_image"),
        "webp_image": ad.get("webp_image"),
        "image": ad.get("image"),
        "images_count": len(imgs),
        "images": "|".join(x for x in imgs if isinstance(x, str)),
        "url": f"https://xe.chotot.com/mua-ban-xe-may/{ad.get('list_id')}" if ad.get("list_id") else "",
    }


def _export_daily_csv_to_s3(raw_records: list, day_str: str) -> tuple[int, str]:
    if not S3_BUCKET or not raw_records:
        return 0, ""

    rows = [_flatten_row(r) for r in raw_records]
    if not rows:
        return 0, ""

    headers = list(rows[0].keys())
    sio = StringIO()
    writer = csv.DictWriter(sio, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)

    body = sio.getvalue().encode("utf-8-sig")
    key = f"{S3_PREFIX}/chotot_daily_{day_str}.csv"
    boto3.client("s3").put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=body,
        ContentType="text/csv; charset=utf-8",
    )
    return len(rows), key


def handler(event: dict, context) -> dict:
    from chotot import scrape_yesterday, upsert_to_dynamodb

    tz_vn = timezone(timedelta(hours=7))
    now_vn = datetime.now(tz_vn)
    scrape_day = (now_vn - timedelta(days=1)).date().isoformat()

    log.info(json.dumps({"event": "start", "now_vn": now_vn.isoformat()}))

    dry_run = event.get("dry_run", False)

    raw_records = scrape_yesterday()
    log.info(f"Scraped {len(raw_records)} records từ {scrape_day}")

    written = 0
    if raw_records and not dry_run:
        written = upsert_to_dynamodb(raw_records)

    csv_rows = 0
    csv_key = ""
    if raw_records and not dry_run and S3_BUCKET:
        try:
            csv_rows, csv_key = _export_daily_csv_to_s3(raw_records, scrape_day)
            log.info(json.dumps({"event": "daily_csv_uploaded", "rows": csv_rows, "s3_key": csv_key}))
        except Exception as e:
            log.error(f"Daily CSV upload failed: {e}")

    result = {
        "new_records": len(raw_records),
        "ddb_written": written,
        "daily_csv_rows": csv_rows,
        "daily_csv_key": csv_key,
        "date": str(now_vn.date()),
        "finished_at": datetime.utcnow().isoformat() + "Z",
    }
    log.info(json.dumps({"event": "done", **result}))
    return {"statusCode": 200, "body": json.dumps(result)}
