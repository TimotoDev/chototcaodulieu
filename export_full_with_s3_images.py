#!/usr/bin/env python3
"""
Export FULL Chotot listing records + S3 image URLs.

- Keeps every field from DynamoDB item AND embedded _raw_json (Chotot API detail).
- Does NOT flatten / drop fields.
- Adds images_s3 / thumbnail_s3 / ... from our S3 copies.
- Original CDN URLs stay in the record (image, images, thumbnail_image, webp_image, etc.).

Outputs (gzip recommended for download):
  reports/chotot_full_with_s3_YYYY-MM-DD.jsonl
  reports/chotot_full_with_s3_YYYY-MM-DD.jsonl.gz
  reports/chotot_full_with_s3_YYYY-MM-DD.meta.json

Usage:
  AWS_PROFILE=harry python3 export_full_with_s3_images.py
  AWS_PROFILE=harry python3 export_full_with_s3_images.py --upload-s3
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import shutil
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
PROFILE = os.getenv("AWS_PROFILE", "harry")
SOURCE_TABLE = os.getenv("CHOTOT_DDB_TABLE", "chotot-xe-may")
STATE_TABLE = os.getenv("STATE_TABLE", "chotot-image-sync-state")
BUCKET = os.getenv("IMAGE_BUCKET", "chotot-dashboard-404850807717")
S3_PUBLIC_BASE = f"https://{BUCKET}.s3.{REGION}.amazonaws.com"
SEGMENTS = int(os.getenv("DDB_SCAN_SEGMENTS", "8"))


def _session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def _json_default(obj: Any):
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _s3_url(key: str) -> str:
    return f"{S3_PUBLIC_BASE}/{key.lstrip('/')}"


def _parallel_scan(table_name: str, scan_kwargs: dict, segments: int = SEGMENTS):
    """Parallel scan that streams items via a queue (does not buffer whole table)."""
    import queue
    import threading

    session = _session()
    q: queue.Queue = queue.Queue(maxsize=5000)
    SENTINEL = object()
    errors: list[BaseException] = []

    def scan_segment(seg: int):
        try:
            table = session.resource("dynamodb").Table(table_name)
            kwargs = dict(scan_kwargs)
            kwargs["Segment"] = seg
            kwargs["TotalSegments"] = segments
            while True:
                resp = table.scan(**kwargs)
                for item in resp.get("Items", []):
                    q.put(item)
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
        finally:
            q.put(SENTINEL)

    threads = [threading.Thread(target=scan_segment, args=(s,), daemon=True) for s in range(segments)]
    for t in threads:
        t.start()

    finished = 0
    while finished < segments:
        item = q.get()
        if item is SENTINEL:
            finished += 1
            continue
        yield item

    for t in threads:
        t.join()
    if errors:
        raise errors[0]

def load_s3_images_by_list_id() -> dict[str, dict[str, list[str]]]:
    by_id: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    done = 0
    kwargs = {
        "ProjectionExpression": "s3_key, list_id, media_type, #s",
        "ExpressionAttributeNames": {"#s": "status"},
        "FilterExpression": Attr("status").eq("done"),
    }
    log.info(f"Loading S3 state with {SEGMENTS} segments...")
    for item in _parallel_scan(STATE_TABLE, kwargs):
        lid = str(item.get("list_id") or "").strip()
        key = item.get("s3_key") or ""
        if not lid or not key:
            continue
        media = item.get("media_type") or "images"
        url = _s3_url(key)
        bucket = by_id[lid][media]
        if url not in bucket:
            bucket.append(url)
        done += 1
        if done % 200_000 == 0:
            log.info(f"  state done_keys≈{done:,} bikes={len(by_id):,}")
    log.info(f"State loaded: done_keys≈{done:,} bikes_with_s3={len(by_id):,}")
    return {k: dict(v) for k, v in by_id.items()}


def _normalize_list_id(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return str(int(v if not isinstance(v, Decimal) else int(v)))
    except Exception:
        s = str(v).strip()
        return s or None


def build_record(item: dict, s3_media: dict[str, list[str]] | None) -> dict:
    """
    Full record:
      - all DynamoDB attributes (except raw string duplicated)
      - parsed _raw_json nested under raw / merged carefully
      - S3 image URLs added (CDN originals kept)
    """
    # Start from full DDB item (convert Decimals later via json default)
    record: dict[str, Any] = dict(item)

    # Parse embedded raw API payload — keep FULL structure
    raw = record.pop("_raw_json", None)
    raw_obj = None
    if raw is not None:
        if isinstance(raw, str):
            try:
                raw_obj = json.loads(raw)
            except json.JSONDecodeError:
                raw_obj = {"_unparsed_raw_json": raw}
        else:
            raw_obj = raw

    if raw_obj is not None:
        # Keep complete original API response
        record["raw"] = raw_obj
        # Also surface ad fields that might only exist in raw (without deleting DDB copies)
        if isinstance(raw_obj, dict):
            ad = raw_obj.get("ad")
            if isinstance(ad, dict):
                for k, v in ad.items():
                    # only fill missing top-level keys — never overwrite existing DDB values
                    if k not in record:
                        record[k] = v
            for k in ("ad_params", "parameters", "params", "view_position"):
                if k in raw_obj and k not in record:
                    record[k] = raw_obj[k]

    list_id = _normalize_list_id(record.get("list_id") or (raw_obj or {}).get("ad", {}).get("list_id") if isinstance(raw_obj, dict) else None)
    if list_id:
        record["list_id"] = int(list_id) if list_id.isdigit() else list_id

    # Attach S3 copies WITHOUT removing CDN fields
    media = s3_media or {}
    images_s3 = list(media.get("images") or [])
    if not images_s3:
        for k in ("image", "thumbnail", "webp"):
            images_s3.extend(media.get(k) or [])

    thumbnail_s3 = None
    if media:
        thumbnail_s3 = (
            (media.get("thumbnail") or [None])[0]
            or (media.get("image") or [None])[0]
            or (media.get("webp") or [None])[0]
            or (images_s3[0] if images_s3 else None)
        )

    record["images_s3"] = images_s3
    record["thumbnail_s3"] = thumbnail_s3
    record["image_s3"] = (media.get("image") or [None])[0] if media else None
    record["webp_image_s3"] = (media.get("webp") or [None])[0] if media else None
    record["images_s3_by_type"] = media
    record["has_s3_images"] = bool(images_s3 or thumbnail_s3)
    record["s3_url_base"] = S3_PUBLIC_BASE
    record["listing_url"] = (
        f"https://xe.chotot.com/mua-ban-xe-may/{list_id}" if list_id else record.get("listing_url")
    )

    return record


def export(out_jsonl: str) -> dict:
    s3_by_id = load_s3_images_by_list_id()
    os.makedirs(os.path.dirname(out_jsonl) or ".", exist_ok=True)

    log.info(f"Scanning FULL source table ({SEGMENTS} segments) — all attributes...")
    total = with_s3 = without_s3 = no_raw = 0

    # No ProjectionExpression → all attributes
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for item in _parallel_scan(SOURCE_TABLE, {}):
            lid = _normalize_list_id(item.get("list_id"))
            if not lid:
                continue
            if "_raw_json" not in item:
                no_raw += 1
            row = build_record(item, s3_by_id.get(lid))
            if row.get("has_s3_images"):
                with_s3 += 1
            else:
                without_s3 += 1
            f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
            total += 1
            if total % 10_000 == 0:
                log.info(f"  wrote {total:,}...")

    gz_path = out_jsonl + ".gz"
    log.info(f"Compressing → {gz_path}")
    with open(out_jsonl, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)

    meta = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_table": SOURCE_TABLE,
        "image_bucket": BUCKET,
        "s3_url_base": S3_PUBLIC_BASE,
        "format": "jsonl — one FULL listing per line (all DDB fields + raw Chotot API + images_s3)",
        "total_listings": total,
        "with_s3_images": with_s3,
        "without_s3_images": without_s3,
        "missing_raw_json": no_raw,
        "jsonl_path": out_jsonl,
        "gz_path": gz_path,
        "jsonl_size_bytes": os.path.getsize(out_jsonl),
        "gz_size_bytes": os.path.getsize(gz_path),
        "note": (
            "No field truncation. CDN URLs kept in image/images/thumbnail_image/webp_image. "
            "Permanent copies in images_s3/thumbnail_s3/image_s3/webp_image_s3."
        ),
    }
    meta_path = out_jsonl.replace(".jsonl", ".meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    log.info(
        f"Done: {total:,} listings | with_s3={with_s3:,} without_s3={without_s3:,} "
        f"| jsonl={meta['jsonl_size_bytes']/1e6:.1f}MB gz={meta['gz_size_bytes']/1e6:.1f}MB"
    )
    return {**meta, "meta_path": meta_path}


def upload_s3(local_path: str, content_type: str) -> str:
    key = f"data/exports/{os.path.basename(local_path)}"
    _session().client("s3").upload_file(
        local_path, BUCKET, key, ExtraArgs={"ContentType": content_type}
    )
    url = _s3_url(key)
    log.info(f"Uploaded → {url}")
    return url


def main():
    today = date.today().isoformat()
    default_out = os.path.join(
        os.path.dirname(__file__), "reports", f"chotot_full_with_s3_{today}.jsonl"
    )
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output", default=default_out)
    p.add_argument("--upload-s3", action="store_true")
    args = p.parse_args()

    stats = export(args.output)
    if args.upload_s3:
        stats["gz_url"] = upload_s3(stats["gz_path"], "application/gzip")
        stats["jsonl_url"] = upload_s3(stats["jsonl_path"], "application/x-ndjson; charset=utf-8")
        stats["meta_url"] = upload_s3(stats["meta_path"], "application/json; charset=utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "note"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
