"""
chotot.py — Daily incremental scraper cho Lambda
=================================================
Chạy lúc 00:00 VN mỗi ngày, cào toàn bộ xe đăng HÔM QUA.
Flow: scrape listing IDs → fetch full detail song song → upsert DynamoDB
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────
BASE_URL    = "https://gateway.chotot.com/v1/public/ad-listing"
DETAIL_URL  = "https://gateway.chotot.com/v2/public/ad-listing"
CATEGORY    = "2020"
LIMIT       = 50
WORKERS     = 20       # concurrent detail fetches
DDB_TABLE   = os.environ.get("CHOTOT_DDB_TABLE",  "chotot-xe-may")
AWS_REGION  = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://xe.chotot.com",
    "Referer": "https://xe.chotot.com/mua-ban-xe-may",
}


def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=30, pool_maxsize=30))
    s.headers.update(HEADERS)
    return s


def _vn_midnight(days_ago: int = 0) -> int:
    tz_vn = timezone(timedelta(hours=7))
    d = datetime.now(tz_vn).replace(hour=0, minute=0, second=0, microsecond=0)
    d -= timedelta(days=days_ago)
    return int(d.timestamp())


def _fetch_listing_page(session: requests.Session, page: int) -> list:
    params = {
        "cg": CATEGORY, "limit": LIMIT,
        "o": (page - 1) * LIMIT, "page": page, "st": "s,k",
    }
    r = session.get(BASE_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("ads", [])


def _fetch_detail_one(args) -> tuple:
    """(list_id, session) → (list_id, detail_dict | None)"""
    list_id, session = args
    try:
        r = session.get(f"{DETAIL_URL}/{list_id}", timeout=15)
        if r.status_code == 404:
            return list_id, None
        r.raise_for_status()
        return list_id, r.json()
    except Exception as e:
        log.warning(f"detail {list_id}: {e}")
        return list_id, None


def scrape_yesterday() -> list:
    """
    Cào toàn bộ listings đăng HÔM QUA (00:00–23:59 VN).
    Trả về list raw JSON objects — không parse, không bỏ field.
    """
    since_ts = _vn_midnight(days_ago=1)
    until_ts = _vn_midnight(days_ago=0)

    log.info(f"Scraping: {datetime.fromtimestamp(since_ts).date()}")
    log.info(f"  since_ts={since_ts}  until_ts={until_ts}")

    session = _make_session()

    # ── Bước 1: Thu thập list_ids từ hôm qua ─────────────────────────
    yesterday_ids = []
    seen_ids = set()
    page = 1

    while True:
        try:
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
                    if lid and lid not in seen_ids:
                        seen_ids.add(lid)
                        yesterday_ids.append(lid)
                else:
                    stop = True

            log.info(f"  page {page:3d}: {len(ads)} ads | collected: {len(yesterday_ids)}")

            if stop:
                log.info("Gặp tin cũ hơn hôm qua → dừng.")
                break
            if page * LIMIT >= 19_900:
                log.warning("Giới hạn 20k → dừng.")
                break

            page += 1
            time.sleep(0.15)

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                log.warning("Rate limit 429 → chờ 30s")
                time.sleep(30)
            else:
                log.error(f"HTTP {e.response.status_code} → dừng")
                break
        except Exception as e:
            log.error(f"Trang {page}: {e}")
            time.sleep(3)

    log.info(f"Tổng IDs hôm qua: {len(yesterday_ids)}")

    # ── Bước 2: Fetch full detail song song ───────────────────────────
    raw_records = []
    args = [(lid, session) for lid in yesterday_ids]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_fetch_detail_one, a): a[0] for a in args}
        done_count = 0
        for future in as_completed(futures):
            lid, detail = future.result()
            done_count += 1
            if detail:
                raw_records.append(detail)
            if done_count % 200 == 0:
                log.info(f"  Detail {done_count}/{len(yesterday_ids)} | ok={len(raw_records)}")

    log.info(f"Detail OK: {len(raw_records)}/{len(yesterday_ids)}")
    return raw_records


def upsert_to_dynamodb(raw_records: list) -> int:
    """Batch-upsert raw records vào DynamoDB. Key=list_id, lưu full raw JSON."""
    import boto3
    from boto3.dynamodb.types import TypeSerializer

    client     = boto3.client("dynamodb", region_name=AWS_REGION)
    serializer = TypeSerializer()

    def _make_item(record: dict) -> dict | None:
        ad = record.get("ad", record)
        list_id = ad.get("list_id") or ad.get("ad_id")
        if not list_id:
            return None
        item = {}
        for k, v in ad.items():
            if v is None or v == "" or v == [] or v == {}:
                continue
            safe_k = str(k).replace(".", "_")
            try:
                item[safe_k] = serializer.serialize(v)
            except Exception:
                item[safe_k] = serializer.serialize(str(v))
        item["list_id"] = serializer.serialize(int(list_id))
        # Store date_added by Vietnam day so dashboard daily files line up.
        list_time_ms = ad.get("list_time", 0)
        if list_time_ms:
            tz_vn = timezone(timedelta(hours=7))
            date_added = datetime.fromtimestamp(int(list_time_ms) / 1000, tz=tz_vn).strftime("%Y-%m-%d")
            item["date_added"] = serializer.serialize(date_added)
        try:
            item["_raw_json"] = serializer.serialize(
                json.dumps(record, ensure_ascii=False)
            )
        except Exception:
            pass
        return item

    written = 0
    batch = []
    for record in raw_records:
        item = _make_item(record)
        if not item:
            continue
        batch.append({"PutRequest": {"Item": item}})
        if len(batch) == 25:
            try:
                client.batch_write_item(RequestItems={DDB_TABLE: batch})
                written += len(batch)
            except Exception as e:
                log.error(f"DDB batch: {e}")
            batch = []
    if batch:
        try:
            client.batch_write_item(RequestItems={DDB_TABLE: batch})
            written += len(batch)
        except Exception as e:
            log.error(f"DDB batch final: {e}")

    log.info(f"DynamoDB: {written}/{len(raw_records)} → {DDB_TABLE}")
    return written
