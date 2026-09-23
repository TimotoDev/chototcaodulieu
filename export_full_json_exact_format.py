#!/usr/bin/env python3
"""
Export FULL Chotot listings using the EXACT wrapper format as:
  /Users/toannguyen/Downloads/chotot_xe_may_s3_images_2026-09-10.json

Wrapper format:
{
  "exported_at": "...",
  "source_table": "chotot-xe-may",
  "image_bucket": "chotot-dashboard-404850807717",
  "s3_url_base": "https://chotot-dashboard-404850807717.s3.ap-southeast-1.amazonaws.com",
  "total_listings": N,
  "with_s3_images": N,
  "without_s3_images": 0,
  "bikes_in_state_table": N,
  "note": "thumbnail_s3 / images_s3 are HTTPS links to our public S3 copies (not cdn.chotot.com)",
  "items": [
    { ... FULL LISTING (NO TRUNCATION) ... }
  ]
}

Each item keeps:
  - All DynamoDB attributes (subject, price, body, params, seller_info, phone, lat/lon, ...)
  - Full original API payload in `raw` (with full `ad` 70+ fields)
  - Original CDN URLs (images, image, thumbnail_image, webp_image)
  - Added S3 URLs (images_s3, thumbnail_s3, image_s3, webp_image_s3, images_s3_count, has_s3_images)

Usage:
  AWS_PROFILE=harry python3 export_full_json_exact_format.py
  AWS_PROFILE=harry python3 export_full_json_exact_format.py --upload-s3
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import queue
import shutil
import threading
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

BRAND_MAP = {
    1: "Honda", 2: "Yamaha", 3: "Vespa/Piaggio", 4: "SYM", 5: "Suzuki",
    6: "Kawasaki", 7: "Ducati", 8: "BMW", 9: "Harley-Davidson", 10: "KTM",
    11: "Triumph", 12: "Royal Enfield", 13: "Benelli", 14: "Kymco",
    15: "Peugeot", 99: "Khác",
}
TYPE_MAP = {
    1: "Tay ga", 2: "Xe số", 3: "Xe côn tay",
    4: "Xe phân khối lớn", 5: "Xe điện", 6: "Xe 3 bánh",
}


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
        except BaseException as e:
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


def build_full_item(item: dict, s3_media: dict[str, list[str]] | None) -> dict:
    """Build full listing: no fields dropped, raw preserved, S3 links attached."""
    record: dict[str, Any] = dict(item)

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
        record["raw"] = raw_obj
        if isinstance(raw_obj, dict):
            ad = raw_obj.get("ad")
            if isinstance(ad, dict):
                for k, v in ad.items():
                    if k not in record:
                        record[k] = v
            for k in ("ad_params", "parameters", "params", "view_position"):
                if k in raw_obj and k not in record:
                    record[k] = raw_obj[k]

    list_id = _normalize_list_id(
        record.get("list_id") or (raw_obj or {}).get("ad", {}).get("list_id")
        if isinstance(raw_obj, dict) else None
    )
    if list_id:
        record["list_id"] = str(list_id)

    # Human-readable brand/type while preserving original IDs
    brand_id = record.get("motorbikebrand")
    try:
        brand_id_int = int(brand_id) if brand_id is not None else 0
        record["brand"] = BRAND_MAP.get(brand_id_int, record.get("brand", ""))
    except Exception:
        pass

    type_id = record.get("motorbiketype")
    try:
        type_id_int = int(type_id) if type_id is not None else 0
        record["type"] = TYPE_MAP.get(type_id_int, record.get("type", ""))
    except Exception:
        pass

    # Normalized year string
    reg = record.get("regdate")
    if reg is not None and not isinstance(reg, str):
        record["year"] = str(int(reg) if isinstance(reg, (int, float, Decimal)) and reg == int(reg) else reg)

    # Attach S3 images (keeps original CDN fields intact)
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

    record["thumbnail_s3"] = thumbnail_s3
    record["images_s3"] = images_s3
    record["images_s3_count"] = len(images_s3)
    record["has_s3_images"] = bool(images_s3 or thumbnail_s3)
    record["listing_url"] = (
        f"https://xe.chotot.com/mua-ban-xe-may/{list_id}" if list_id else record.get("listing_url", "")
    )

    return record


def export(out_json: str, only_with_s3: bool = True) -> dict:
    s3_by_id = load_s3_images_by_list_id()
    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)

    # Intermediate JSONL file to stream writes without memory blowup
    tmp_jsonl = out_json + ".tmp.jsonl"
    log.info(f"Scanning FULL source table ({SEGMENTS} segments) — all attributes...")
    total = with_s3 = without_s3 = 0

    with open(tmp_jsonl, "w", encoding="utf-8") as f:
        for item in _parallel_scan(SOURCE_TABLE, {}):
            lid = _normalize_list_id(item.get("list_id"))
            if not lid:
                continue
            row = build_full_item(item, s3_by_id.get(lid))
            has_s3 = row.get("has_s3_images", False)
            if has_s3:
                with_s3 += 1
            else:
                without_s3 += 1
                if only_with_s3:
                    continue
            f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
            total += 1
            if total % 10_000 == 0:
                log.info(f"  streamed {total:,}...")

    exported_at = datetime.now(timezone.utc).isoformat()
    log.info(f"Writing final JSON file with EXACT wrapper: {out_json} ...")

    wrapper_header = {
        "exported_at": exported_at,
        "source_table": SOURCE_TABLE,
        "image_bucket": BUCKET,
        "s3_url_base": S3_PUBLIC_BASE,
        "total_listings": total,
        "with_s3_images": with_s3,
        "without_s3_images": without_s3 if not only_with_s3 else 0,
        "bikes_in_state_table": len(s3_by_id),
        "note": "thumbnail_s3 / images_s3 are HTTPS links to our public S3 copies (not cdn.chotot.com)",
    }

    with open(out_json, "w", encoding="utf-8") as dst, open(tmp_jsonl, "r", encoding="utf-8") as src:
        dst.write("{\n")
        for k, v in wrapper_header.items():
            dst.write(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},\n")
        dst.write('  "items": [\n')
        first = True
        for line in src:
            line = line.strip()
            if not line:
                continue
            if not first:
                dst.write(",\n")
            dst.write("    ")
            dst.write(line)
            first = False
        dst.write("\n  ]\n}\n")

    try:
        os.remove(tmp_jsonl)
    except OSError:
        pass

    gz_path = out_json + ".gz"
    log.info(f"Compressing → {gz_path}")
    with open(out_json, "rb") as s_in, gzip.open(gz_path, "wb") as s_out:
        shutil.copyfileobj(s_in, s_out)

    size_json = os.path.getsize(out_json)
    size_gz = os.path.getsize(gz_path)
    log.info(
        f"Done: {total:,} listings | json={size_json/1e6:.1f}MB | gz={size_gz/1e6:.1f}MB"
    )

    return {
        **wrapper_header,
        "json_path": out_json,
        "gz_path": gz_path,
        "json_size_bytes": size_json,
        "gz_size_bytes": size_gz,
    }


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
        os.path.dirname(__file__), "reports", f"chotot_xe_may_full_s3_{today}.json"
    )
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output", default=default_out)
    p.add_argument("--include-without-s3", action="store_true", help="Include listings missing S3 images")
    p.add_argument("--upload-s3", action="store_true")
    args = p.parse_args()

    stats = export(args.output, only_with_s3=not args.include_without_s3)
    if args.upload_s3:
        stats["json_url"] = upload_s3(stats["json_path"], "application/json; charset=utf-8")
        stats["gz_url"] = upload_s3(stats["gz_path"], "application/gzip")
    print(json.dumps({k: v for k, v in stats.items() if k != "note"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
