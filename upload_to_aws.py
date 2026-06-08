"""
upload_to_aws.py
================
Standalone script to upload an existing chotot CSV to S3 and/or DynamoDB.

Usage:
    python upload_to_aws.py                              # upload today's CSV (auto-detect)
    python upload_to_aws.py --csv chotot_xe_may_full.csv
    python upload_to_aws.py --csv data.csv --no-dynamodb # S3 only
    python upload_to_aws.py --csv data.csv --no-s3       # DynamoDB only

Environment variables:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
    CHOTOT_S3_BUCKET   (default: chotot-xe-may-data)
    CHOTOT_S3_PREFIX   (default: xe-may)
    CHOTOT_DDB_TABLE   (default: chotot-xe-may)
"""

import argparse
import logging
import os
import sys
from datetime import date

import boto3
import pandas as pd
from botocore.exceptions import ClientError, NoCredentialsError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

S3_BUCKET = os.environ.get("CHOTOT_S3_BUCKET", "chotot-xe-may-data")
S3_PREFIX = os.environ.get("CHOTOT_S3_PREFIX", "xe-may")
DDB_TABLE = os.environ.get("CHOTOT_DDB_TABLE", "chotot-xe-may")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")


# ─── S3 ──────────────────────────────────────────────────────────────
def upload_s3(csv_path: str) -> str:
    key = f"{S3_PREFIX}/{date.today()}.csv"
    s3 = boto3.client("s3", region_name=AWS_REGION)
    size_mb = os.path.getsize(csv_path) / 1_048_576
    log.info(f"Uploading {csv_path} ({size_mb:.1f} MB) → s3://{S3_BUCKET}/{key}")
    try:
        s3.upload_file(csv_path, S3_BUCKET, key)
        log.info(f"S3 ✅  s3://{S3_BUCKET}/{key}")
        return key
    except ClientError as e:
        log.error(f"S3 upload failed: {e}")
        return ""


# ─── DynamoDB ────────────────────────────────────────────────────────
def _clean_record(r: dict) -> dict:
    """Convert types for DynamoDB (no None, no float for int fields)."""
    item = {}
    for k, v in r.items():
        if v is None or (isinstance(v, float) and str(v) == "nan"):
            continue
        if k in ("list_id", "ad_id", "gia_vnd", "km_da_di", "so_da_ban"):
            try:
                item[k] = int(v)
            except (ValueError, TypeError):
                pass  # skip unconvertable int fields
        elif k == "gia_trieu":
            try:
                item[k] = round(float(v), 2)
            except (ValueError, TypeError):
                pass
        elif k == "co_video":
            item[k] = bool(v)
        else:
            item[k] = str(v) if not isinstance(v, str) else v
    return item


def upsert_dynamodb(records: list) -> int:
    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(DDB_TABLE)
    written = 0
    log.info(f"Upserting {len(records):,} records → DynamoDB:{DDB_TABLE}")
    with table.batch_writer() as batch:
        for r in records:
            item = _clean_record(r)
            if "list_id" not in item:
                continue
            batch.put_item(Item=item)
            written += 1
    log.info(f"DynamoDB ✅  {written:,} items upserted")
    return written


# ─── MAIN ────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="chotot_xe_may_full.csv",
                   help="Path to CSV file (default: chotot_xe_may_full.csv)")
    p.add_argument("--no-s3", action="store_true", help="Skip S3 upload")
    p.add_argument("--no-dynamodb", action="store_true", help="Skip DynamoDB upsert")
    return p.parse_args()


def main():
    args = parse_args()

    if not os.path.exists(args.csv):
        log.error(f"File not found: {args.csv}")
        sys.exit(1)

    try:
        df = pd.read_csv(args.csv, encoding="utf-8-sig")
    except Exception as e:
        log.error(f"Cannot read CSV: {e}")
        sys.exit(1)

    log.info(f"Loaded {len(df):,} records from {args.csv}")

    try:
        if not args.no_s3:
            upload_s3(args.csv)

        if not args.no_dynamodb:
            records = df.to_dict("records")
            upsert_dynamodb(records)
    except NoCredentialsError:
        log.error("AWS credentials not found. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
        sys.exit(1)

    log.info("Done.")


if __name__ == "__main__":
    main()
