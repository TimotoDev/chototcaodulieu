"""
init_scrape.py — Cào toàn bộ xe máy Chợ Tốt (~60k tin)
=========================================================
Chạy 1 lần để lấy dữ liệu nền. Daily incremental dùng Lambda.

Sau khi research API:
  - r= (region) param bị IGNORE sau sáp nhập tỉnh 2025
  - price=min-max param HOẠT ĐỘNG và filter đúng
  - motorbikemodel param mở "cửa sổ" mới vào dataset (~48% new/page)
  - Dùng 3 waves: price×region + brand×region + model×region

Dùng:
    python3 init_scrape.py                          # chỉ lưu CSV
    python3 init_scrape.py --upload-s3              # + upload S3
    python3 init_scrape.py --upload-s3 --upload-ddb # + DynamoDB
    python3 init_scrape.py --resume                 # tiếp tục nếu bị ngắt
"""

import re, os, time, argparse, logging
from datetime import date

import requests
import pandas as pd

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── Mappings ───────────────────────────────────────────────────────────
BRAND_MAP = {
    1:"Honda", 2:"Yamaha", 3:"Vespa / Piaggio", 4:"SYM",
    5:"Suzuki", 6:"Kawasaki", 7:"Ducati", 8:"BMW",
    9:"Harley-Davidson", 10:"KTM", 11:"Triumph", 12:"Royal Enfield",
    13:"Benelli", 14:"Kymco", 15:"Peugeot", 99:"Khác",
}
TYPE_MAP     = {1:"Tay ga", 2:"Xe số", 3:"Xe côn tay", 4:"Xe phân khối lớn", 5:"Xe điện", 6:"Xe 3 bánh"}
CAPACITY_MAP = {1:"Dưới 50cc", 2:"50-100cc", 3:"100-175cc", 4:"175-300cc", 5:"300-500cc", 6:"Trên 500cc"}

# ── 13 regions (sau sáp nhập tỉnh 2025) ──────────────────────────────
# Dùng param `region=` (KHÔNG phải `r=` — bị ignore)
# region=13: HCM, region=12: HN, region=3: ĐN, region=4: HP
# region=1-11: cụm tỉnh còn lại
REGIONS = {
    "r01_NinhBinh_BacNinh":   1,
    "r02_DongNai_BinhDuong":  2,
    "r03_DaNang":             3,
    "r04_HaiPhong":           4,
    "r05_CanTho_DongThap":    5,
    "r06_Hue_QuangTri":       6,
    "r07_KhanhHoa_LamDong":   7,
    "r08_ThanhHoa_NgheAn":    8,
    "r09_GiaLai_DakLak":      9,
    "r10_QuangNinh_BacNinh":  10,
    "r11_DienBien_SonLa":     11,
    "r12_HaNoi":              12,
    "r13_HCM":                13,
}

# ── 6 price ranges ────────────────────────────────────────────────────
PRICE_RANGES = [
    ("duoi_5M",   "0-5000000"),
    ("5_10M",     "5000001-10000000"),
    ("10_20M",    "10000001-20000000"),
    ("20_40M",    "20000001-40000000"),
    ("40_100M",   "40000001-100000000"),
    ("tren_100M", "100000001-9999999999"),
]

# ── 15 brands — dùng để surface thêm listings khác nhau ──────────────
BRANDS = {
    "honda":    1,  "yamaha":   2,  "vespa":    3,  "sym":      4,
    "suzuki":   5,  "kawasaki": 6,  "ducati":   7,  "bmw":      8,
    "harley":   9,  "ktm":      10, "triumph":  11, "royal":    12,
    "benelli":  13, "kymco":    14, "khac":     99,
}

# ── 192 motorbikemodel IDs — mỗi model tạo "cửa sổ" mới vào dataset ──
# Discovered: 192 unique model IDs (1-250 cover all, invalid IDs → 0 ads)
MODEL_IDS = list(range(1, 251))

# ── 341 area_v2 district codes — KHÔNG bị pagination cap! ────────────
# area_v2 filter cho phép lấy tất cả listings của 1 quận/huyện
# Collected from all 13 regions. Load at runtime.
_AREAS_FILE = os.path.join(os.path.dirname(__file__), "chotot_areas.json")

# ── Config ─────────────────────────────────────────────────────────────
BASE_URL       = "https://gateway.chotot.com/v1/public/ad-listing"
CATEGORY       = "2020"
LIMIT          = 50
DELAY_SEC      = 0.3
CHECKPOINT_DIR = "chotot_checkpoints"
OUTPUT_CSV     = f"chotot_xe_may_full_{date.today()}.csv"

S3_BUCKET  = os.environ.get("CHOTOT_S3_BUCKET", "chotot-xe-may-data")
S3_PREFIX  = os.environ.get("CHOTOT_S3_PREFIX", "xe-may")
DDB_TABLE  = os.environ.get("CHOTOT_DDB_TABLE",  "chotot-xe-may")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9",
    "Origin": "https://xe.chotot.com",
    "Referer": "https://xe.chotot.com/mua-ban-xe-may",
}

ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

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

def fetch_page(page: int, region: int = None, price_range: str = None, brand: int = None, model: int = None, area_v2: int = None) -> dict:
    params = {
        "cg": CATEGORY, "limit": LIMIT,
        "o": (page - 1) * LIMIT, "page": page,
        "st": "s,k",
    }
    if region is not None:      params["region"]          = region
    if price_range is not None: params["price"]           = price_range
    if brand is not None:       params["motorbikebrand"]  = brand
    if model is not None:       params["motorbikemodel"]  = model
    if area_v2 is not None:     params["area_v2"]         = area_v2
    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def scrape_segment(label: str, region: int = None, price_range: str = None, brand: int = None, model: int = None, area_v2: int = None) -> list:
    """Cào 1 price range toàn quốc. Trả về list records unique."""
    ck_csv  = os.path.join(CHECKPOINT_DIR, f"{label}.csv")
    ck_done = os.path.join(CHECKPOINT_DIR, f"{label}.done")

    if os.path.exists(ck_done) and os.path.exists(ck_csv):
        sz = os.path.getsize(ck_csv)
        if sz > 10:
            df = pd.read_csv(ck_csv, encoding="utf-8-sig")
            log.info(f"  [{label}] SKIP — {len(df):,} records (checkpoint)")
            return df.to_dict("records")

    log.info(f"  [{label}] — bắt đầu cào...")
    records: list = []
    seen: set     = set()
    page          = 1
    consec_empty  = 0

    while True:
        try:
            data = fetch_page(page, region=region, price_range=price_range, brand=brand, model=model, area_v2=area_v2)
            ads  = data.get("ads", [])

            if not ads:
                break

            new = 0
            for ad in ads:
                lid = ad.get("list_id")
                if lid and lid not in seen:
                    seen.add(lid)
                    try:
                        records.append(parse_ad(ad))
                        new += 1
                    except Exception:
                        pass

            print(f"    [{label}] trang {page:3d}  +{new:2d}  tổng={len(records):,}", end="\r", flush=True)

            if new == 0:
                consec_empty += 1
                if consec_empty >= 3:
                    break
            else:
                consec_empty = 0

            if len(ads) < LIMIT:
                break

            if page * LIMIT >= 19_950:
                log.warning(f"\n  [{label}] Chạm 20k giới hạn! Có thể thiếu data.")
                break

            page += 1
            time.sleep(DELAY_SEC)

        except requests.HTTPError as e:
            sc = e.response.status_code
            if sc == 429:
                print(f"\n  [{label}] Rate limit — chờ 30s...", flush=True)
                time.sleep(30)
            elif sc in (400, 404):
                break
            else:
                time.sleep(5)
        except Exception as e:
            log.error(f"  [{label}] Lỗi: {e}")
            time.sleep(3)

    print(flush=True)
    log.info(f"  [{label}] Xong: {len(records):,} records")

    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    pd.DataFrame(records).to_csv(ck_csv, index=False, encoding="utf-8-sig")
    open(ck_done, "w").close()
    return records

# ── AWS ────────────────────────────────────────────────────────────────
def upload_to_s3(csv_path: str) -> str:
    import boto3
    key = f"{S3_PREFIX}/full/{os.path.basename(csv_path)}"
    boto3.client("s3", region_name=AWS_REGION).upload_file(csv_path, S3_BUCKET, key)
    log.info(f"S3 ✅  s3://{S3_BUCKET}/{key}")
    return key

def upsert_to_dynamodb(records: list) -> int:
    import boto3
    from decimal import Decimal
    ddb   = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = ddb.Table(DDB_TABLE)
    ok = 0
    BATCH = 25
    for i in range(0, len(records), BATCH):
        batch = records[i:i+BATCH]
        with table.batch_writer() as bw:
            for r in batch:
                item = {}
                for k, v in r.items():
                    if v is None or v == "":
                        continue
                    if isinstance(v, float):
                        from decimal import Decimal
                        item[k] = Decimal(str(v))
                    else:
                        item[k] = v
                try:
                    bw.put_item(Item=item)
                    ok += 1
                except Exception as e:
                    log.error(f"DDB error: {e}")
        if i % 1000 == 0 and i > 0:
            log.info(f"  DynamoDB: {i:,}/{len(records):,} upserted")
    log.info(f"DynamoDB ✅  {ok:,} records upserted")
    return ok

# ── Main ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload-s3",   action="store_true")
    ap.add_argument("--upload-ddb",  action="store_true")
    ap.add_argument("--resume",      action="store_true", help="Tiếp tục từ checkpoint")
    args = ap.parse_args()

    os.makedirs(CHECKPOINT_DIR, exist_ok=True)

    log.info("=" * 55)
    log.info("CHOTOT INIT SCRAPE — national + 6 price ranges")
    log.info(f"S3={args.upload_s3}  DDB={args.upload_ddb}  resume={args.resume}")
    log.info("=" * 55)

    if not args.resume:
        import shutil
        shutil.rmtree(CHECKPOINT_DIR, ignore_errors=True)
        os.makedirs(CHECKPOINT_DIR)
        log.info("Checkpoint xóa sạch — bắt đầu fresh run")

    # ── Build segments ────────────────────────────────────────────────
    # Wave 1: region × price  (78 queries) — đã có checkpoint
    # Wave 2: region × brand  (195 queries) — surface listings khác
    segments = []

    for r_label, region_code in REGIONS.items():
        for p_label, price_range in PRICE_RANGES:
            label = f"price__{r_label}__{p_label}"
            segments.append(dict(label=label, region=region_code, price_range=price_range, brand=None, model=None, area_v2=None))

    for r_label, region_code in REGIONS.items():
        for b_label, brand_id in BRANDS.items():
            label = f"brand__{r_label}__{b_label}"
            segments.append(dict(label=label, region=region_code, price_range=None, brand=brand_id, model=None, area_v2=None))

    # Wave 3: model × region (250 × 13 = 3,250 queries) — surface listings bị giấu
    for model_id in MODEL_IDS:
        for r_label, region_code in REGIONS.items():
            label = f"model__{r_label}__m{model_id}"
            segments.append(dict(label=label, region=region_code, price_range=None, brand=None, model=model_id, area_v2=None))

    # Wave 4: area_v2 (quận/huyện) — KHÔNG bị pagination cap → lấy toàn bộ listings
    import json as _json
    areas = {}
    if os.path.exists(_AREAS_FILE):
        with open(_AREAS_FILE) as f:
            areas = _json.load(f)
    for area_code_str, area_info in areas.items():
        area_code = int(area_code_str)
        safe_name = area_info["name"].replace(" ", "_").replace("/", "-")[:20]
        label = f"area__r{area_info['region']:02d}__{area_code}__{safe_name}"
        segments.append(dict(label=label, region=None, price_range=None, brand=None, model=None, area_v2=area_code))

    log.info(f"Tổng {len(segments)} segments (78 price + 195 brand + {len(MODEL_IDS)*len(REGIONS)} model + {len(areas)} area_v2)")

    all_records: list = []
    global_seen: set  = set()

    for seg in segments:
        records = scrape_segment(**seg)
        added = 0
        for r in records:
            lid = r.get("list_id")
            if lid and lid not in global_seen:
                global_seen.add(lid)
                all_records.append(r)
                added += 1
        dup = len(records) - added
        log.info(f"  [{seg['label']}] +{added:,} unique  ({dup} dups)  | Global: {len(all_records):,}")

    log.info(f"\nTổng unique records: {len(all_records):,}")

    if not all_records:
        log.warning("Không có data!")
        return

    # Lưu CSV
    df = pd.DataFrame(all_records)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    log.info(f"CSV saved: {OUTPUT_CSV}  ({os.path.getsize(OUTPUT_CSV)//1024:,} KB)")

    if args.upload_s3:
        upload_to_s3(OUTPUT_CSV)
    if args.upload_ddb:
        upsert_to_dynamodb(all_records)

    log.info("=" * 55)
    log.info("DONE!")
    log.info("=" * 55)

if __name__ == "__main__":
    main()
