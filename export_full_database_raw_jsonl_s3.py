#!/usr/bin/env python3
"""
Xuất TOÀN BỘ cơ sở dữ liệu Chợ Tốt (157.000+ bản tin) ra định dạng RAW JSONL:
- Mỗi dòng là 1 JSON nguyên bản của 1 bản tin quảng cáo nhận được từ Chợ Tốt:
    {"ad": {...}, "ad_params": {...}, "parameters": [...], "params": [...], "view_position": {...}}
- Tất cả các trường trong `ad` giữ nguyên 100% (body, phone, shop, seller_info, rating...).
- Thêm trường `ad_company` (bool) trong `ad` để phân biệt Cò xe / Đại lý (`True`) vs Cá nhân (`False`).
- Chuyển đổi toàn bộ link ảnh trong `ad` thành link S3 vĩnh viễn:
    * ad.image -> link S3
    * ad.thumbnail_image -> link S3
    * ad.webp_image -> link S3
    * ad.images -> danh sách link S3
    * ad.image_thumbnails -> danh sách {"image": link S3, "thumbnail": link S3}
- Tự động gộp dữ liệu snapshot đầy đủ + các tin đăng mới nhất từ DynamoDB.
- Xuất file nén .jsonl.gz (và sample 100 tin), upload lên S3 và copy vào Downloads cho Ba.
"""

import gzip
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from decimal import Decimal
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
PROFILE = os.getenv("AWS_PROFILE", "harry")
BUCKET = os.getenv("IMAGE_BUCKET", "chotot-dashboard-404850807717")
S3_BASE = f"https://{BUCKET}.s3.{REGION}.amazonaws.com"
CLOUDFRONT_DIST_ID = "E245MGJPF7C1DW"
DDB_TABLE_NAME = "chotot-xe-may"

BASE_SNAPSHOT = "reports/chotot_full_with_s3_2026-09-10.jsonl.gz"
OUT_GZ = "reports/chotot_all_xe_raw_s3_full.jsonl.gz"
OUT_SAMPLE = "reports/chotot_all_xe_raw_s3_sample100.jsonl"
OUT_META = "reports/chotot_all_xe_raw_s3_full.meta.json"
DOWNLOADS_DIR = Path("/Users/toannguyen/Downloads")


def _session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def _json_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _ext(u: str) -> str:
    ext = PurePosixPath(urlparse(u).path).suffix.lower()
    return ext if (ext and len(ext) <= 8) else ".jpg"


def to_s3(lid: str, mtype: str, url: str) -> str:
    """Chuyển đổi URL ảnh Chợ Tốt sang link S3 vĩnh viễn theo mã băm SHA1."""
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return url
    if S3_BASE in url:
        return url
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"{S3_BASE}/images/{lid}/{mtype}/{h}{_ext(url)}"


def transform_raw_obj(raw_obj: dict, fallback_row: dict | None = None) -> dict:
    """
    Chuyển đổi 1 đối tượng raw Chợ Tốt sang link S3 và thêm ad_company.
    """
    new_record = dict(raw_obj)
    ad = dict(new_record.get("ad", {}))
    lid = str(ad.get("list_id", "") or ad.get("ad_id", "") or (fallback_row.get("list_id") if fallback_row else "") or "")

    if ad:
        # 1. image
        if ad.get("image"):
            ad["image"] = to_s3(lid, "image", ad["image"])

        # 2. thumbnail_image
        if ad.get("thumbnail_image"):
            ad["thumbnail_image"] = to_s3(lid, "image", ad["thumbnail_image"])

        # 3. webp_image
        if ad.get("webp_image"):
            ad["webp_image"] = to_s3(lid, "webp", ad["webp_image"])

        # 4. images list
        if isinstance(ad.get("images"), list):
            ad["images"] = [to_s3(lid, "images", u) for u in ad["images"]]

        # 5. image_thumbnails
        if isinstance(ad.get("image_thumbnails"), list):
            new_thumbs = []
            for th in ad["image_thumbnails"]:
                if isinstance(th, dict):
                    new_thumbs.append({
                        "image": to_s3(lid, "images", th.get("image", "")),
                        "thumbnail": to_s3(lid, "image", th.get("thumbnail", ""))
                    })
                else:
                    new_thumbs.append(th)
            ad["image_thumbnails"] = new_thumbs

        # 6. Thêm trường ad_company chuẩn xác (True = Cò xe / Đại lý, False = Cá nhân)
        if "company_ad" in ad:
            ad["ad_company"] = bool(ad["company_ad"])
        elif fallback_row and "company_ad" in fallback_row:
            ad["ad_company"] = bool(fallback_row["company_ad"])
        else:
            ad["ad_company"] = False

        new_record["ad"] = ad

    return new_record


def convert_row(row: dict) -> tuple[str, dict]:
    """
    Nhận 1 row từ snapshot hoặc DynamoDB và chuyển thành chuẩn Raw JSONL.
    """
    lid = str(row.get("list_id", "") or "")
    raw = row.get("raw")
    if raw is None and "_raw_json" in row:
        val = row["_raw_json"]
        if isinstance(val, str):
            try:
                raw = json.loads(val)
            except Exception:
                raw = None
        elif isinstance(val, dict):
            raw = val

    if raw and isinstance(raw, dict) and "ad" in raw:
        transformed = transform_raw_obj(raw, fallback_row=row)
    else:
        # Fallback đối với các tin đăng cũ không lưu _raw_json
        ad_fields = dict(row)
        ad_fields.pop("raw", None)
        ad_fields.pop("_raw_json", None)
        ad_fields.pop("images_s3", None)
        ad_fields.pop("thumbnail_s3", None)
        ad_fields.pop("images_s3_by_type", None)
        ad_fields.pop("webp_image_s3", None)
        ad_fields.pop("image_s3", None)

        pseudo_raw = {
            "ad": ad_fields,
            "ad_params": row.get("ad_params") or {},
            "parameters": row.get("parameters") or [],
            "params": row.get("params") or [],
            "view_position": row.get("view_position") or {}
        }
        transformed = transform_raw_obj(pseudo_raw, fallback_row=row)

    ad_id = str(transformed.get("ad", {}).get("list_id") or lid)
    return ad_id, transformed


def fetch_new_items_from_dynamodb() -> list[dict]:
    """Truy vấn các tin đăng mới từ ngày 11/09/2026 đến nay qua GSI date-index."""
    log.info("Querying DynamoDB GSI date-index for recent records (2026-09-11 to today)...")
    session = _session()
    table = session.resource("dynamodb").Table(DDB_TABLE_NAME)
    new_items = []

    for day in range(11, 25):
        d_str = f"2026-09-{day:02d}"
        try:
            resp = table.query(
                IndexName="date-index",
                KeyConditionExpression="date_added = :d",
                ExpressionAttributeValues={":d": d_str}
            )
            items = resp.get("Items", [])
            if items:
                log.info(f"  {d_str}: {len(items)} items")
                new_items.extend(items)
        except Exception as e:
            log.warning(f"  Error querying {d_str}: {e}")

    log.info(f"Total new items fetched from DynamoDB: {len(new_items):,}")
    return new_items


def export_all():
    start_time = time.time()
    seen_ids = set()
    total_written = 0
    dealer_count = 0
    individual_count = 0

    os.makedirs("reports", exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=== BẮT ĐẦU ĐÓNG GÓI TOÀN BỘ CƠ SỞ DỮ LIỆU CHỢ TỐT SANG RAW JSONL ===")
    log.info(f"Source snapshot: {BASE_SNAPSHOT}")
    log.info(f"Output GZ: {OUT_GZ}")
    log.info(f"Output Sample: {OUT_SAMPLE}")

    with gzip.open(OUT_GZ, "wt", encoding="utf-8") as f_gz, \
         open(OUT_SAMPLE, "w", encoding="utf-8") as f_sample:

        # 1. Đọc từ file snapshot 152k record
        if os.path.exists(BASE_SNAPSHOT):
            log.info(f"Streaming from base snapshot: {BASE_SNAPSHOT}...")
            with gzip.open(BASE_SNAPSHOT, "rt", encoding="utf-8") as f_in:
                for idx, line in enumerate(f_in, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        lid, record = convert_row(row)
                        if lid in seen_ids:
                            continue
                        seen_ids.add(lid)

                        line_str = json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
                        f_gz.write(line_str)

                        if total_written < 100:
                            f_sample.write(line_str)

                        total_written += 1
                        if record.get("ad", {}).get("ad_company"):
                            dealer_count += 1
                        else:
                            individual_count += 1

                        if total_written % 20000 == 0:
                            log.info(f"  Đã ghi {total_written:,} records (Đại lý: {dealer_count:,}, Cá nhân: {individual_count:,})...")

                    except Exception as e:
                        if idx <= 5:
                            log.warning(f"Error parsing row {idx}: {e}")
                        continue
            log.info(f"Snapshot finished: {total_written:,} unique records written.")

        # 2. Bổ sung các bản tin mới nhất từ DynamoDB
        new_ddb_items = fetch_new_items_from_dynamodb()
        added_from_ddb = 0
        for item in new_ddb_items:
            try:
                lid, record = convert_row(item)
                if lid in seen_ids:
                    continue
                seen_ids.add(lid)

                line_str = json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
                f_gz.write(line_str)

                total_written += 1
                added_from_ddb += 1
                if record.get("ad", {}).get("ad_company"):
                    dealer_count += 1
                else:
                    individual_count += 1
            except Exception as e:
                log.warning(f"Error converting DDB item: {e}")

        log.info(f"Added {added_from_ddb:,} new records from live DynamoDB.")

    gz_size = os.path.getsize(OUT_GZ)
    sample_size = os.path.getsize(OUT_SAMPLE)
    elapsed = time.time() - start_time

    meta = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "format": "jsonl (mỗi dòng là 1 bản tin Chợ Tốt RAW chuẩn gốc: ad, ad_params, parameters, params, view_position)",
        "total_records": total_written,
        "dealer_records": dealer_count,
        "individual_records": individual_count,
        "image_source": "AWS S3 permanent storage",
        "s3_bucket": BUCKET,
        "gz_size_mb": round(gz_size / (1024 * 1024), 2),
        "duration_seconds": round(elapsed, 1),
    }
    with open(OUT_META, "w", encoding="utf-8") as f_meta:
        json.dump(meta, f_meta, indent=2, ensure_ascii=False)

    log.info(f"=== ĐÓNG GÓI HOÀN TẤT ===")
    log.info(f"Tổng bản tin: {total_written:,} (Đại lý: {dealer_count:,}, Cá nhân: {individual_count:,})")
    log.info(f"Dung lượng file nén .jsonl.gz: {meta['gz_size_mb']} MB (Thời gian xử lý: {elapsed:.1f}s)")

    # 3. Copy sang thư mục Downloads trên Mac
    dest_gz = DOWNLOADS_DIR / "chotot_all_xe_raw_s3_full.jsonl.gz"
    dest_sample = DOWNLOADS_DIR / "chotot_all_xe_raw_s3_sample100.jsonl"
    log.info(f"Sao chép file sang {dest_gz}...")
    shutil.copyfile(OUT_GZ, dest_gz)
    shutil.copyfile(OUT_SAMPLE, dest_sample)

    # 4. Upload lên S3 & Invalidate CloudFront
    upload_s3_and_invalidate()


def upload_s3_and_invalidate():
    session = _session()
    s3 = session.client("s3")
    cf = session.client("cloudfront")

    log.info("Uploading exports to S3...")
    uploads = [
        (
            OUT_GZ,
            "data/exports/chotot_all_xe_raw_s3_full.jsonl.gz",
            "application/gzip",
            "attachment; filename=\"chotot_all_xe_raw_s3_full.jsonl.gz\""
        ),
        (
            OUT_SAMPLE,
            "data/exports/chotot_all_xe_raw_s3_sample100.jsonl",
            "application/x-ndjson",
            "attachment; filename=\"chotot_all_xe_raw_s3_sample100.jsonl\""
        ),
        (
            OUT_META,
            "data/exports/chotot_all_xe_raw_s3_full.meta.json",
            "application/json",
            "inline"
        ),
    ]

    for local_path, s3_key, content_type, disposition in uploads:
        if os.path.exists(local_path):
            log.info(f"Uploading {local_path} -> s3://{BUCKET}/{s3_key}")
            s3.upload_file(
                local_path,
                BUCKET,
                s3_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "ContentDisposition": disposition,
                }
            )

    log.info(f"Invalidating CloudFront distribution {CLOUDFRONT_DIST_ID} for /data/exports/*...")
    try:
        cf.create_invalidation(
            DistributionId=CLOUDFRONT_DIST_ID,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": ["/data/exports/*"]},
                "CallerReference": f"export_full_raw_{int(time.time())}"
            }
        )
        log.info("CloudFront invalidation submitted successfully.")
    except Exception as e:
        log.warning(f"CloudFront invalidation error: {e}")


if __name__ == "__main__":
    export_all()
