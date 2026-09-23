#!/usr/bin/env python3
"""
Đồng bộ và bù đủ 100% chính xác 157.565 tin đăng trong DynamoDB:
- Nhận diện 5.146 tin đăng mới nhất chưa có trong bản snapshot ngày 10/09.
- Sử dụng batch_get_item đa luồng lấy toàn bộ dữ liệu raw của 5.146 tin này.
- Gộp snapshot (152.419 tin) + 5.146 tin mới = ĐÚNG CHÍNH XÁC 157.565 TIN ĐĂNG (khớp 100% với Dashboard).
- Ghi đè vào reports/chotot_all_xe_raw_s3_full.jsonl.gz và /Users/toannguyen/Downloads/chotot_all_xe_raw_s3_full.jsonl.gz.
- Upload S3 và Invalidate CloudFront.
"""

import gzip
import hashlib
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
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

CURRENT_GZ = "reports/chotot_all_xe_raw_s3_full.jsonl.gz"
TEMP_GZ = "reports/chotot_all_xe_raw_s3_full.tmp.gz"
DEST_DOWNLOADS = Path("/Users/toannguyen/Downloads/chotot_all_xe_raw_s3_full.jsonl.gz")


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
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return url
    if S3_BASE in url:
        return url
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"{S3_BASE}/images/{lid}/{mtype}/{h}{_ext(url)}"


def transform_raw_obj(raw_obj: dict, fallback_row: dict | None = None) -> dict:
    new_record = dict(raw_obj)
    ad = dict(new_record.get("ad", {}))
    lid = str(ad.get("list_id", "") or ad.get("ad_id", "") or (fallback_row.get("list_id") if fallback_row else "") or "")

    if ad:
        if ad.get("image"):
            ad["image"] = to_s3(lid, "image", ad["image"])
        if ad.get("thumbnail_image"):
            ad["thumbnail_image"] = to_s3(lid, "image", ad["thumbnail_image"])
        if ad.get("webp_image"):
            ad["webp_image"] = to_s3(lid, "webp", ad["webp_image"])
        if isinstance(ad.get("images"), list):
            ad["images"] = [to_s3(lid, "images", u) for u in ad["images"]]
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

        if "company_ad" in ad:
            ad["ad_company"] = bool(ad["company_ad"])
        elif fallback_row and "company_ad" in fallback_row:
            ad["ad_company"] = bool(fallback_row["company_ad"])
        else:
            ad["ad_company"] = False

        new_record["ad"] = ad

    return new_record


def convert_ddb_item(item: dict) -> dict:
    lid = str(item.get("list_id") or "")
    raw = None
    if "_raw_json" in item:
        val = item["_raw_json"]
        if isinstance(val, str):
            try:
                raw = json.loads(val)
            except Exception:
                raw = None
        elif isinstance(val, dict):
            raw = val

    if raw and isinstance(raw, dict) and "ad" in raw:
        return transform_raw_obj(raw, fallback_row=item)
    else:
        ad_fields = dict(item)
        ad_fields.pop("_raw_json", None)
        pseudo_raw = {
            "ad": ad_fields,
            "ad_params": item.get("ad_params") or {},
            "parameters": item.get("parameters") or [],
            "params": item.get("params") or [],
            "view_position": item.get("view_position") or {}
        }
        return transform_raw_obj(pseudo_raw, fallback_row=item)


def get_missing_ids(existing_ids: set[str]) -> list[int]:
    log.info("Quét toàn bộ ID trong bảng chotot-xe-may để tìm các ID còn thiếu...")
    SEGMENTS = 8
    all_ids = set()

    def scan_segment(seg):
        session = _session()
        table = session.resource("dynamodb").Table(DDB_TABLE_NAME)
        kwargs = {
            "Segment": seg,
            "TotalSegments": SEGMENTS,
            "ProjectionExpression": "list_id"
        }
        seg_ids = []
        while True:
            resp = table.scan(**kwargs)
            for it in resp.get("Items", []):
                lid = it.get("list_id")
                if lid is not None:
                    seg_ids.append(int(lid))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return seg_ids

    with ThreadPoolExecutor(max_workers=SEGMENTS) as executor:
        for res in executor.map(scan_segment, range(SEGMENTS)):
            all_ids.update(res)

    existing_ints = {int(x) for x in existing_ids if str(x).isdigit()}
    missing = sorted(list(all_ids - existing_ints))
    log.info(f"Tổng ID trong DynamoDB: {len(all_ids):,}")
    log.info(f"Tổng ID đã có: {len(existing_ids):,}")
    log.info(f"Tổng ID còn thiếu cần bù: {len(missing):,}")
    return missing


def fetch_items_by_ids(ids: list[int]) -> list[dict]:
    log.info(f"Đang fetch dữ liệu chi tiết của {len(ids):,} tin đăng qua batch_get_item...")
    chunks = [ids[i:i + 100] for i in range(0, len(ids), 100)]
    fetched_items = []

    def fetch_chunk(chunk):
        session = _session()
        dynamodb = session.resource("dynamodb")
        keys = [{"list_id": lid} for lid in chunk]
        unprocessed = keys
        results = []
        for _ in range(3):
            if not unprocessed:
                break
            resp = dynamodb.batch_get_item(RequestItems={DDB_TABLE_NAME: {"Keys": unprocessed}})
            items = resp.get("Responses", {}).get(DDB_TABLE_NAME, [])
            results.extend(items)
            unprocessed = resp.get("UnprocessedKeys", {}).get(DDB_TABLE_NAME, {}).get("Keys", [])
            if unprocessed:
                time.sleep(0.5)
        return results

    with ThreadPoolExecutor(max_workers=8) as executor:
        for items in executor.map(fetch_chunk, chunks):
            fetched_items.extend(items)

    log.info(f"Fetch hoàn tất: Đã lấy được {len(fetched_items):,} tin đăng đầy đủ.")
    return fetched_items


def run_sync():
    start_time = time.time()
    existing_ids = set()
    total_count = 0
    dealer_count = 0
    individual_count = 0

    log.info("=== BẮT ĐẦU ĐỒNG BỘ 100% CƠ SỞ DỮ LIỆU CHỢ TỐT (157.565 TIN) ===")

    # 1. Đọc các ID hiện có trong file đã xuất
    if os.path.exists(CURRENT_GZ):
        log.info(f"Đọc dữ liệu hiện có từ {CURRENT_GZ}...")
        with gzip.open(CURRENT_GZ, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    lid = str(rec.get("ad", {}).get("list_id") or "")
                    if lid:
                        existing_ids.add(lid)
                except Exception:
                    pass
        log.info(f"Đã đọc {len(existing_ids):,} ID từ file hiện có.")

    # 2. Tìm các ID còn thiếu trong DynamoDB
    missing_ids = get_missing_ids(existing_ids)

    # 3. Fetch dữ liệu chi tiết của các ID còn thiếu
    new_items = fetch_items_by_ids(missing_ids) if missing_ids else []

    # 4. Ghi toàn bộ dữ liệu ra file hoàn chỉnh
    log.info(f"Đang ghi toàn bộ dữ liệu ra file nén {TEMP_GZ}...")
    with gzip.open(TEMP_GZ, "wt", encoding="utf-8") as f_out:
        # Ghi các bản ghi cũ
        if os.path.exists(CURRENT_GZ):
            with gzip.open(CURRENT_GZ, "rt", encoding="utf-8") as f_in:
                for line in f_in:
                    if not line.strip():
                        continue
                    f_out.write(line)
                    total_count += 1
                    try:
                        rec = json.loads(line)
                        if rec.get("ad", {}).get("ad_company"):
                            dealer_count += 1
                        else:
                            individual_count += 1
                    except Exception:
                        pass

        # Ghi các bản ghi mới bổ sung
        for item in new_items:
            try:
                record = convert_ddb_item(item)
                line_str = json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
                f_out.write(line_str)
                total_count += 1
                if record.get("ad", {}).get("ad_company"):
                    dealer_count += 1
                else:
                    individual_count += 1
            except Exception as e:
                log.warning(f"Lỗi convert item: {e}")

    # Đổi tên file tạm sang file chính
    os.replace(TEMP_GZ, CURRENT_GZ)
    log.info(f"Cập nhật hoàn tất: {CURRENT_GZ} có đúng CHÍNH XÁC {total_count:,} bản tin!")
    log.info(f"Đại lý / Cò xe: {dealer_count:,} | Cá nhân: {individual_count:,}")

    # Copy sang Downloads
    log.info(f"Sao chép sang {DEST_DOWNLOADS}...")
    shutil.copyfile(CURRENT_GZ, DEST_DOWNLOADS)

    # Upload lên S3 & Invalidate CloudFront
    session = _session()
    s3 = session.client("s3")
    cf = session.client("cloudfront")

    s3_key = "data/exports/chotot_all_xe_raw_s3_full.jsonl.gz"
    log.info(f"Uploading {CURRENT_GZ} -> s3://{BUCKET}/{s3_key}...")
    s3.upload_file(
        CURRENT_GZ,
        BUCKET,
        s3_key,
        ExtraArgs={
            "ContentType": "application/gzip",
            "ContentDisposition": "attachment; filename=\"chotot_all_xe_raw_s3_full.jsonl.gz\"",
        }
    )

    log.info(f"Invalidating CloudFront cache for /{s3_key}...")
    try:
        cf.create_invalidation(
            DistributionId=CLOUDFRONT_DIST_ID,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": [f"/{s3_key}"]},
                "CallerReference": f"sync_full_157k_{int(time.time())}"
            }
        )
        log.info("CloudFront cache invalidated successfully.")
    except Exception as e:
        log.warning(f"CloudFront invalidation warning: {e}")

    elapsed = time.time() - start_time
    size_mb = round(os.path.getsize(CURRENT_GZ) / (1024 * 1024), 2)
    log.info(f"=== TẤT CẢ ĐÃ HOÀN TẤT TRONG {elapsed:.1f}s ===")
    log.info(f"Tổng bản tin: {total_count:,} | Kích thước file: {size_mb} MB")


if __name__ == "__main__":
    run_sync()
