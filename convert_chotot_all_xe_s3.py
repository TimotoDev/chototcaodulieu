#!/usr/bin/env python3
"""
Convert /Users/toannguyen/Downloads/chotot_all_xe.json:
- Keep 100% of all 67 fields exactly as original:
    * subject, motorbikebrand, avatar, list_id, status, region_name, total_rating_for_seller
    * veh_ecom_can_buy_now, ward_name, full_name, price_string, state, ward_name_v3, date
    * average_rating_for_seller, is_zalo_show, motorbikemodel, orig_list_time, region
    * condition_ad, total_rating, latitude, phone, body, location, mileage_v2, job_tier
    * motorbikecapacity, contain_videos, account_id, region_name_v3, area_v2, average_rating
    * motorbiketype, veh_inspected, _raw_json, shop, area, webp_image, condition_ad_name
    * longitude, category, company_ad, ward, shop_alias, regdate, list_time, account_oid
    * seller_info, category_name, account_name, image, params, area_name, giveaway
    * ad_id, region_v2, date_added, image_thumbnails, number_of_images, images
    * protection_entitlement, price, is_sticky, sold_ads, type
- Add ad_company = bool(company_ad) for convenience.
- Replace CDN image URLs with S3 URLs:
    * image -> S3 image URL
    * thumbnail_image -> S3 image URL
    * webp_image -> S3 webp URL
    * images -> list of S3 images URLs
    * image_thumbnails -> list of {"image": S3 url, "thumbnail": S3 url}
- Output to:
    1. /Users/toannguyen/Downloads/chotot_all_xe_s3_images.json
    2. /Users/toannguyen/Downloads/chotot_all_xe_s3_images.json.gz
    3. /Users/toannguyen/Downloads/chotot_all_xe_s3_images_sample500.json
- Upload to S3 bucket chotot-dashboard-404850807717/data/exports/
"""

import gzip
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import PurePosixPath
from urllib.parse import urlparse

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
PROFILE = os.getenv("AWS_PROFILE", "harry")
BUCKET = os.getenv("IMAGE_BUCKET", "chotot-dashboard-404850807717")
S3_BASE = f"https://{BUCKET}.s3.{REGION}.amazonaws.com"
CLOUDFRONT_DIST_ID = "E245MGJPF7C1DW"


def _ext(u: str) -> str:
    ext = PurePosixPath(urlparse(u).path).suffix.lower()
    return ext if (ext and len(ext) <= 8) else ".jpg"


def to_s3(lid: str, mtype: str, url: str) -> str:
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return url
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"{S3_BASE}/images/{lid}/{mtype}/{h}{_ext(url)}"


def convert_item(item: dict) -> dict:
    lid = str(item.get("list_id", ""))
    new_item = dict(item)

    # 1. image
    if new_item.get("image"):
        new_item["image"] = to_s3(lid, "image", new_item["image"])

    # 2. thumbnail_image
    if new_item.get("thumbnail_image"):
        new_item["thumbnail_image"] = to_s3(lid, "image", new_item["thumbnail_image"])

    # 3. webp_image
    if new_item.get("webp_image"):
        new_item["webp_image"] = to_s3(lid, "webp", new_item["webp_image"])

    # 4. images list
    if isinstance(new_item.get("images"), list):
        new_item["images"] = [to_s3(lid, "images", u) for u in new_item["images"]]

    # 5. image_thumbnails
    if isinstance(new_item.get("image_thumbnails"), list):
        new_thumbs = []
        for th in new_item["image_thumbnails"]:
            if isinstance(th, dict):
                new_thumbs.append({
                    "image": to_s3(lid, "images", th.get("image", "")),
                    "thumbnail": to_s3(lid, "image", th.get("thumbnail", ""))
                })
            else:
                new_thumbs.append(th)
        new_item["image_thumbnails"] = new_thumbs

    # 6. ad_company field alongside company_ad
    if "company_ad" in new_item:
        new_item["ad_company"] = bool(new_item["company_ad"])

    return new_item


def process_file(src_path: str, out_json: str, out_gz: str, sample_json: str):
    log.info(f"Loading full JSON from {src_path}...")
    start_t = time.time()
    with open(src_path, "r", encoding="utf-8") as f_in:
        raw_items = json.load(f_in)

    total = len(raw_items)
    log.info(f"Loaded {total:,} items in {time.time() - start_t:.1f}s. Converting images to S3...")

    f_json = open(out_json, "w", encoding="utf-8")
    f_gz = gzip.open(out_gz, "wt", encoding="utf-8")

    f_json.write("[\n")
    f_gz.write("[\n")

    sample_items = []
    convert_start = time.time()

    for idx, item in enumerate(raw_items):
        converted = convert_item(item)
        if idx < 500:
            sample_items.append(converted)

        formatted = json.dumps(converted, ensure_ascii=False, indent=2)
        indented = "\n".join("  " + l for l in formatted.split("\n"))

        if idx > 0:
            f_json.write(",\n")
            f_gz.write(",\n")
        f_json.write(indented)
        f_gz.write(indented)

        if (idx + 1) % 5000 == 0:
            log.info(f"  Converted & written {idx + 1:,}/{total:,} items...")

    f_json.write("\n]\n")
    f_gz.write("\n]\n")

    f_json.close()
    f_gz.close()

    # Write sample 500
    with open(sample_json, "w", encoding="utf-8") as f_samp:
        json.dump(sample_items, f_samp, ensure_ascii=False, indent=2)

    el = time.time() - start_t
    json_mb = os.path.getsize(out_json) / (1024 * 1024)
    gz_mb = os.path.getsize(out_gz) / (1024 * 1024)
    samp_mb = os.path.getsize(sample_json) / (1024 * 1024)

    log.info(f"Finished {total:,} items in {el:.1f}s:")
    log.info(f"  JSON output:   {json_mb:.2f} MB ({out_json})")
    log.info(f"  GZIP output:   {gz_mb:.2f} MB ({out_gz})")
    log.info(f"  Sample 500:    {samp_mb:.2f} MB ({sample_json})")


def upload_to_s3(files: list[tuple[str, str, str]]):
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    s3 = session.client("s3")
    for local_path, s3_key, content_type in files:
        filename = os.path.basename(local_path)
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        log.info(f"Uploading {local_path} ({size_mb:.2f} MB) -> s3://{BUCKET}/{s3_key}...")
        extra_args = {
            "ContentType": content_type,
            "ContentDisposition": f'attachment; filename="{filename}"',
        }
        s3.upload_file(local_path, BUCKET, s3_key, ExtraArgs=extra_args)
        log.info(f"  Uploaded s3://{BUCKET}/{s3_key}")


def invalidate_cloudfront(paths: list[str]):
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    cf = session.client("cloudfront")
    ref = f"inv-{int(time.time())}"
    log.info(f"Invalidating CloudFront paths: {paths}")
    resp = cf.create_invalidation(
        DistributionId=CLOUDFRONT_DIST_ID,
        InvalidationBatch={
            "Paths": {"Quantity": len(paths), "Items": paths},
            "CallerReference": ref,
        },
    )
    log.info(f"Invalidation ID: {resp['Invalidation']['Id']}")


def main():
    src_path = "/Users/toannguyen/Downloads/chotot_all_xe.json"
    out_json = "/Users/toannguyen/Downloads/chotot_all_xe_s3_images.json"
    out_gz = "/Users/toannguyen/Downloads/chotot_all_xe_s3_images.json.gz"
    sample_json = "/Users/toannguyen/Downloads/chotot_all_xe_s3_images_sample500.json"

    process_file(src_path, out_json, out_gz, sample_json)

    files_to_upload = [
        (out_json, "data/exports/chotot_all_xe_s3_images.json", "application/json"),
        (out_gz, "data/exports/chotot_all_xe_s3_images.json.gz", "application/gzip"),
        (sample_json, "data/exports/chotot_all_xe_s3_images_sample500.json", "application/json"),
    ]
    upload_to_s3(files_to_upload)
    invalidate_cloudfront(["/data/exports/chotot_all_xe_s3_images*"])
    log.info("ALL DONE SUCCESSFULLY!")


if __name__ == "__main__":
    main()
