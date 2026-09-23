#!/usr/bin/env python3
"""
Tạo report đa phương diện cho N xe mới nhất trong 1 ngày VN.

Usage:
    python3 generate_daily_report.py
    python3 generate_daily_report.py --date 2026-07-06 --limit 50
    python3 generate_daily_report.py --limit 50 -o reports/my_report.json
"""

import argparse
import json
import os
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta

from chotot import (
    _make_session,
    _fetch_listing_page,
    _fetch_detail_one,
    _vn_day_bounds,
)

BRAND_MAP = {
    1: "Honda", 2: "Yamaha", 3: "Vespa / Piaggio", 4: "SYM",
    5: "Suzuki", 6: "Kawasaki", 7: "Ducati", 8: "BMW",
    9: "Harley-Davidson", 10: "KTM", 11: "Triumph", 12: "Royal Enfield",
    13: "Benelli", 14: "Kymco", 15: "Peugeot", 99: "Khác",
}
TYPE_MAP = {
    1: "Tay ga", 2: "Xe số", 3: "Xe côn tay",
    4: "Xe phân khối lớn", 5: "Xe điện", 6: "Xe 3 bánh",
}
CAPACITY_MAP = {
    1: "Dưới 50cc", 2: "50-100cc", 3: "100-175cc",
    4: "175-300cc", 5: "300-500cc", 6: "Trên 500cc",
}

TZ_VN = timezone(timedelta(hours=7))
REPORT_DIR = os.path.join(os.path.dirname(__file__), "reports")


def fetch_today_newest(limit: int, target_date: date) -> list[dict]:
    since_ts, until_ts = _vn_day_bounds(target_date)
    session = _make_session()
    list_ids = []
    seen = set()
    page = 1

    while len(list_ids) < limit:
        ads = _fetch_listing_page(session, page)
        if not ads:
            break
        stop = False
        for ad in ads:
            ts = int(ad.get("list_time", 0)) // 1000
            lid = ad.get("list_id")
            if ts >= until_ts:
                continue
            if ts >= since_ts:
                if lid and lid not in seen:
                    seen.add(lid)
                    list_ids.append(lid)
                    if len(list_ids) >= limit:
                        break
            else:
                stop = True
        if stop or len(list_ids) >= limit:
            break
        page += 1
        time.sleep(0.15)

    records = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_fetch_detail_one, (lid, session)): lid for lid in list_ids}
        for fut in as_completed(futures):
            _lid, detail = fut.result()
            if detail:
                records.append(detail)

    records.sort(
        key=lambda r: int((r.get("ad", r) or {}).get("list_time", 0)),
        reverse=True,
    )
    return records[:limit]


def _ad(record: dict) -> dict:
    return record.get("ad", record) if isinstance(record, dict) else {}


def _price_trieu(price) -> float | None:
    try:
        p = int(price)
        return round(p / 1_000_000, 2) if p else None
    except (TypeError, ValueError):
        return None


def _list_time_vn(ad: dict) -> str:
    ms = ad.get("list_time", 0)
    if not ms:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, tz=TZ_VN).strftime("%Y-%m-%d %H:%M:%S")


def build_bike_record(record: dict) -> dict:
    ad = _ad(record)
    brand_id = ad.get("motorbikebrand", 0)
    type_id = ad.get("motorbiketype", 0)
    cap_id = ad.get("motorbikecapacity", 0)
    price = ad.get("price", 0)
    imgs = ad.get("images", []) if isinstance(ad.get("images"), list) else []

    return {
        "list_id": ad.get("list_id"),
        "ad_id": ad.get("ad_id"),
        "tieu_de": ad.get("subject", ""),
        "gia_vnd": price,
        "gia_trieu": _price_trieu(price),
        "gia_hien_thi": ad.get("price_string", ""),
        "hang_xe": BRAND_MAP.get(brand_id, f"Brand_{brand_id}"),
        "hang_xe_id": brand_id,
        "model_id": ad.get("motorbikemodel"),
        "loai_xe": TYPE_MAP.get(type_id, f"Type_{type_id}"),
        "loai_xe_id": type_id,
        "dung_tich": CAPACITY_MAP.get(cap_id, ""),
        "dung_tich_id": cap_id,
        "nam_san_xuat": ad.get("regdate"),
        "km_da_di": ad.get("mileage_v2", 0),
        "tinh_trang": ad.get("condition_ad_name", ""),
        "khu_vuc": ad.get("region_name_v3") or ad.get("region_name", ""),
        "quan_huyen": ad.get("area_name", ""),
        "phuong_xa": ad.get("ward_name", ""),
        "nguoi_ban": ad.get("account_name", ""),
        "account_oid": ad.get("account_oid"),
        "so_tin_da_ban": ad.get("sold_ads", 0),
        "danh_gia_tb": ad.get("average_rating_for_seller"),
        "so_anh": len(imgs),
        "co_video": bool(ad.get("videos")),
        "thumbnail": ad.get("thumbnail_image", ""),
        "ngay_dang": ad.get("date", ""),
        "list_time_vn": _list_time_vn(ad),
        "url": f"https://xe.chotot.com/mua-ban-xe-may/{ad.get('list_id')}" if ad.get("list_id") else "",
        "raw": record,
    }


def _price_band(trieu: float | None) -> str:
    if trieu is None:
        return "Không rõ"
    if trieu < 5:
        return "Dưới 5tr"
    if trieu < 10:
        return "5-10tr"
    if trieu < 20:
        return "10-20tr"
    if trieu < 40:
        return "20-40tr"
    if trieu < 100:
        return "40-100tr"
    return "Trên 100tr"


def build_summary(bikes: list[dict]) -> dict:
    prices = [b["gia_trieu"] for b in bikes if b.get("gia_trieu")]

    def top(counter: Counter, n=10):
        return [{"label": k, "count": v} for k, v in counter.most_common(n)]

    return {
        "tong_xe": len(bikes),
        "gia": {
            "trung_binh_trieu": round(statistics.mean(prices), 2) if prices else None,
            "trung_vi_trieu": round(statistics.median(prices), 2) if prices else None,
            "thap_nhat_trieu": min(prices) if prices else None,
            "cao_nhat_trieu": max(prices) if prices else None,
        },
        "theo_hang_xe": top(Counter(b["hang_xe"] for b in bikes)),
        "theo_loai_xe": top(Counter(b["loai_xe"] for b in bikes)),
        "theo_khu_vuc": top(Counter(b["khu_vuc"] for b in bikes)),
        "theo_tinh_trang": top(Counter(b["tinh_trang"] or "Không rõ" for b in bikes)),
        "theo_khoang_gia": top(Counter(_price_band(b.get("gia_trieu")) for b in bikes)),
        "theo_nam_sx": top(Counter(str(b.get("nam_san_xuat") or "Không rõ") for b in bikes)),
    }


def generate_report(target_date: date, limit: int) -> dict:
    raw_records = fetch_today_newest(limit, target_date)
    bikes = [build_bike_record(r) for r in raw_records]

    return {
        "meta": {
            "report_type": "chotot_xe_may_daily_snapshot",
            "ngay_bao_cao": target_date.isoformat(),
            "tao_luc": datetime.now(TZ_VN).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "so_xe": len(bikes),
            "gioi_han_yeu_cau": limit,
            "nguon": "Chotot API (newest first, full raw detail)",
            "timezone": "Asia/Ho_Chi_Minh (UTC+7)",
        },
        "tom_tat": build_summary(bikes),
        "xe": bikes,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: hôm nay VN)")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("-o", "--output", default=None)
    args = p.parse_args()

    target = date.fromisoformat(args.date) if args.date else datetime.now(TZ_VN).date()
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = args.output or os.path.join(REPORT_DIR, f"chotot_report_{target.isoformat()}_{args.limit}xe.json")

    print(f"Fetching {args.limit} newest bikes for {target}...")
    report = generate_report(target, args.limit)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Saved → {out}")
    print(f"Records: {report['meta']['so_xe']}")
    if report["xe"]:
        print(f"Newest: [{report['xe'][0]['list_id']}] {report['xe'][0]['tieu_de'][:50]}")


if __name__ == "__main__":
    main()
