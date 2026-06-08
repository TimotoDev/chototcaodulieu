"""
fetch_raw_details.py — Lấy 100% raw JSON từ detail API cho mọi listing
=======================================================================
Không parse, không cắt — toàn bộ response JSON từ:
  GET https://gateway.chotot.com/v2/public/ad-listing/{list_id}

Output: chotot_raw_details.jsonl  (1 JSON object per line, mỗi line = 1 listing)
        chotot_raw_details_404.txt (list_ids trả 404/expired)

Chạy:
    python3 fetch_raw_details.py
    python3 fetch_raw_details.py --workers 20 --delay 0.1
    python3 fetch_raw_details.py --resume  (tiếp tục từ checkpoint)
"""

import os, csv, json, time, logging, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

WORKDIR     = os.path.dirname(os.path.abspath(__file__))
INPUT_CSV   = os.path.join(WORKDIR, "chotot_xe_may_merged.csv")
OUTPUT_JSONL = os.path.join(WORKDIR, "chotot_raw_details.jsonl")
OUTPUT_404  = os.path.join(WORKDIR, "chotot_raw_details_404.txt")
DETAIL_URL  = "https://gateway.chotot.com/v2/public/ad-listing"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://xe.chotot.com",
    "Referer": "https://xe.chotot.com/mua-ban-xe-may",
}

write_lock = Lock()
stats = {"done": 0, "ok": 0, "err404": 0, "err_other": 0}


def fetch_one(list_id: str, delay: float):
    """Fetch raw detail for one list_id. Returns (list_id, status_code, raw_json)."""
    time.sleep(delay)
    for attempt in range(2):
        try:
            r = requests.get(f"{DETAIL_URL}/{list_id}", headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return list_id, 200, r.json()
            elif r.status_code == 404:
                return list_id, 404, None
            else:
                time.sleep(1)
        except requests.RequestException:
            if attempt == 1:
                return list_id, -1, None
            time.sleep(1)
    return list_id, -1, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10, help="Concurrent threads (default: 10)")
    ap.add_argument("--delay",   type=float, default=0.2, help="Delay per worker in seconds (default: 0.2)")
    ap.add_argument("--resume",  action="store_true", help="Skip already-fetched IDs")
    args = ap.parse_args()

    # ── Load all list_ids from merged CSV ──────────────────────────────
    all_ids = []
    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            lid = row.get("list_id", "").strip()
            if lid.isdigit():
                all_ids.append(lid)
    log.info(f"Total list_ids to fetch: {len(all_ids):,}")

    # ── Resume: skip already done ──────────────────────────────────────
    done_ids = set()
    if args.resume:
        if os.path.exists(OUTPUT_JSONL):
            with open(OUTPUT_JSONL, encoding="utf-8") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        lid = str(obj.get("ad", {}).get("list_id") or obj.get("list_id", ""))
                        if lid: done_ids.add(lid)
                    except: pass
        if os.path.exists(OUTPUT_404):
            with open(OUTPUT_404) as f:
                for line in f:
                    done_ids.add(line.strip())
        log.info(f"Already done: {len(done_ids):,} — skipping")

    todo = [lid for lid in all_ids if lid not in done_ids]
    log.info(f"Remaining to fetch: {len(todo):,}")
    log.info(f"Workers: {args.workers} | Delay per worker: {args.delay}s")
    log.info(f"Estimated time: ~{len(todo) * args.delay / args.workers / 60:.0f} min")
    log.info(f"Output: {OUTPUT_JSONL}")

    if not todo:
        log.info("Nothing to do!")
        return

    # ── Open output files (append mode for resume) ────────────────────
    jsonl_file = open(OUTPUT_JSONL, "a", encoding="utf-8")
    f404_file  = open(OUTPUT_404, "a", encoding="utf-8")

    start_time = time.time()
    total = len(todo)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(fetch_one, lid, args.delay): lid for lid in todo}
            for future in as_completed(futures):
                lid, status, raw = future.result()

                with write_lock:
                    stats["done"] += 1
                    if status == 200 and raw:
                        stats["ok"] += 1
                        jsonl_file.write(json.dumps(raw, ensure_ascii=False) + "\n")
                    elif status == 404:
                        stats["err404"] += 1
                        f404_file.write(lid + "\n")
                    else:
                        stats["err_other"] += 1
                        log.warning(f"  {lid}: HTTP {status}")

                    # Progress every 500
                    if stats["done"] % 500 == 0 or stats["done"] == total:
                        elapsed = time.time() - start_time
                        rate = stats["done"] / elapsed
                        eta = (total - stats["done"]) / rate if rate > 0 else 0
                        log.info(
                            f"  [{stats['done']:,}/{total:,}] "
                            f"OK={stats['ok']:,} 404={stats['err404']:,} ERR={stats['err_other']:,} | "
                            f"Rate={rate:.1f}/s | ETA={eta/60:.1f}min"
                        )
                        jsonl_file.flush()
                        f404_file.flush()
    finally:
        jsonl_file.close()
        f404_file.close()

    elapsed = time.time() - start_time
    size_mb = os.path.getsize(OUTPUT_JSONL) / 1024 / 1024
    log.info(f"\n{'='*60}")
    log.info(f"DONE in {elapsed/60:.1f} min")
    log.info(f"  OK (full raw JSON):  {stats['ok']:,}")
    log.info(f"  404 (expired):       {stats['err404']:,}")
    log.info(f"  Other errors:        {stats['err_other']:,}")
    log.info(f"  Output JSONL:        {OUTPUT_JSONL}  ({size_mb:.1f} MB)")
    log.info(f"  404 list:            {OUTPUT_404}")


if __name__ == "__main__":
    main()
