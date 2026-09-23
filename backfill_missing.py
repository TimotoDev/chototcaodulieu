#!/usr/bin/env python3
"""
Backfill missing daily scrape days → DynamoDB + S3 CSV.

Usage:
    AWS_PROFILE=harry python3 backfill_missing.py              # all gaps
    AWS_PROFILE=harry python3 backfill_missing.py 2026-03-29   # one day
    AWS_PROFILE=harry python3 backfill_missing.py --dry-run
"""

import argparse
import logging
import os
import re
import sys
from datetime import date, timedelta

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

S3_BUCKET = os.getenv("CHOTOT_EXPORT_S3_BUCKET", "chotot-dashboard-404850807717")
S3_PREFIX = os.getenv("CHOTOT_EXPORT_S3_PREFIX", "data/daily")
PIPELINE_START = date(2026, 3, 24)


def _s3_daily_dates() -> set[str]:
    s3 = boto3.client("s3")
    have = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/"
    ):
        for obj in page.get("Contents", []):
            m = re.search(r"chotot_daily_(\d{4}-\d{2}-\d{2})\.csv", obj["Key"])
            if m:
                have.add(m.group(1))
    return have


def _missing_days(until: date | None = None) -> list[str]:
    until = until or (date.today() - timedelta(days=1))
    have = _s3_daily_dates()
    missing = []
    d = PIPELINE_START
    while d <= until:
        ds = d.isoformat()
        if ds not in have:
            missing.append(ds)
        d += timedelta(days=1)
    return missing


def _backfill_one(day_str: str, dry_run: bool) -> dict:
    from chotot import scrape_day, upsert_to_dynamodb
    from lambda_handler import _export_daily_csv_to_s3

    log.info(f"=== Backfill {day_str} ===")
    records = scrape_day(day_str)
    log.info(f"Scraped {len(records)} records for {day_str}")

    if dry_run or not records:
        return {"day": day_str, "scraped": len(records), "written": 0, "csv_rows": 0}

    written = upsert_to_dynamodb(records)
    csv_rows, csv_key = _export_daily_csv_to_s3(records, day_str)
    log.info(f"DDB={written} CSV={csv_rows} → s3://{S3_BUCKET}/{csv_key}")
    return {"day": day_str, "scraped": len(records), "written": written, "csv_rows": csv_rows, "csv_key": csv_key}


def main():
    p = argparse.ArgumentParser(description="Backfill missing Chotot daily scrape days")
    p.add_argument("days", nargs="*", help="Specific YYYY-MM-DD days (default: auto-detect gaps)")
    p.add_argument("--dry-run", action="store_true", help="Scrape only, no DDB/S3 writes")
    args = p.parse_args()

    if args.days:
        targets = args.days
    else:
        targets = _missing_days()
        log.info(f"Missing days detected: {targets or '(none)'}")

    if not targets:
        print("No missing days to backfill.")
        return

    results = []
    for day_str in targets:
        try:
            results.append(_backfill_one(day_str, args.dry_run))
        except Exception as e:
            log.error(f"Failed {day_str}: {e}")
            results.append({"day": day_str, "error": str(e)})

    print("\n=== Backfill summary ===")
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
