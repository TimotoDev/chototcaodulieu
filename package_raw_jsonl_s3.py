#!/usr/bin/env python3
"""
Chuẩn hoá & Đóng gói dữ liệu Chợ Tốt dạng RAW JSONL theo yêu cầu của Ba:
- Mỗi dòng là 1 JSON nguyên bản của 1 bản tin quảng cáo nhận được từ Chợ Tốt (ad, ad_params, parameters, params, view_position).
- Giữ nguyên 100% tất cả các trường trong `ad` (body, phone, shop, seller_info, rating, thông số...)
- Bổ sung `ad_company` = bool(company_ad) để Ba lọc Đại lý / Cò xe (`True`) vs Cá nhân (`False`).
- Toàn bộ link hình ảnh của Chợ Tốt trong `ad` được tự động chuyển thành link S3:
    * ad.image -> S3 URL
    * ad.thumbnail_image -> S3 URL
    * ad.webp_image -> S3 URL
    * ad.images -> List các S3 URLs
    * ad.image_thumbnails -> List các {"image": S3 URL, "thumbnail": S3 URL}
- Xuất song song file .jsonl, file nén .jsonl.gz và sample 100 bản ghi.
- Upload lên S3 với header attachment để tự động tải về máy.
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
    """Chuyển đổi URL ảnh Chợ Tốt sang link S3 vĩnh viễn theo mã băm SHA1."""
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return url
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"{S3_BASE}/images/{lid}/{mtype}/{h}{_ext(url)}"


def transform_raw_record(raw_record: dict) -> dict:
    """
    Chuyển đổi 1 bản tin Chợ Tốt RAW:
    Giữ nguyên cấu trúc {'ad': {...}, 'ad_params': ..., 'parameters': ..., 'params': ..., 'view_position': ...}
    Thay thế các trường hình ảnh trong 'ad' bằng link S3.
    """
    new_record = dict(raw_record)
    ad = dict(new_record.get("ad", {}))
    lid = str(ad.get("list_id", "") or ad.get("ad_id", ""))

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

        # 6. Thêm trường ad_company chuẩn xác
        if "company_ad" in ad:
            ad["ad_company"] = bool(ad["company_ad"])

        new_record["ad"] = ad

    return new_record


def package_raw_file(src_path: str, out_jsonl: str, out_gz: str, sample_jsonl: str = None):
    log.info(f"Bắt đầu đóng gói file RAW từ: {src_path}")
    start_t = time.time()

    f_out = open(out_jsonl, "w", encoding="utf-8")
    f_gz = gzip.open(out_gz, "wt", encoding="utf-8")
    f_samp = open(sample_jsonl, "w", encoding="utf-8") if sample_jsonl else None

    count = 0
    with open(src_path, "r", encoding="utf-8") as f_in:
        for line in f_in:
            line_str = line.strip()
            if not line_str:
                continue
            raw_obj = json.loads(line_str)
            transformed = transform_raw_record(raw_obj)
            out_line = json.dumps(transformed, ensure_ascii=False) + "\n"

            f_out.write(out_line)
            f_gz.write(out_line)

            count += 1
            if f_samp and count <= 100:
                f_samp.write(out_line)

            if count % 5000 == 0:
                log.info(f"  Đã xử lý {count:,} tin đăng...")

    f_out.close()
    f_gz.close()
    if f_samp:
        f_samp.close()

    el = time.time() - start_t
    mb_raw = os.path.getsize(out_jsonl) / (1024 * 1024)
    mb_gz = os.path.getsize(out_gz) / (1024 * 1024)
    log.info(f"Hoàn thành đóng gói {count:,} tin đăng trong {el:.1f}s:")
    log.info(f"  JSONL nguyên bản: {mb_raw:.2f} MB ({out_jsonl})")
    log.info(f"  GZIP nén:         {mb_gz:.2f} MB ({out_gz})")
    if sample_jsonl:
        mb_samp = os.path.getsize(sample_jsonl) / (1024 * 1024)
        log.info(f"  Sample 100 tin:   {mb_samp:.2f} MB ({sample_jsonl})")


def upload_to_s3(files: list[tuple[str, str, str]]):
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    s3 = session.client("s3")
    for local_path, s3_key, content_type in files:
        filename = os.path.basename(local_path)
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        log.info(f"Đang upload {local_path} ({size_mb:.2f} MB) -> s3://{BUCKET}/{s3_key}...")
        extra_args = {
            "ContentType": content_type,
            "ContentDisposition": f'attachment; filename="{filename}"',
        }
        s3.upload_file(local_path, BUCKET, s3_key, ExtraArgs=extra_args)
        log.info(f"  Đã upload s3://{BUCKET}/{s3_key}")


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
    src_file = "/Users/toannguyen/Downloads/chotot_all_xe_raw.jsonl"
    out_jsonl = "/Users/toannguyen/Downloads/chotot_all_xe_raw_s3.jsonl"
    out_gz = "/Users/toannguyen/Downloads/chotot_all_xe_raw_s3.jsonl.gz"
    sample_jsonl = "/Users/toannguyen/Downloads/chotot_all_xe_raw_s3_sample100.jsonl"

    package_raw_file(src_file, out_jsonl, out_gz, sample_jsonl)

    files_to_upload = [
        (out_jsonl, "data/exports/chotot_all_xe_raw_s3.jsonl", "application/json"),
        (out_gz, "data/exports/chotot_all_xe_raw_s3.jsonl.gz", "application/gzip"),
        (sample_jsonl, "data/exports/chotot_all_xe_raw_s3_sample100.jsonl", "application/json"),
    ]
    upload_to_s3(files_to_upload)
    invalidate_cloudfront(["/data/exports/chotot_all_xe_raw_s3*"])
    log.info("HOÀN TẤT!")


if __name__ == "__main__":
    main()
