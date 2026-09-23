#!/usr/bin/env python3
"""
Export full RAW Chotot detail JSON from DynamoDB (_raw_json field).
No flattening — each line is the exact API response as scraped.

Usage:
    AWS_PROFILE=harry python3 export_raw_from_ddb.py
    AWS_PROFILE=harry python3 export_raw_from_ddb.py --upload-s3
"""

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TABLE = os.getenv("CHOTOT_DDB_TABLE", "chotot-xe-may")
REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
BUCKET = os.getenv("CHOTOT_EXPORT_S3_BUCKET", "chotot-dashboard-404850807717")
OUT_DIR = os.path.join(os.path.dirname(__file__), "reports")
OUT_JSONL = os.path.join(OUT_DIR, "chotot_all_xe_raw.jsonl")
S3_KEY = "data/chotot_all_xe_raw.jsonl"


def export_raw(out_path: str) -> tuple[int, int]:
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    count = 0
    skipped = 0
    scan_kwargs = {"ProjectionExpression": "list_id, #r", "ExpressionAttributeNames": {"#r": "_raw_json"}}

    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            resp = table.scan(**scan_kwargs)
            for item in resp.get("Items", []):
                raw = item.get("_raw_json")
                if not raw:
                    skipped += 1
                    continue
                try:
                    obj = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                count += 1
                if count % 5000 == 0:
                    log.info(f"  exported {count:,}...")

            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    return count, skipped


def upload_s3(local_path: str) -> str:
    s3 = boto3.client("s3", region_name=REGION)
    log.info(f"Uploading to s3://{BUCKET}/{S3_KEY} ...")
    s3.upload_file(local_path, BUCKET, S3_KEY, ExtraArgs={"ContentType": "application/x-ndjson"})
    return f"https://d1cwchl0xo05n0.cloudfront.net/{S3_KEY}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--upload-s3", action="store_true")
    p.add_argument("-o", "--output", default=OUT_JSONL)
    args = p.parse_args()

    log.info(f"Exporting RAW from DynamoDB table: {TABLE}")
    count, skipped = export_raw(args.output)
    size = os.path.getsize(args.output)
    log.info(f"Done: {count:,} records | skipped {skipped:,} | {size:,} bytes → {args.output}")

    meta = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "records": count,
        "skipped": skipped,
        "format": "jsonl (one raw Chotot API response per line, NOT flattened)",
        "source": f"dynamodb:{TABLE}._raw_json",
    }
    meta_path = args.output.replace(".jsonl", "_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    log.info(f"Meta: {meta_path}")

    if args.upload_s3:
        url = upload_s3(args.output)
        log.info(f"Public URL: {url}")


if __name__ == "__main__":
    main()
