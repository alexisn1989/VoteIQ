#!/usr/bin/env python3
"""
Bulk-download Virginia SBE Report.csv files for every monthly period
from 2012 through 2026 and import them into polls.db:va_cf_reports.

Usage:
    python bulk_download_va_reports.py
    python bulk_download_va_reports.py --start 2021_01 --end 2023_12
    python bulk_download_va_reports.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DB_PATH  = DATA_DIR / "polls.db"
BASE_URL = "https://apps.elections.virginia.gov/SBE_CSV/CF"
DATA_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": f"{BASE_URL}/",
}

CSV_ENCODINGS = ("cp1252", "utf-8-sig", "latin-1", "utf-8")


def all_periods() -> list[str]:
    """Return all period folder names from the SBE directory listing."""
    req = urllib.request.Request(f"{BASE_URL}/", headers=HEADERS)
    html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
    # Strip JS, extract text
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", html)
    # Match year or year_month folder names
    return sorted(set(re.findall(r"\b(\d{4}(?:_\d{2})?)\b", text)))


def fetch_csv(period: str, filename: str) -> bytes | None:
    url = f"{BASE_URL}/{period}/{filename}"
    hdrs = {**HEADERS, "Referer": f"{BASE_URL}/{period}/"}
    try:
        req = urllib.request.Request(url, headers=hdrs)
        r = urllib.request.urlopen(req, timeout=60)
        return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 503):
            return None
        raise


def decode_csv(data: bytes) -> list[dict]:
    for enc in CSV_ENCODINGS:
        try:
            text = data.decode(enc)
            reader = csv.DictReader(io.StringIO(text))
            return list(reader)
        except (UnicodeDecodeError, csv.Error):
            continue
    return []


def ensure_table(conn: sqlite3.Connection, columns: list[str]) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(va_cf_reports)").fetchall()}
    for col in columns + ["source_period", "imported_at"]:
        if col not in existing:
            conn.execute(f'ALTER TABLE va_cf_reports ADD COLUMN "{col}" TEXT')
    conn.commit()


def import_rows(conn: sqlite3.Connection, rows: list[dict], period: str) -> int:
    if not rows:
        return 0

    columns = list(rows[0].keys())
    ensure_table(conn, columns)

    all_cols = columns + ["source_period", "imported_at"]
    placeholders = ", ".join("?" for _ in all_cols)
    quoted = ", ".join(f'"{c}"' for c in all_cols)
    pk = "ReportId"

    update_cols = [c for c in all_cols if c != pk]
    update_clause = ", ".join(f'"{c}" = excluded."{c}"' for c in update_cols)

    sql = (
        f'INSERT INTO va_cf_reports ({quoted}) VALUES ({placeholders}) '
        f'ON CONFLICT ("{pk}") DO UPDATE SET {update_clause}'
    )

    written = 0
    for row in rows:
        vals = [row.get(c, "") for c in columns] + [period, "now"]
        try:
            conn.execute(sql, vals)
            written += 1
        except sqlite3.Error:
            pass
    conn.commit()
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",   default="2012_03", help="First period e.g. 2012_03")
    parser.add_argument("--end",     default="2026_05", help="Last period  e.g. 2026_05")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Fetching period list from SBE…")
    periods = all_periods()
    periods = [p for p in periods if args.start <= p <= args.end]
    print(f"  {len(periods)} periods in range {args.start} – {args.end}")

    if args.dry_run:
        print("Dry-run: would process:", periods[:5], "…")
        return

    conn = sqlite3.connect(DB_PATH)
    # Ensure base table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS va_cf_reports (
            "ReportId" TEXT,
            source_period TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY ("ReportId")
        )
    """)
    conn.commit()

    total_new = 0
    for i, period in enumerate(periods, 1):
        print(f"  [{i}/{len(periods)}] {period} … ", end="", flush=True)
        data = fetch_csv(period, "Report.csv")
        if data is None:
            print("no file")
            continue
        rows = decode_csv(data)
        if not rows:
            print("empty/unreadable")
            continue
        written = import_rows(conn, rows, period)
        total_new += written
        print(f"{written} rows (total so far: {total_new})")
        time.sleep(0.5)  # be polite to the server

    final = conn.execute("SELECT COUNT(*) FROM va_cf_reports").fetchone()[0]
    print(f"\nDone. va_cf_reports now has {final:,} rows ({total_new} inserted/updated this run).")
    conn.close()


if __name__ == "__main__":
    main()
