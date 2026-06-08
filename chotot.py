"""
🏍️  CHOTOT XE MÁY SCRAPER – FULL 60K
======================================
Fix: Chotot giới hạn offset 20k/request → chia nhỏ theo từng tỉnh/thành.
     Mỗi tỉnh cào độc lập, merge + dedup ở cuối.

Cách dùng:
    pip install requests pandas openpyxl
    python chotot_xe_may_full.py

Output:
    chotot_xe_may_full.csv
    chotot_xe_may_full.xlsx
"""

import re
import requests
import pandas as pd
import time
import os
from datetime import datetime

# ─── MAPPINGS ────────────────────────────────────────────────────────
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

# ─── 63 TỈNH/THÀNH ───────────────────────────────────────────────────
# Mỗi tỉnh ≤ 20k → bypass giới hạn offset API
REGIONS = {
    "An Giang": 40000, "Bà Rịa - Vũng Tàu": 55000, "Bắc Giang": 61000,
    "Bắc Kạn": 62000, "Bạc Liêu": 95000, "Bắc Ninh": 63000,
    "Bến Tre": 83000, "Bình Định": 52000, "Bình Dương": 2011,
    "Bình Phước": 70000, "Bình Thuận": 60000, "Cà Mau": 96000,
    "Cần Thơ": 90000, "Cao Bằng": 4000, "Đà Nẵng": 15000,
    "Đắk Lắk": 66000, "Đắk Nông": 67000, "Điện Biên": 20000,
    "Đồng Nai": 56000, "Đồng Tháp": 87000, "Gia Lai": 64000,
    "Hà Giang": 2000, "Hà Nam": 35000, "Hà Nội": 12000,
    "Hà Tĩnh": 42000, "Hải Dương": 30000, "Hải Phòng": 16000,
    "Hậu Giang": 93000, "Hòa Bình": 17000, "Hưng Yên": 33000,
    "Khánh Hòa": 54000, "Kiên Giang": 91000, "Kon Tum": 65000,
    "Lai Châu": 22000, "Lâm Đồng": 68000, "Lạng Sơn": 6000,
    "Lào Cai": 10000, "Long An": 80000, "Nam Định": 36000,
    "Nghệ An": 41000, "Ninh Bình": 37000, "Ninh Thuận": 58000,
    "Phú Thọ": 25000, "Phú Yên": 53000, "Quảng Bình": 44000,
    "Quảng Nam": 49000, "Quảng Ngãi": 51000, "Quảng Ninh": 22000,
    "Quảng Trị": 45000, "Sóc Trăng": 94000, "Sơn La": 14000,
    "Tây Ninh": 72000, "Thái Bình": 34000, "Thái Nguyên": 19000,
    "Thanh Hóa": 38000, "Thừa Thiên Huế": 46000, "Tiền Giang": 82000,
    "TP HCM": 13000, "Trà Vinh": 84000, "Tuyên Quang": 8000,
    "Vĩnh Long": 86000, "Vĩnh Phúc": 26000, "Yên Bái": 15000,
}

# ─── CONFIG ──────────────────────────────────────────────────────────
BASE_URL       = "https://gateway.chotot.com/v1/public/ad-listing"
CATEGORY       = "2020"
LIMIT          = 50
DELAY_SEC      = 0.4
CHECKPOINT_DIR = "chotot_checkpoints"
OUTPUT_CSV     = "chotot_xe_may_full.csv"
OUTPUT_XLSX    = "chotot_xe_may_full.xlsx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Origin": "https://xe.chotot.com",
    "Referer": "https://xe.chotot.com/mua-ban-xe-may",
}

# ─── HELPERS ─────────────────────────────────────────────────────────
ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

def clean_str(val):
    if isinstance(val, str):
        return ILLEGAL_RE.sub("", val)
    return val

def fetch_page(page: int, region: int) -> dict:
    params = {
        "cg": CATEGORY, "o": (page - 1) * LIMIT,
        "st": "s,k", "limit": LIMIT, "page": page, "r": region,
    }
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()

def parse_ad(ad: dict) -> dict:
    brand_id    = ad.get("motorbikebrand", 0)
    type_id     = ad.get("motorbiketype", 0)
    capacity_id = ad.get("motorbikecapacity", 0)
    price       = ad.get("price", 0)
    year        = ad.get("regdate", None)
    pd_dict     = {p["id"]: p["value"] for p in ad.get("params", [])}
    return {
        "list_id":      ad.get("list_id"),
        "ad_id":        ad.get("ad_id"),
        "tieu_de":      clean_str(ad.get("subject", "")),
        "hang_xe":      BRAND_MAP.get(brand_id, f"Brand_{brand_id}"),
        "loai_xe":      TYPE_MAP.get(type_id, clean_str(pd_dict.get("motorbiketype", "Khác"))),
        "dung_tich":    CAPACITY_MAP.get(capacity_id, ""),
        "nam_san_xuat": year or pd_dict.get("regdate", ""),
        "tinh_trang":   clean_str(pd_dict.get("condition_ad", ad.get("condition_ad_name", ""))),
        "gia_vnd":      price,
        "gia_trieu":    round(price / 1_000_000, 2) if price else None,
        "khu_vuc":      clean_str(ad.get("region_name_v3", ad.get("region_name", ""))),
        "quan_huyen":   clean_str(ad.get("area_name", "")),
        "nguoi_ban":    clean_str(ad.get("account_name", "")),
        "so_da_ban":    ad.get("sold_ads", 0),
        "km_da_di":     ad.get("mileage_v2", 0),
        "ngay_dang":    ad.get("date", ""),
        "co_video":     bool(ad.get("videos")),
        "url":          f"https://xe.chotot.com/mua-ban-xe-may/{ad.get('list_id')}",
    }

def scrape_region(region_name: str, region_code: int) -> list:
    ck_file = os.path.join(CHECKPOINT_DIR, f"{region_code}.csv")

    # Resume nếu có checkpoint
    if os.path.exists(ck_file):
        try:
            df_ck = pd.read_csv(ck_file, encoding="utf-8-sig")
            records    = df_ck.to_dict("records")
            start_page = (len(records) // LIMIT) + 1
            print(f"    ♻️  Resume trang {start_page} ({len(records):,} tin)")
        except Exception:
            records, start_page = [], 1
    else:
        records, start_page = [], 1

    page = start_page
    while True:
        try:
            data = fetch_page(page, region_code)
            ads  = data.get("ads", [])
            if not ads:
                break

            for ad in ads:
                try:
                    records.append(parse_ad(ad))
                except Exception:
                    pass

            total = data.get("total", "?")
            print(f"    trang {page} +{len(ads)} ({len(records):,}/{total})", end="\r")

            if page % 50 == 0:
                os.makedirs(CHECKPOINT_DIR, exist_ok=True)
                pd.DataFrame(records).to_csv(ck_file, index=False, encoding="utf-8-sig")

            if len(ads) < LIMIT:
                break

            # Dừng trước giới hạn 30k của Chotot
            if (page * LIMIT) >= 300000:
                print(f"\n    ⚠️  Gần giới hạn 30k — dừng tỉnh này")
                break

            page += 1
            time.sleep(DELAY_SEC)

        except requests.exceptions.HTTPError as e:
            sc = e.response.status_code
            if sc == 429:
                print(f"\n    ⏳ Rate limit, chờ 30s...")
                time.sleep(30)
                continue
            elif sc in (400, 404):
                break
            else:
                print(f"\n    ❌ HTTP {sc}, thử lại...")
                time.sleep(5)
                continue
        except Exception as e:
            print(f"\n    ❌ {e}, thử lại...")
            time.sleep(3)
            continue

    print(f"    → {len(records):,} tin              ")
    return records

# ─── MAIN ────────────────────────────────────────────────────────────
def main():
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    start_time = time.time()

    print("=" * 60)
    print("🏍️  CHOTOT SCRAPER – FULL 60K (REGION MODE)")
    print("=" * 60)
    print(f"⏱  Bắt đầu: {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}")
    print(f"📍 Cào theo {len(REGIONS)} tỉnh/thành → bypass giới hạn 30k\n")

    all_records = []
    seen_ids    = set()

    for i, (name, code) in enumerate(REGIONS.items(), 1):
        print(f"\n[{i:02d}/{len(REGIONS)}] 📍 {name} (code={code})")
        region_recs = scrape_region(name, code)

        added = 0
        for r in region_recs:
            lid = r.get("list_id")
            if lid and lid not in seen_ids:
                seen_ids.add(lid)
                all_records.append(r)
                added += 1

        dup = len(region_recs) - added
        print(f"    ✅ +{added:,} mới  (bỏ {dup} trùng)  |  Tổng: {len(all_records):,}")

        ck_file = os.path.join(CHECKPOINT_DIR, f"{code}.csv")
        if os.path.exists(ck_file):
            os.remove(ck_file)

    # ─── LƯU ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"📦 Tổng: {len(all_records):,} tin  |  💾 Đang lưu...")

    if not all_records:
        print("⚠️  Không có dữ liệu!")
        return

    df = pd.DataFrame(all_records)

    prices = df["gia_trieu"].dropna()
    print(f"\n📊 Hãng phổ biến: {df['hang_xe'].value_counts().index[0]}")
    print(f"   Loại phổ biến: {df['loai_xe'].value_counts().index[0]}")
    print(f"   Giá TB: {prices.mean():.1f}tr  |  Min: {prices.min():.1f}tr  |  Max: {prices.max():.1f}tr")

    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n  ✅ CSV:  {OUTPUT_CSV}  ({os.path.getsize(OUTPUT_CSV):,} bytes)")

    # Clean toàn bộ string trước khi ghi Excel
    df_xl = df.copy()
    for col in df_xl.select_dtypes(include="object").columns:
        df_xl[col] = df_xl[col].apply(clean_str)

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        df_xl.to_excel(writer, sheet_name="Danh sách xe", index=False)

        pd.DataFrame({
            "Hạng mục": ["Tổng tin", "Giá TB (triệu)", "Giá min", "Giá max"],
            "Giá trị":  [len(df), round(prices.mean(), 1), prices.min(), prices.max()]
        }).to_excel(writer, sheet_name="Thống kê", index=False)

        df["hang_xe"].value_counts().rename_axis("Hãng xe").reset_index(
            name="Số tin").to_excel(writer, sheet_name="Theo hãng xe", index=False)

        df["loai_xe"].value_counts().rename_axis("Loại xe").reset_index(
            name="Số tin").to_excel(writer, sheet_name="Theo loại xe", index=False)

        df["khu_vuc"].value_counts().rename_axis("Khu vực").reset_index(
            name="Số tin").to_excel(writer, sheet_name="Theo khu vực", index=False)

    print(f"  ✅ Excel: {OUTPUT_XLSX}  ({os.path.getsize(OUTPUT_XLSX):,} bytes)")

    elapsed = time.time() - start_time
    print(f"\n🎉 HOÀN TẤT!  {len(all_records):,} tin  |  "
          f"{int(elapsed//60)}p{int(elapsed%60)}s  |  "
          f"{datetime.now().strftime('%H:%M:%S')}")
    print("=" * 60)

if __name__ == "__main__":
    main()