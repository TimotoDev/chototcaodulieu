"""
scrape_by_seller.py — Lấy thêm listings bằng account_oid scraping
==================================================================
account_oid API không bị cap 20k → có thể lấy TẤT CẢ listings của mỗi seller.

Strategy:
1. Load existing 25k listings
2. Lấy account_oid cho mỗi unique seller (via detail API)
3. Paginate qua TOÀN BỘ listings của mỗi seller
4. Collect listings chưa có trong existing set
5. Merge vào CSV chính

Chạy:
    python3 scrape_by_seller.py
    python3 scrape_by_seller.py --upload-s3 --upload-ddb
"""

import os, re, time, logging, argparse
from datetime import date

import requests
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://xe.chotot.com",
    "Referer": "https://xe.chotot.com/mua-ban-xe-may",
}
LIST_URL   = "https://gateway.chotot.com/v1/public/ad-listing"
DETAIL_URL = "https://gateway.chotot.com/v2/public/ad-listing"
CATEGORY   = "2020"
DELAY      = 0.3

S3_BUCKET  = os.environ.get("CHOTOT_S3_BUCKET", "chotot-xe-may-data")
S3_PREFIX  = os.environ.get("CHOTOT_S3_PREFIX", "xe-may")
DDB_TABLE  = os.environ.get("CHOTOT_DDB_TABLE",  "chotot-xe-may")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")

ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
BRAND_MAP = {
    1:"Honda", 2:"Yamaha", 3:"Vespa / Piaggio", 4:"SYM",
    5:"Suzuki", 6:"Kawasaki", 7:"Ducati", 8:"BMW",
    9:"Harley-Davidson", 10:"KTM", 11:"Triumph", 12:"Royal Enfield",
    13:"Benelli", 14:"Kymco", 15:"Peugeot", 99:"Khác",
}
TYPE_MAP     = {1:"Tay ga", 2:"Xe số", 3:"Xe côn tay", 4:"Xe phân khối lớn", 5:"Xe điện", 6:"Xe 3 bánh"}
CAPACITY_MAP = {1:"Dưới 50cc", 2:"50-100cc", 3:"100-175cc", 4:"175-300cc", 5:"300-500cc", 6:"Trên 500cc"}

def clean(v):
    return ILLEGAL_RE.sub("", v) if isinstance(v, str) else v

def parse_ad(ad: dict) -> dict:
    pd_dict = {p["id"]: p["value"] for p in ad.get("params", [])}
    price   = ad.get("price", 0)
    return {
        "list_id":      ad.get("list_id"),
        "tieu_de":      clean(ad.get("subject", "")),
        "hang_xe":      BRAND_MAP.get(ad.get("motorbikebrand", 0), "Khác"),
        "loai_xe":      TYPE_MAP.get(ad.get("motorbiketype", 0), clean(pd_dict.get("motorbiketype", ""))),
        "dung_tich":    CAPACITY_MAP.get(ad.get("motorbikecapacity", 0), ""),
        "nam_sx":       ad.get("regdate") or pd_dict.get("regdate", ""),
        "tinh_trang":   clean(pd_dict.get("condition_ad", ad.get("condition_ad_name", ""))),
        "gia_vnd":      price,
        "gia_trieu":    round(price / 1_000_000, 2) if price else None,
        "khu_vuc":      clean(ad.get("region_name_v3", ad.get("region_name", ""))),
        "quan_huyen":   clean(ad.get("area_name", "")),
        "nguoi_ban":    clean(ad.get("account_name", "")),
        "so_da_ban":    ad.get("sold_ads", 0),
        "km_da_di":     ad.get("mileage_v2", 0),
        "ngay_dang":    ad.get("date", ""),
        "co_video":     bool(ad.get("videos")),
        "url":          f"https://xe.chotot.com/mua-ban-xe-may/{ad.get('list_id')}",
    }
2
def get_account_oid(list_id: int):
    try:
        r = requests.get(f"{DETAIL_URL}/{list_id}", headers=HEADERS, timeout=10)
        return r.json()["ad"].get("account_oid")
    except:
        return None

def scrape_seller(account_oid: str, existing_ids: set) -> list:
    """Lấy TẤT CẢ listings của 1 seller, trả về records chưa có."""
    new_records = []
    page = 1
    while True:
        try:
            r = requests.get(LIST_URL, params={
                "account_oid": account_oid,
                "cg": CATEGORY,
                "limit": 50,
                "o": (page - 1) * 50,
                "page": page,
                "st": "s,k",
            }, headers=HEADERS, timeout=10)
            ads = r.json().get("ads", [])
            if not ads:
                break
            for ad in ads:
                lid = ad.get("list_id")
                if lid and lid not in existing_ids:
                    try:
                        new_records.append(parse_ad(ad))
                        existing_ids.add(lid)
                    except:
                        pass
            page += 1
            time.sleep(DELAY)
        except Exception as e:
            log.error(f"  Seller error page {page}: {e}")
            time.sleep(3)
            break
    return new_records

def upload_to_s3(csv_path: str) -> str:
    import boto3
    key = f"{S3_PREFIX}/full/{os.path.basename(csv_path)}"
    boto3.client("s3", region_name=AWS_REGION).upload_file(csv_path, S3_BUCKET, key)
    log.info(f"S3 ✅  s3://{S3_BUCKET}/{key}")
    return key

def upsert_to_dynamodb(records: list):
    import boto3
    from decimal import Decimal
    ddb   = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(DDB_TABLE)
    BATCH = 25
    ok = 0
    for i in range(0, len(records), BATCH):
        batch = records[i:i+BATCH]
        with table.batch_writer() as bw:
            for r in batch:
                item = {}
                for k, v in r.items():
                    if v is None or v == "": continue
                    item[k] = Decimal(str(v)) if isinstance(v, float) else v
                try:
                    bw.put_item(Item=item)
                    ok += 1
                except: pass
    log.info(f"DynamoDB ✅  {ok:,} upserted")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload-s3",  action="store_true")
    ap.add_argument("--upload-ddb", action="store_true")
    ap.add_argument("--min-listings", type=int, default=5,
                    help="Chỉ scrape sellers có >= N listings trong data hiện tại (default: 5)")
    args = ap.parse_args()

    # ── Load existing data ─────────────────────────────────────────────
    ck_dir = "chotot_checkpoints"
    all_dfs = []
    for f in os.listdir(ck_dir):
        if f.endswith(".csv") and os.path.getsize(os.path.join(ck_dir, f)) > 10:
            try: all_dfs.append(pd.read_csv(os.path.join(ck_dir, f)))
            except: pass

    # Tìm file CSV đầy đủ cuối
    existing_csvs = sorted([f for f in os.listdir(".") if f.startswith("chotot_xe_may_full_") and f.endswith(".csv")])
    if existing_csvs:
        all_dfs.append(pd.read_csv(existing_csvs[-1]))

    df_existing = pd.concat(all_dfs, ignore_index=True).drop_duplicates("list_id")
    existing_ids = set(df_existing["list_id"].astype(int).tolist())
    log.info(f"Existing records: {len(existing_ids):,}")

    # ── Tìm sellers có nhiều listings nhất ────────────────────────────
    seller_counts = df_existing.groupby("nguoi_ban").size().sort_values(ascending=False)
    target_sellers = seller_counts[seller_counts >= args.min_listings]
    log.info(f"Sellers với >= {args.min_listings} listings: {len(target_sellers):,}")
    log.info(f"Top 5: {list(target_sellers.head(5).items())}")

    # ── Checkpoint account_oids đã xử lý ─────────────────────────────
    oid_ck_file = "seller_oids.txt"
    done_oids = set()
    if os.path.exists(oid_ck_file):
        with open(oid_ck_file) as f:
            done_oids = set(line.strip() for line in f if line.strip())
    log.info(f"Sellers đã xử lý trước: {len(done_oids)}")

    # ── Scrape từng seller ─────────────────────────────────────────────
    all_new_records = []
    oid_cache = {}  # seller_name -> account_oid

    for i, (seller_name, count) in enumerate(target_sellers.items()):
        # Lấy account_oid từ 1 listing của seller này
        sample_lid = int(df_existing[df_existing["nguoi_ban"] == seller_name]["list_id"].iloc[0])
        account_oid = get_account_oid(sample_lid)
        if not account_oid or account_oid in done_oids:
            continue

        log.info(f"[{i+1}/{len(target_sellers)}] {seller_name} ({count} known) | oid={account_oid[:16]}...")
        new_recs = scrape_seller(account_oid, existing_ids)
        all_new_records.extend(new_recs)

        # Lưu checkpoint
        with open(oid_ck_file, "a") as f:
            f.write(account_oid + "\n")
        done_oids.add(account_oid)

        log.info(f"  +{len(new_recs):,} new | Total new so far: {len(all_new_records):,}")

        if (i + 1) % 50 == 0:
            log.info(f"  === Progress: {i+1}/{len(target_sellers)} sellers | {len(all_new_records):,} new records ===")

    # ── Save ───────────────────────────────────────────────────────────
    log.info(f"\nTổng new records từ seller scrape: {len(all_new_records):,}")

    if not all_new_records:
        log.info("Không tìm thêm được gì mới.")
        return

    # Merge vào existing
    df_new = pd.DataFrame(all_new_records)
    df_merged = pd.concat([df_existing, df_new], ignore_index=True).drop_duplicates("list_id")
    out_csv = f"chotot_xe_may_full_{date.today()}.csv"
    df_merged.to_csv(out_csv, index=False, encoding="utf-8-sig")
    log.info(f"CSV: {out_csv} | {len(df_merged):,} total records ({len(df_new):,} added)")

    if args.upload_s3:
        upload_to_s3(out_csv)
    if args.upload_ddb:
        upsert_to_dynamodb(all_new_records)

    log.info("DONE!")

if __name__ == "__main__":
    main()
