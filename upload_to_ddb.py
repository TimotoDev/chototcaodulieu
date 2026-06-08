#!/usr/bin/env python3
"""
upload_to_ddb.py — Batch upload chotot_raw_details.jsonl to DynamoDB
Reads JSONL, adds date_added from list_time, batch-writes 25 items at a time.
Checkpoint file tracks progress so re-runs skip already-written items.
"""

import json
import os
import time
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

# ── Config ──────────────────────────────────────────────────────────────
JSONL_FILE    = "/Users/toannguyen/chototcaodulieu/.claude/worktrees/trusting-raman/chotot_raw_details.jsonl"
CHECKPOINT    = "/Users/toannguyen/chototcaodulieu/.claude/worktrees/trusting-raman/chotot_checkpoints/ddb_upload.checkpoint"
TABLE_NAME    = "chotot-xe-may"
REGION        = "ap-southeast-1"
PROFILE       = "harry"
BATCH_SIZE    = 25
MAX_RETRIES   = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def load_checkpoint() -> set:
    """Return set of list_ids already written."""
    os.makedirs(os.path.dirname(CHECKPOINT), exist_ok=True)
    if not os.path.exists(CHECKPOINT):
        return set()
    done = set()
    with open(CHECKPOINT) as f:
        for line in f:
            line = line.strip()
            if line:
                done.add(int(line))
    log.info(f"Checkpoint: {len(done):,} already written")
    return done


def save_checkpoint(list_ids: list):
    """Append newly written list_ids to checkpoint file."""
    with open(CHECKPOINT, "a") as f:
        for lid in list_ids:
            f.write(f"{lid}\n")


def make_item(record: dict, serializer: TypeSerializer):
    """Convert a raw JSONL record to DynamoDB item format."""
    ad = record.get("ad", record)
    list_id = ad.get("list_id") or ad.get("ad_id")
    if not list_id:
        return None

    item = {}
    for k, v in ad.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        safe_k = str(k).replace(".", "_")
        try:
            item[safe_k] = serializer.serialize(v)
        except Exception:
            item[safe_k] = serializer.serialize(str(v))

    # Ensure list_id is stored as Number
    item["list_id"] = serializer.serialize(int(list_id))

    # Add date_added from list_time for GSI
    list_time_ms = ad.get("list_time", 0)
    if list_time_ms:
        try:
            date_added = datetime.fromtimestamp(
                int(list_time_ms) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d")
            item["date_added"] = serializer.serialize(date_added)
        except Exception:
            pass

    # Store full raw JSON
    try:
        item["_raw_json"] = serializer.serialize(
            json.dumps(record, ensure_ascii=False)
        )
    except Exception:
        pass

    return item


def write_batch(client, table: str, batch: list) -> tuple[int, list]:
    """Write a batch of 25 PutRequests, return (written_count, unprocessed_items)."""
    retries = 0
    remaining = batch[:]
    written = 0

    while remaining and retries < MAX_RETRIES:
        try:
            resp = client.batch_write_item(RequestItems={table: remaining})
            written += len(remaining)
            unprocessed = resp.get("UnprocessedItems", {}).get(table, [])
            if not unprocessed:
                break
            # Retry unprocessed items with exponential backoff
            retries += 1
            wait = 2 ** retries
            log.warning(f"  {len(unprocessed)} unprocessed items, retry {retries}/{MAX_RETRIES} (wait {wait}s)")
            time.sleep(wait)
            written -= len(unprocessed)
            remaining = unprocessed
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded"):
                retries += 1
                wait = 2 ** retries
                log.warning(f"  Throttle ({code}), retry {retries}/{MAX_RETRIES} (wait {wait}s)")
                time.sleep(wait)
            else:
                log.error(f"  DDB error: {e}")
                break

    return written, remaining


def main():
    # Set up boto3 session with profile
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    client = session.client("dynamodb")
    serializer = TypeSerializer()

    done_ids = load_checkpoint()

    total_records = 0
    total_written = 0
    total_skipped = 0
    batch_requests = []
    batch_list_ids = []

    log.info(f"Reading: {JSONL_FILE}")

    with open(JSONL_FILE, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                log.warning(f"  Line {line_no}: JSON error — {e}")
                continue

            ad = record.get("ad", record)
            list_id = int(ad.get("list_id") or ad.get("ad_id") or 0)
            if not list_id:
                continue

            total_records += 1

            # Skip if already written
            if list_id in done_ids:
                total_skipped += 1
                continue

            item = make_item(record, serializer)
            if not item:
                continue

            batch_requests.append({"PutRequest": {"Item": item}})
            batch_list_ids.append(list_id)

            # Write in batches of 25
            if len(batch_requests) == BATCH_SIZE:
                written, _ = write_batch(client, TABLE_NAME, batch_requests)
                total_written += written
                save_checkpoint(batch_list_ids)
                done_ids.update(batch_list_ids)
                batch_requests = []
                batch_list_ids = []

                if total_written % 1000 < BATCH_SIZE:
                    log.info(f"  Progress: {total_records:,} read | {total_written:,} written | {total_skipped:,} skipped")

    # Flush remaining
    if batch_requests:
        written, _ = write_batch(client, TABLE_NAME, batch_requests)
        total_written += written
        save_checkpoint(batch_list_ids)

    log.info(f"Done: {total_records:,} records | {total_written:,} written | {total_skipped:,} skipped")


if __name__ == "__main__":
    main()
