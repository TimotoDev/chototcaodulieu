#!/usr/bin/env python3
"""
Theo dõi tiến độ image sync — báo cáo mỗi 5 giây.

Queue SQS: poll mỗi 5s (nhanh).
Bảng state + S3: refresh mỗi 60s (tránh scan 350k rows liên tục).

Usage:
    AWS_PROFILE=harry python3 monitor_image_sync.py
    AWS_PROFILE=harry python3 monitor_image_sync.py --interval 5
    AWS_PROFILE=harry python3 monitor_image_sync.py --once
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import boto3

REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
PROFILE = os.getenv("AWS_PROFILE", "harry")
BUCKET = os.getenv("CHOTOT_EXPORT_S3_BUCKET", "chotot-dashboard-404850807717")
QUEUE_NAME = "chotot-image-sync-queue"
DLQ_NAME = "chotot-image-sync-dlq"
STATE_TABLE = "chotot-image-sync-state"


def _session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def _queue_stats(sqs, name: str) -> tuple[int, int]:
    url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]), int(attrs["ApproximateNumberOfMessagesNotVisible"])


def _sync_state_counts(table) -> tuple[int, int]:
    done = error = 0
    kwargs = {"ProjectionExpression": "#s", "ExpressionAttributeNames": {"#s": "status"}}
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            st = item.get("status", "")
            if st == "done":
                done += 1
            elif st == "error":
                error += 1
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return done, error


def _s3_bike_count(s3) -> int:
    count = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="images/", Delimiter="/"):
        count += len(page.get("CommonPrefixes", []))
    return count


def print_report(cur: dict, prev: dict | None, started_at: float) -> None:
    elapsed = int(time.time() - started_at)
    total_q = cur["queue_wait"] + cur["queue_running"]
    status = "RUNNING" if total_q > 0 else "IDLE"

    parts = [
        f"[{cur['ts']}] {status}",
        f"queue: {cur['queue_wait']:,} wait + {cur['queue_running']:,} running",
        f"DLQ: {cur['dlq']:,}",
    ]

    if cur.get("sync_done") is not None:
        parts.append(f"synced: {cur['sync_done']:,} done, {cur['sync_error']:,} error")
    if cur.get("s3_bikes") is not None:
        parts.append(f"S3 bikes: {cur['s3_bikes']:,}")

    if prev:
        dq = (prev["queue_wait"] + prev["queue_running"]) - total_q
        parts.append(f"Δqueue: {dq:+,}")

        if cur.get("sync_done") is not None and prev.get("sync_done") is not None:
            dd = cur["sync_done"] - prev["sync_done"]
            de = cur["sync_error"] - prev["sync_error"]
            parts.append(f"Δsync: done +{dd:,}, error +{de:,}")

    parts.append(f"elapsed {elapsed // 60}m{elapsed % 60}s")
    print(" | ".join(parts), flush=True)


def main():
    p = argparse.ArgumentParser(description="Monitor Chotot image sync progress")
    p.add_argument("--interval", type=int, default=5, help="Seconds between reports (default: 5)")
    p.add_argument("--stats-interval", type=int, default=60, help="Seconds between full state/S3 refresh (default: 60)")
    p.add_argument("--once", action="store_true", help="Print one report and exit")
    p.add_argument("--count-s3", action="store_true", help="Include S3 bike folder count on stats refresh")
    args = p.parse_args()

    try:
        session = _session()
        sqs = session.client("sqs")
        s3 = session.client("s3")
        table = session.resource("dynamodb").Table(STATE_TABLE)
    except Exception as e:
        print(f"AWS init failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("=== Chotot Image Sync Monitor ===")
    print(f"Profile: {PROFILE} | Region: {REGION}")
    print(f"Queue: {QUEUE_NAME} | Interval: {args.interval}s | Stats refresh: {args.stats_interval}s")
    print("Ctrl+C to stop\n")

    started = time.time()
    prev = None
    last_stats_at = 0.0
    cached_stats: dict = {}

    while True:
        try:
            now = time.time()
            q_wait, q_run = _queue_stats(sqs, QUEUE_NAME)
            dlq_wait, dlq_run = _queue_stats(sqs, DLQ_NAME)

            if now - last_stats_at >= args.stats_interval or not cached_stats:
                print("  (refreshing state table stats...)", flush=True)
                done, err = _sync_state_counts(table)
                cached_stats = {"sync_done": done, "sync_error": err}
                if args.count_s3:
                    print("  (counting S3 bike folders...)", flush=True)
                    cached_stats["s3_bikes"] = _s3_bike_count(s3)
                last_stats_at = now

            cur = {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "queue_wait": q_wait,
                "queue_running": q_run,
                "dlq": dlq_wait + dlq_run,
                **cached_stats,
            }
            print_report(cur, prev, started)
            prev = cur

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"Error: {e}", flush=True)

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
