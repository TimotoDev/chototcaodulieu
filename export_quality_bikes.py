#!/usr/bin/env python3
"""
Lấy N xe từ Chotot — chỉ giữ tin còn sống + đủ chất lượng, gom 1 file raw.

Tiêu chí "còn sống":
  - Detail API trả 200 (không 404)
  - status == active, state == accepted

Tiêu chí "chất lượng":
  - Có giá hợp lệ (1tr – 500tr VND)
  - Có tiêu đề >= 8 ký tự
  - Có ít nhất 1 ảnh (images hoặc thumbnail)
  - Có khu vực + người bán
  - Có list_time

Usage:
    python3 export_quality_bikes.py --limit 500
    python3 export_quality_bikes.py --limit 500 -o reports/chotot_quality_500.json
"""

import argparse
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from chotot import _make_session, _fetch_listing_page, _fetch_detail_one

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))
REPORT_DIR = os.path.join(os.path.dirname(__file__), "reports")

MIN_PRICE = 1_000_000
MAX_PRICE = 500_000_000
MIN_SUBJECT_LEN = 8
WORKERS = 25
PAGE_DELAY = 0.12


def _ad(record: dict) -> dict:
    return record.get("ad", record) if isinstance(record, dict) else {}


def is_alive(record: dict) -> bool:
    ad = _ad(record)
    return ad.get("status") == "active" and ad.get("state") == "accepted"


def is_quality(record: dict) -> tuple[bool, str]:
    ad = _ad(record)
    if not is_alive(record):
        return False, "not_active"

    price = ad.get("price") or 0
    try:
        price = int(price)
    except (TypeError, ValueError):
        return False, "bad_price"

    if price < MIN_PRICE or price > MAX_PRICE:
        return False, "price_out_of_range"

    subject = (ad.get("subject") or "").strip()
    if len(subject) < MIN_SUBJECT_LEN:
        return False, "short_subject"

    imgs = ad.get("images") if isinstance(ad.get("images"), list) else []
    thumb = ad.get("thumbnail_image") or ad.get("image") or ad.get("webp_image")
    if not imgs and not thumb:
        return False, "no_image"

    if not (ad.get("region_name_v3") or ad.get("region_name")):
        return False, "no_region"

    if not ad.get("account_name"):
        return False, "no_seller"

    if not ad.get("list_time"):
        return False, "no_list_time"

    if not ad.get("list_id"):
        return False, "no_list_id"

    return True, "ok"


def collect_ids(session, needed: int, buffer: float = 2.5) -> list[int]:
    """Thu list_id từ listing API — lấy dư buffer vì sẽ lọc bớt."""
    target = int(needed * buffer)
    ids = []
    seen = set()
    page = 1

    while len(ids) < target:
        ads = _fetch_listing_page(session, page)
        if not ads:
            break
        for ad in ads:
            lid = ad.get("list_id")
            if lid and lid not in seen:
                seen.add(lid)
                ids.append(lid)
        log.info(f"  listing page {page}: collected {len(ids):,} ids")
        if page * 50 >= 19_900:
            break
        page += 1
        time.sleep(PAGE_DELAY)

    return ids


def fetch_and_filter(session, list_ids: list[int], limit: int) -> tuple[list[dict], dict]:
    stats = {"scanned": 0, "detail_ok": 0, "detail_404": 0, "rejected": {}, "accepted": 0}
    accepted = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_fetch_detail_one, (lid, session)): lid for lid in list_ids}
        for fut in as_completed(futures):
            lid, detail = fut.result()
            stats["scanned"] += 1

            if not detail:
                stats["detail_404"] += 1
                continue

            stats["detail_ok"] += 1
            ok, reason = is_quality(detail)
            if not ok:
                stats["rejected"][reason] = stats["rejected"].get(reason, 0) + 1
                continue

            accepted.append(detail)
            stats["accepted"] += 1

            if stats["accepted"] % 50 == 0:
                log.info(f"  quality bikes: {stats['accepted']}/{limit}")

            if len(accepted) >= limit:
                for f in futures:
                    f.cancel()
                break

    accepted.sort(
        key=lambda r: int(_ad(r).get("list_time", 0)),
        reverse=True,
    )
    return accepted[:limit], stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--buffer", type=float, default=2.5, help="Listing ID buffer multiplier")
    args = p.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now(TZ_VN).strftime("%Y-%m-%d")
    out = args.output or os.path.join(REPORT_DIR, f"chotot_quality_{args.limit}_{today}.json")

    log.info(f"Target: {args.limit} live + quality bikes")
    session = _make_session()

    list_ids = collect_ids(session, args.limit, args.buffer)
    log.info(f"Fetching details for {len(list_ids):,} ids...")

    bikes, stats = fetch_and_filter(session, list_ids, args.limit)

    if len(bikes) < args.limit:
        log.warning(f"Only got {len(bikes)}/{args.limit} — fetching more pages...")
        extra_ids = collect_ids(session, args.limit - len(bikes), buffer=3.0)
        existing = {_ad(b).get("list_id") for b in bikes}
        extra_ids = [i for i in extra_ids if i not in existing]
        more, stats2 = fetch_and_filter(session, extra_ids, args.limit - len(bikes))
        bikes.extend(more)
        bikes.sort(key=lambda r: int(_ad(r).get("list_time", 0)), reverse=True)
        bikes = bikes[:args.limit]
        for k, v in stats2["rejected"].items():
            stats["rejected"][k] = stats.get("rejected", {}).get(k, 0) + v
        stats["accepted"] = len(bikes)

    report = {
        "meta": {
            "ngay_tao": datetime.now(TZ_VN).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "so_xe": len(bikes),
            "yeu_cau": args.limit,
            "tieu_chi": {
                "con_song": "detail 200, status=active, state=accepted",
                "chat_luong": f"price {MIN_PRICE}-{MAX_PRICE}, co anh, co khu vuc, co nguoi ban, tieu de >= {MIN_SUBJECT_LEN} ky tu",
            },
            "format": "raw Chotot API response per bike (khong flatten)",
            "thong_ke_loc": stats,
        },
        "xe": bikes,
    }

    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    size_mb = os.path.getsize(out) / 1024 / 1024
    log.info(f"Done: {len(bikes)} bikes → {out} ({size_mb:.1f} MB)")
    print(json.dumps({"output": out, "count": len(bikes), "stats": stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
