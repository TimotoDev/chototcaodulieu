#!/usr/bin/env python3
"""
Format and export Chợ Tốt Xe Máy listings to JSON format with:
- Exact wrapper format (exported_at, source_table, image_bucket, s3_url_base, total_listings, items)
- FULL fields including:
    * body (nội dung chi tiết của quảng cáo bán xe)
    * ad_company & company_ad (True = Cửa hàng/Đại lý/Dân buôn xe, False = Cá nhân bán)
    * seller_type ("Đại lý / Cò xe" vs "Cá nhân")
    * sold_ads, live_ads (số tin đã bán, số tin đang đăng của người bán)
    * seller_info (avatar, full_name, ratings...)
    * phone (số điện thoại liên hệ nếu có)
    * detail_address, ward, area, region, coordinates (latitude, longitude)
    * params (thông số kỹ thuật chi tiết của xe)
    * thumbnail_s3, images_s3, images_s3_count, has_s3_images
- Stream directly to disk in JSON and GZIP formats to keep memory low.
- Upload to S3 with Content-Disposition: attachment to force direct download in browsers.
- Invalidate CloudFront CDN cache.
"""

import gzip
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
PROFILE = os.getenv("AWS_PROFILE", "harry")
SOURCE_TABLE = os.getenv("CHOTOT_DDB_TABLE", "chotot-xe-may")
BUCKET = os.getenv("IMAGE_BUCKET", "chotot-dashboard-404850807717")
S3_PUBLIC_BASE = f"https://{BUCKET}.s3.{REGION}.amazonaws.com"
CLOUDFRONT_DIST_ID = "E3IXW4F9535NPP"
CDN_BASE = "https://d13854125r8y84.cloudfront.net"

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


def _clean_num(v: Any):
    if isinstance(v, Decimal):
        return int(v) if v == int(v) else float(v)
    return v


def format_full_item(row: dict) -> dict:
    """Format single record with all required rich fields."""
    seller_info = row.get("seller_info") or {}
    # ad_company: True = dealer / pro seller / store, False = individual seller
    ad_company = bool(row.get("company_ad"))
    
    sold_ads = row.get("sold_ads")
    if sold_ads is None:
        sold_ads = seller_info.get("sold_ads", 0)
    sold_ads = _clean_num(sold_ads)

    live_ads = seller_info.get("live_ads", 0)
    live_ads = _clean_num(live_ads)

    brand_id = _clean_num(row.get("motorbikebrand"))
    type_id = _clean_num(row.get("motorbiketype"))

    # If brand name is not in brand field, look up BRAND_MAP
    brand_name = row.get("brand") or BRAND_MAP.get(brand_id, "")
    type_name = TYPE_MAP.get(type_id, "") or (row.get("type") if row.get("type") != "s" else "")

    images_s3 = row.get("images_s3") or []
    thumbnail_s3 = row.get("thumbnail_s3") or ""

    item = {
        "list_id": str(row.get("list_id", "")),
        "subject": row.get("subject") or "",
        "body": row.get("body") or "",
        "ad_company": ad_company,
        "company_ad": ad_company,
        "seller_type": "Đại lý / Cò xe" if ad_company else "Cá nhân",
        "sold_ads": sold_ads,
        "live_ads": live_ads,
        "price": _clean_num(row.get("price") or 0),
        "price_string": row.get("price_string") or "",
        "brand": brand_name,
        "type": type_name,
        "model": str(row.get("motorbikemodel") or ""),
        "year": str(row.get("regdate") or row.get("year") or ""),
        "mileage": _clean_num(row.get("mileage_v2") or row.get("mileage") or 0),
        "condition": row.get("condition_ad_name") or "",
        "region": row.get("region_name") or "",
        "area": row.get("area_name") or "",
        "ward": row.get("ward_name") or "",
        "detail_address": row.get("detail_address") or "",
        "seller": row.get("account_name") or row.get("full_name") or "",
        "phone": row.get("phone") or "",
        "total_rating_for_seller": _clean_num(row.get("total_rating_for_seller") or 0),
        "average_rating_for_seller": _clean_num(row.get("average_rating_for_seller") or 0),
        "date_added": row.get("date_added") or "",
        "listing_url": row.get("listing_url") or f"https://xe.chotot.com/mua-ban-xe-may/{row.get('list_id')}",
        "thumbnail_s3": thumbnail_s3,
        "images_s3": images_s3,
        "images_s3_count": len(images_s3),
        "has_s3_images": bool(images_s3 or thumbnail_s3),
        "latitude": _clean_num(row.get("latitude")),
        "longitude": _clean_num(row.get("longitude")),
        "params": row.get("params") or [],
    }
    return item


def export_from_source(source_gz_path: str, out_json_path: str, out_gz_path: str, sample_path: str):
    """Read full records from source gzip and stream formatted JSON & GZIP."""
    now_iso = datetime.now(timezone.utc).isoformat()
    log.info(f"Starting format & export from {source_gz_path}")

    # First count total records
    log.info("Counting records...")
    total_listings = 0
    with gzip.open(source_gz_path, "rt", encoding="utf-8") as fin:
        for _ in fin:
            total_listings += 1
    log.info(f"Total listings to format: {total_listings:,}")

    # Stream write uncompressed JSON and compressed GZ simultaneously
    f_json = open(out_json_path, "w", encoding="utf-8")
    f_gz = gzip.open(out_gz_path, "wt", encoding="utf-8")

    header = (
        "{\n"
        f'  "exported_at": "{now_iso}",\n'
        f'  "source_table": "{SOURCE_TABLE}",\n'
        f'  "image_bucket": "{BUCKET}",\n'
        f'  "s3_url_base": "{S3_PUBLIC_BASE}",\n'
        f'  "total_listings": {total_listings},\n'
        '  "note": "Full data format with body (mô tả xe), ad_company (True=Đại lý/cò xe, False=Cá nhân), seller_type, sold_ads, live_ads, phone, params, và link ảnh S3",\n'
        '  "items": [\n'
    )
    f_json.write(header)
    f_gz.write(header)

    sample_items = []
    company_count = 0
    individual_count = 0
    has_body_count = 0

    log.info("Streaming formatted JSON items...")
    start_t = time.time()
    with gzip.open(source_gz_path, "rt", encoding="utf-8") as fin:
        for idx, line in enumerate(fin):
            row = json.loads(line)
            item = format_full_item(row)

            if item["ad_company"]:
                company_count += 1
            else:
                individual_count += 1
            if item["body"]:
                has_body_count += 1

            if idx < 500:
                sample_items.append(item)

            line_str = "    " + json.dumps(item, ensure_ascii=False)
            if idx < total_listings - 1:
                line_str += ",\n"
            else:
                line_str += "\n"

            f_json.write(line_str)
            f_gz.write(line_str)

            if (idx + 1) % 25000 == 0:
                log.info(f"  Processed {idx + 1:,}/{total_listings:,} records ({(idx + 1)/total_listings*100:.1f}%)")

    footer = "  ]\n}\n"
    f_json.write(footer)
    f_gz.write(footer)

    f_json.close()
    f_gz.close()

    el = time.time() - start_t
    json_size_mb = os.path.getsize(out_json_path) / (1024 * 1024)
    gz_size_mb = os.path.getsize(out_gz_path) / (1024 * 1024)

    log.info(f"Export finished in {el:.1f}s:")
    log.info(f"  JSON uncompressed: {json_size_mb:.2f} MB ({out_json_path})")
    log.info(f"  JSON gzipped:      {gz_size_mb:.2f} MB ({out_gz_path})")
    log.info(f"  has_body:          {has_body_count:,} ({has_body_count/total_listings*100:.1f}%)")
    log.info(f"  ad_company == True (Đại lý/cò xe): {company_count:,} ({company_count/total_listings*100:.1f}%)")
    log.info(f"  ad_company == False (Cá nhân):    {individual_count:,} ({individual_count/total_listings*100:.1f}%)")

    # Write sample 500
    sample_doc = {
        "exported_at": now_iso,
        "source_table": SOURCE_TABLE,
        "image_bucket": BUCKET,
        "s3_url_base": S3_PUBLIC_BASE,
        "total_listings": len(sample_items),
        "note": "Sample 500 listings with full body, ad_company, seller_type, sold_ads, phone, params, s3_images",
        "items": sample_items,
    }
    with open(sample_path, "w", encoding="utf-8") as f_sample:
        json.dump(sample_doc, f_sample, ensure_ascii=False, indent=2)
    log.info(f"  Sample 500 written: {os.path.getsize(sample_path)/(1024):.1f} KB ({sample_path})")


def upload_files_to_s3(files: list[tuple[str, str, str]]):
    """
    Upload files to S3 with attachment content-disposition.
    files: [(local_path, s3_key, content_type)]
    """
    s3 = _session().client("s3")
    for local_path, s3_key, content_type in files:
        filename = os.path.basename(local_path)
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        log.info(f"Uploading {local_path} ({size_mb:.2f} MB) -> s3://{BUCKET}/{s3_key}...")
        extra_args = {
            "ContentType": content_type,
            "ContentDisposition": f'attachment; filename="{filename}"',
        }
        if s3_key.endswith(".gz"):
            # For .gz files, Content-Type application/gzip ensures browsers save it directly
            extra_args["ContentType"] = "application/gzip"
        s3.upload_file(local_path, BUCKET, s3_key, ExtraArgs=extra_args)
        log.info(f"  Uploaded s3://{BUCKET}/{s3_key}")


def invalidate_cloudfront(paths: list[str]):
    """Invalidate CloudFront cache for updated paths."""
    cf = _session().client("cloudfront")
    caller_ref = f"inv-{int(time.time())}"
    log.info(f"Creating CloudFront invalidation for paths: {paths}")
    resp = cf.create_invalidation(
        DistributionId=CLOUDFRONT_DIST_ID,
        InvalidationBatch={
            "Paths": {"Quantity": len(paths), "Items": paths},
            "CallerReference": caller_ref,
        },
    )
    inv_id = resp["Invalidation"]["Id"]
    log.info(f"Invalidation created: {inv_id}")


def main():
    source_gz = "/Users/toannguyen/chototcaodulieu/reports/chotot_full_with_s3_2026-09-10.jsonl.gz"
    out_json = "/Users/toannguyen/chototcaodulieu/reports/chotot_xe_may_s3_images_2026-09-11.json"
    out_gz = "/Users/toannguyen/chototcaodulieu/reports/chotot_xe_may_s3_images_2026-09-11.json.gz"
    sample_path = "/Users/toannguyen/chototcaodulieu/reports/chotot_xe_may_s3_images_2026-09-11_sample500.json"

    export_from_source(source_gz, out_json, out_gz, sample_path)

    # Upload to S3
    files_to_upload = [
        (out_json, "data/exports/chotot_xe_may_s3_images_2026-09-11.json", "application/json"),
        (out_gz, "data/exports/chotot_xe_may_s3_images_2026-09-11.json.gz", "application/gzip"),
        (sample_path, "data/exports/chotot_xe_may_s3_images_2026-09-11_sample500.json", "application/json"),
    ]
    upload_files_to_s3(files_to_upload)

    # Invalidate CloudFront
    invalidate_cloudfront(["/data/exports/chotot_xe_may_s3_images_2026-09-11*"])
    log.info("ALL DONE!")


if __name__ == "__main__":
    main()
