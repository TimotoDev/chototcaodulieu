"""
lambda_handler.py — AWS Lambda entry point
==========================================
Trigger: EventBridge 00:00 VN (17:00 UTC) mỗi ngày
Flow: scrape listings HÔM QUA → full detail → upsert DynamoDB
"""

import json
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event: dict, context) -> dict:
    from chotot import scrape_yesterday, upsert_to_dynamodb

    tz_vn = timezone(timedelta(hours=7))
    now_vn = datetime.now(tz_vn)

    log.info(json.dumps({"event": "start", "now_vn": now_vn.isoformat()}))

    dry_run = event.get("dry_run", False)

    raw_records = scrape_yesterday()
    log.info(f"Scraped {len(raw_records)} records từ {now_vn.date() - __import__('datetime').timedelta(days=1)}")

    written = 0
    if raw_records and not dry_run:
        written = upsert_to_dynamodb(raw_records)

    result = {
        "new_records": len(raw_records),
        "ddb_written": written,
        "date": str(now_vn.date()),
        "finished_at": datetime.utcnow().isoformat() + "Z",
    }
    log.info(json.dumps({"event": "done", **result}))
    return {"statusCode": 200, "body": json.dumps(result)}
