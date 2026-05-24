#!/usr/bin/env python3
"""
Download all Virginia SBE campaign finance CSVs from:
  https://apps.elections.virginia.gov/SBE_CSV/CF/

Files are saved to finance_csv/<period>/ and skipped if already present.
Excludes 2026_04 and 2026_05 (already downloaded separately).

Usage:
    python download_va_finance.py
    python download_va_finance.py --since 2020_01   # only periods >= 2020_01
    python download_va_finance.py --workers 4       # parallel downloads
"""

import argparse
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE_URL = "https://apps.elections.virginia.gov/SBE_CSV/CF"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
OUT_DIR = DATA_DIR / "finance_csv"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SKIP = {"2026_04", "2026_05"}
CSV_FILES = [
    "Report.csv",
    "ScheduleA.csv",
    "ScheduleB.csv",
    "ScheduleC.csv",
    "ScheduleD.csv",
    "ScheduleE.csv",
    "ScheduleF.csv",
    "ScheduleG.csv",
    "ScheduleH.csv",
    "ScheduleI.csv",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def list_periods(session: requests.Session) -> list[str]:
    resp = session.get(f"{BASE_URL}/", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return re.findall(r'HREF="/SBE_CSV/CF/([^/"]+)/"', resp.text)


def download_file(session: requests.Session, period: str, filename: str) -> tuple[str, bool, str]:
    dest = OUT_DIR / period / filename
    if dest.exists() and dest.stat().st_size > 0:
        return f"{period}/{filename}", False, "skipped"

    url = f"{BASE_URL}/{period}/{filename}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=60, stream=True)
        if resp.status_code == 404:
            return f"{period}/{filename}", False, "404"
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
        return f"{period}/{filename}", True, "ok"
    except Exception as e:
        return f"{period}/{filename}", False, f"error: {e}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None, help="Skip periods before this (e.g. 2020_01)")
    parser.add_argument("--workers", type=int, default=4, help="Parallel download threads (default 4)")
    args = parser.parse_args()

    session = requests.Session()

    print("Fetching directory listing...")
    all_periods = list_periods(session)
    periods = [p for p in all_periods if p not in SKIP]
    if args.since:
        periods = [p for p in periods if p >= args.since]

    print(f"Periods to download: {len(periods)}  (skipping {SKIP})")

    tasks = [(period, f) for period in periods for f in CSV_FILES]
    total = len(tasks)
    done = skipped = errors = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_file, session, period, f): (period, f) for period, f in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            path, downloaded, status = future.result()
            if status == "skipped":
                skipped += 1
            elif status == "ok":
                done += 1
                print(f"[{i}/{total}] {path}")
            elif status == "404":
                skipped += 1
            else:
                errors += 1
                print(f"[{i}/{total}] FAIL {path} — {status}")

            if i % 100 == 0:
                print(f"  progress: {i}/{total} ({done} new, {skipped} skipped, {errors} errors)")
            time.sleep(0.05)

    print(f"\nDone. {done} downloaded, {skipped} skipped/missing, {errors} errors.")
    print(f"Files saved to: {OUT_DIR}")


if __name__ == "__main__":
    main()
