#!/usr/bin/env python3
"""Sync Chotot images to S3 with resumable checkpoint.

Features:
- Reads media URLs from DynamoDB (or local JSON export)
- Uploads to S3 path: images/<list_id>/<media_type>/<sha1(url)><ext>
- Resume-safe using local SQLite state (uploaded/error history)
- Batch mode via --stop-after to avoid long uninterrupted runs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
import requests


MediaEntry = Tuple[str, str, str, str]  # list_id, media_type, url, s3_key


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync images to S3 with checkpoint/resume")
    p.add_argument("--profile", default=os.getenv("AWS_PROFILE", "harry"))
    p.add_argument("--region", default=os.getenv("AWS_REGION", "ap-southeast-1"))
    p.add_argument("--bucket", default="chotot-dashboard-404850807717")
    p.add_argument("--prefix", default="images")
    p.add_argument("--table", default="chotot-xe-may")
    p.add_argument("--source", choices=["dynamodb", "json"], default="dynamodb")
    p.add_argument("--json-path", default="chotot_all_xe.json")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--stop-after", type=int, default=0, help="Stop after N new URLs queued (0=all)")
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--sqlite", default="image_sync_state.db")
    p.add_argument("--storage-class", default="STANDARD")
    p.add_argument("--no-thumbnails", action="store_true", help="Skip thumbnail_image")
    p.add_argument("--no-webp", action="store_true", help="Skip webp_image")
    p.add_argument("--no-image-field", action="store_true", help="Skip single image field")
    p.add_argument("--include-image-thumbnails", action="store_true", default=False)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uploaded (
            s3_key TEXT PRIMARY KEY,
            list_id TEXT,
            media_type TEXT,
            url TEXT,
            size_bytes INTEGER,
            uploaded_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS errors (
            s3_key TEXT PRIMARY KEY,
            list_id TEXT,
            media_type TEXT,
            url TEXT,
            attempts INTEGER,
            last_error TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()


def load_uploaded_keys(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT s3_key FROM uploaded")
    return {r[0] for r in cur.fetchall()}


def load_error_attempts(conn: sqlite3.Connection) -> Dict[str, int]:
    cur = conn.execute("SELECT s3_key, attempts FROM errors")
    return {r[0]: int(r[1]) for r in cur.fetchall()}


def upsert_error(conn: sqlite3.Connection, entry: MediaEntry, attempts: int, err: str) -> None:
    list_id, media_type, url, s3_key = entry
    conn.execute(
        """
        INSERT INTO errors(s3_key, list_id, media_type, url, attempts, last_error, updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(s3_key) DO UPDATE SET
          attempts=excluded.attempts,
          last_error=excluded.last_error,
          updated_at=excluded.updated_at
        """,
        (s3_key, list_id, media_type, url, attempts, err[:500], now_iso()),
    )


def mark_uploaded(conn: sqlite3.Connection, entry: MediaEntry, size_bytes: int) -> None:
    list_id, media_type, url, s3_key = entry
    conn.execute(
        """
        INSERT OR REPLACE INTO uploaded(s3_key, list_id, media_type, url, size_bytes, uploaded_at)
        VALUES(?,?,?,?,?,?)
        """,
        (s3_key, list_id, media_type, url, int(size_bytes), now_iso()),
    )
    conn.execute("DELETE FROM errors WHERE s3_key=?", (s3_key,))


def get_ext(url: str) -> str:
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext and len(ext) <= 8 and all(c.isalnum() or c == "." for c in ext):
        return ext
    return ".jpg"


def build_key(prefix: str, list_id: str, media_type: str, url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    ext = get_ext(url)
    return f"{prefix.rstrip('/')}/{list_id}/{media_type}/{h}{ext}"


def normalize_list_id(v) -> Optional[str]:
    if v is None:
        return None
    try:
        return str(int(v))
    except Exception:
        s = str(v).strip()
        return s or None


def extract_media_from_ad(ad: dict, args: argparse.Namespace) -> List[Tuple[str, str]]:
    urls: List[Tuple[str, str]] = []

    images = ad.get("images")
    if isinstance(images, list):
        for u in images:
            if isinstance(u, str) and u.startswith("http"):
                urls.append(("images", u))

    if not args.no_image_field:
        u = ad.get("image")
        if isinstance(u, str) and u.startswith("http"):
            urls.append(("image", u))

    if not args.no_thumbnails:
        u = ad.get("thumbnail_image")
        if isinstance(u, str) and u.startswith("http"):
            urls.append(("thumbnail", u))

    if not args.no_webp:
        u = ad.get("webp_image")
        if isinstance(u, str) and u.startswith("http"):
            urls.append(("webp", u))

    if args.include_image_thumbnails:
        thumbs = ad.get("image_thumbnails")
        if isinstance(thumbs, list):
            for u in thumbs:
                if isinstance(u, str) and u.startswith("http"):
                    urls.append(("thumbs", u))

    # per-record dedupe by url
    seen = set()
    uniq = []
    for media_type, u in urls:
        if u in seen:
            continue
        seen.add(u)
        uniq.append((media_type, u))
    return uniq


def iter_records_from_dynamodb(ddb_table) -> Iterator[dict]:
    kwargs = {
        "ProjectionExpression": "list_id, images, thumbnail_image, webp_image, image, image_thumbnails"
    }
    while True:
        resp = ddb_table.scan(**kwargs)
        for item in resp.get("Items", []):
            yield item
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek


def iter_records_from_json(path: str) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for row in data:
        # row can already contain flattened fields from ad
        yield row


def build_media_entries(records: Iterable[dict], args: argparse.Namespace) -> Iterator[MediaEntry]:
    for rec in records:
        ad = rec.get("ad") if isinstance(rec.get("ad"), dict) else rec
        list_id = normalize_list_id(ad.get("list_id") if isinstance(ad, dict) else rec.get("list_id"))
        if not list_id:
            continue

        for media_type, url in extract_media_from_ad(ad, args):
            key = build_key(args.prefix, list_id, media_type, url)
            yield (list_id, media_type, url, key)


_tls = threading.local()


def get_http_session() -> requests.Session:
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "chotot-image-sync/1.0"})
        _tls.session = s
    return s


def get_s3_client(profile: str, region: str):
    c = getattr(_tls, "s3", None)
    if c is None:
        sess = boto3.Session(profile_name=profile, region_name=region)
        c = sess.client("s3")
        _tls.s3 = c
    return c


def upload_one(entry: MediaEntry, args: argparse.Namespace) -> Tuple[bool, str, int]:
    _, _, url, s3_key = entry
    if args.dry_run:
        return True, "dry-run", 0

    sess = get_http_session()
    s3 = get_s3_client(args.profile, args.region)

    r = sess.get(url, timeout=(8, 35), stream=True)
    if r.status_code != 200:
        return False, f"HTTP_{r.status_code}", 0

    body = r.content
    content_type = r.headers.get("Content-Type", "application/octet-stream")

    s3.put_object(
        Bucket=args.bucket,
        Key=s3_key,
        Body=body,
        ContentType=content_type,
        StorageClass=args.storage_class,
    )
    return True, "ok", len(body)


def process_batch(
    batch: List[MediaEntry],
    args: argparse.Namespace,
    conn: sqlite3.Connection,
    uploaded_keys: set[str],
    error_attempts: Dict[str, int],
) -> Tuple[int, int, int]:
    ok = 0
    fail = 0
    skip = 0

    futures = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for entry in batch:
            s3_key = entry[3]
            if s3_key in uploaded_keys:
                skip += 1
                continue
            attempts = error_attempts.get(s3_key, 0)
            if attempts >= args.max_retries:
                skip += 1
                continue
            futures[ex.submit(upload_one, entry, args)] = entry

        for fut in as_completed(futures):
            entry = futures[fut]
            s3_key = entry[3]
            attempts = error_attempts.get(s3_key, 0)
            try:
                success, msg, size = fut.result()
            except Exception as e:  # noqa: BLE001
                success, msg, size = False, str(e), 0

            if success:
                mark_uploaded(conn, entry, size)
                uploaded_keys.add(s3_key)
                error_attempts.pop(s3_key, None)
                ok += 1
            else:
                attempts += 1
                error_attempts[s3_key] = attempts
                upsert_error(conn, entry, attempts, msg)
                fail += 1

    conn.commit()
    return ok, fail, skip


def main() -> None:
    args = parse_args()

    db_path = os.path.abspath(args.sqlite)
    conn = sqlite3.connect(db_path)
    ensure_db(conn)

    uploaded_keys = load_uploaded_keys(conn)
    error_attempts = load_error_attempts(conn)

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    if args.source == "dynamodb":
        ddb = sess.resource("dynamodb")
        table = ddb.Table(args.table)
        records = iter_records_from_dynamodb(table)
    else:
        records = iter_records_from_json(args.json_path)

    scheduled = 0
    processed = 0
    ok_total = 0
    fail_total = 0
    skip_total = 0
    seen_keys_in_run = set()

    started = time.time()
    batch: List[MediaEntry] = []

    for entry in build_media_entries(records, args):
        _, _, _, s3_key = entry

        if s3_key in seen_keys_in_run:
            continue
        seen_keys_in_run.add(s3_key)

        if s3_key in uploaded_keys:
            skip_total += 1
            continue

        batch.append(entry)
        scheduled += 1

        if args.stop_after and scheduled >= args.stop_after:
            break

        if len(batch) >= args.batch_size:
            ok, fail, skip = process_batch(batch, args, conn, uploaded_keys, error_attempts)
            processed += len(batch)
            ok_total += ok
            fail_total += fail
            skip_total += skip
            elapsed = time.time() - started
            rate = ok_total / elapsed if elapsed > 0 else 0
            print(
                f"[progress] queued={scheduled} processed={processed} ok={ok_total} "
                f"fail={fail_total} skip={skip_total} rate_ok={rate:.2f}/s"
            )
            batch = []

    if batch:
        ok, fail, skip = process_batch(batch, args, conn, uploaded_keys, error_attempts)
        processed += len(batch)
        ok_total += ok
        fail_total += fail
        skip_total += skip

    elapsed = time.time() - started
    print("=" * 72)
    print(f"source={args.source} bucket=s3://{args.bucket}/{args.prefix}/")
    print(f"scheduled_new={scheduled} processed={processed}")
    print(f"uploaded_ok={ok_total} failed={fail_total} skipped={skip_total}")
    print(f"elapsed_sec={elapsed:.1f}")
    if ok_total > 0:
        print(f"avg_ok_per_sec={ok_total/elapsed:.2f}")
    print(f"state_db={db_path}")
    print("resume_hint=rerun same command; it will skip already uploaded keys")


if __name__ == "__main__":
    main()
