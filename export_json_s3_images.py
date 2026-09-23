#!/usr/bin/env python3
"""
Export bike listings with S3 image URLs (not Chợ Tốt CDN).

Outputs:
  - reports/chotot_xe_may_s3_images_YYYY-MM-DD.jsonl  (1 bike / line, fast)
  - reports/chotot_xe_may_s3_images_YYYY-MM-DD.meta.json

Usage:
  AWS_PROFILE=harry python3 export_json_s3_images.py
  AWS_PROFILE=harry python3 export_json_s3_images.py --only-with-s3 --upload-s3
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


def _num(v: Any):
    if isinstance(v, Decimal):
        return int(v) if v == int(v) else float(v)
    return v


def _s3_url(key: str) -> str:
    return f"{S3_PUBLIC_BASE}/{key.lstrip('/')}"


def _parallel_scan(table_name: str, scan_kwargs: dict, segments: int = SEGMENTS):
    """Yield items via streaming parallel Segment scan with bounded memory."""
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
    log.info(f"Loading state table with {SEGMENTS} parallel segments...")
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


def fmt_bike(item: dict, s3_media: dict[str, list[str]] | None) -> dict:
    brand_id = int(_num(item.get("motorbikebrand") or 0) or 0)
    type_id = int(_num(item.get("motorbiketype") or 0) or 0)
    price = _num(item.get("price") or 0) or 0
    list_id = str(int(_num(item.get("list_id"))))
    year = item.get("regdate")
    if isinstance(year, Decimal):
        year = int(year) if year == int(year) else float(year)
    year = "" if year is None else str(year)

    images = list((s3_media or {}).get("images") or [])
    if not images:
        for k in ("image", "thumbnail", "webp"):
            images.extend((s3_media or {}).get(k) or [])

    thumbnail = None
    if s3_media:
        thumbnail = (
            (s3_media.get("thumbnail") or [None])[0]
            or (s3_media.get("image") or [None])[0]
            or (s3_media.get("webp") or [None])[0]
            or (images[0] if images else None)
        )

    return {
        "list_id": list_id,
        "subject": item.get("subject") or "",
        "price": price,
        "brand": BRAND_MAP.get(brand_id, ""),
        "type": TYPE_MAP.get(type_id, ""),
        "year": year,
        "mileage": _num(item.get("mileage_v2") or 0) or 0,
        "region": item.get("region_name") or item.get("region_name_v3") or "",
        "area": item.get("area_name") or "",
        "seller": item.get("account_name") or "",
        "date_added": item.get("date_added") or "",
        "listing_url": f"https://xe.chotot.com/mua-ban-xe-may/{list_id}",
        "thumbnail_s3": thumbnail,
        "images_s3": images,
        "images_s3_count": len(images),
        "has_s3_images": bool(images or thumbnail),
    }


def export(out_json: str, only_with_s3: bool = False) -> dict:
    s3_by_id = load_s3_images_by_list_id()

    log.info(f"Scanning source table with {SEGMENTS} parallel segments...")
    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    total = with_s3 = without_s3 = 0
    out_jsonl = out_json if out_json.endswith(".jsonl") else out_json.replace(".json", ".jsonl")
    if out_json.endswith(".jsonl"):
        out_json = out_jsonl.replace(".jsonl", ".json")

    scan_kwargs = {
        "ProjectionExpression": (
            "list_id, subject, price, motorbikebrand, motorbiketype, regdate, "
            "mileage_v2, region_name, region_name_v3, area_name, account_name, date_added"
        ),
    }

    # Stream JSONL first (fast), then wrap into one JSON file.
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for item in _parallel_scan(SOURCE_TABLE, scan_kwargs):
            try:
                lid = str(int(_num(item.get("list_id"))))
            except Exception:
                continue
            row = fmt_bike(item, s3_by_id.get(lid))
            if row["has_s3_images"]:
                with_s3 += 1
            else:
                without_s3 += 1
                if only_with_s3:
                    continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            total += 1
            if total % 25_000 == 0:
                log.info(f"  wrote {total:,}...")

    exported_at = datetime.now(timezone.utc).isoformat()
    log.info(f"Wrapping JSONL → single JSON file {out_json} ...")
    with open(out_jsonl, "r", encoding="utf-8") as src, open(out_json, "w", encoding="utf-8") as dst:
        header = {
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
        # Write header fields then items array without loading all rows in RAM
        dst.write("{\n")
        for i, (k, v) in enumerate(header.items()):
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

    meta = {
        **header,
        "format": "json+jsonl",
        "json_path": out_json,
        "jsonl_path": out_jsonl,
    }

    # Automatically compress to .gz (easier download, ~45MB vs 227MB)
    gz_path = out_json + ".gz"
    log.info(f"Compressing JSON → {gz_path} ...")
    with open(out_json, "rb") as s_in, gzip.open(gz_path, "wb") as s_out:
        shutil.copyfileobj(s_in, s_out)

    jsonl_gz_path = out_jsonl + ".gz"
    log.info(f"Compressing JSONL → {jsonl_gz_path} ...")
    with open(out_jsonl, "rb") as s_in, gzip.open(jsonl_gz_path, "wb") as s_out:
        shutil.copyfileobj(s_in, s_out)

    meta["gz_path"] = gz_path
    meta["jsonl_gz_path"] = jsonl_gz_path
    meta["gz_size_bytes"] = os.path.getsize(gz_path)
    meta["jsonl_gz_size_bytes"] = os.path.getsize(jsonl_gz_path)

    meta_path = out_json.replace(".json", ".meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    size = os.path.getsize(out_json)
    log.info(
        f"Wrote {out_json} | listings={total:,} with_s3={with_s3:,} "
        f"without_s3={without_s3:,} size={size/1e6:.1f} MB (gz={meta['gz_size_bytes']/1e6:.1f} MB)"
    )
    return {**meta, "size_bytes": size, "meta_path": meta_path}


def upload_s3(local_path: str) -> str:
    session = _session()
    key = f"data/exports/{os.path.basename(local_path)}"
    if local_path.endswith(".jsonl"):
        ctype = "application/x-ndjson; charset=utf-8"
    elif local_path.endswith(".gz"):
        ctype = "application/gzip"
    else:
        ctype = "application/json; charset=utf-8"
    session.client("s3").upload_file(
        local_path,
        BUCKET,
        key,
        ExtraArgs={"ContentType": ctype},
    )
    url = _s3_url(key)
    log.info(f"Uploaded → {url}")
    return url


def main():
    p = argparse.ArgumentParser()
    today = date.today().isoformat()
    default_out = os.path.join(
        os.path.dirname(__file__), "reports", f"chotot_xe_may_s3_images_{today}.json"
    )
    p.add_argument("-o", "--output", default=default_out)
    p.add_argument("--only-with-s3", action="store_true")
    p.add_argument("--upload-s3", action="store_true")
    args = p.parse_args()

    stats = export(args.output, only_with_s3=args.only_with_s3)
    if args.upload_s3:
        stats["s3_url"] = upload_s3(stats["json_path"])
        stats["gz_s3_url"] = upload_s3(stats["gz_path"])
        stats["jsonl_s3_url"] = upload_s3(stats["jsonl_path"])
        stats["jsonl_gz_s3_url"] = upload_s3(stats["jsonl_gz_path"])
        stats["meta_s3_url"] = upload_s3(stats["meta_path"])
    print(json.dumps({k: stats[k] for k in stats if k != "note"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
