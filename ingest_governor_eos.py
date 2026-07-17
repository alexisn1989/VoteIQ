from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from voteiq.analysis.executive_orders import (
    extract_eo_from_pdf,
    extract_pdf_text_from_bytes,
    fetch_pdf_bytes,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
EXECUTIVE_ACTIONS_URL = "https://www.governor.virginia.gov/executive-actions/"
USER_AGENT = "VoteIQ/1.0 (Virginia executive orders research)"


def scrape_eo_urls(listing_url: str = EXECUTIVE_ACTIONS_URL) -> list[dict]:
    response = requests.get(
        listing_url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    eos: list[dict] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = " ".join(link.get_text(" ", strip=True).split())
        url = urljoin(listing_url, href)
        lower_url = url.lower()

        if ".pdf" not in lower_url:
            continue
        if "/pdf/eo/" not in lower_url and "/pdf/ed/" not in lower_url:
            continue
        if url in seen:
            continue

        title = text or _title_from_url(url)
        eos.append({"title": title, "url": url})
        seen.add(url)

    return eos


def _title_from_url(url: str) -> str:
    filename = url.rsplit("/", 1)[-1]
    filename = re.sub(r"\.pdf$", "", filename, flags=re.I)
    filename = filename.replace("%2C", ",").replace("%20", " ")
    filename = filename.replace("-", " ").replace("_", " ")
    return " ".join(filename.split())


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS governor_executive_orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number    TEXT,
            title           TEXT,
            signed_date     TEXT,
            governor        TEXT,
            jurisdiction    TEXT,
            summary         TEXT,
            policy_topics   TEXT,
            agencies        TEXT,
            actions_required TEXT,
            effective_date  TEXT,
            full_text       TEXT,
            source_type     TEXT DEFAULT 'executive order',
            source_url      TEXT NOT NULL UNIQUE,
            raw_json        TEXT,
            fetched_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_governor_eos_signed_date
            ON governor_executive_orders(signed_date);
        CREATE INDEX IF NOT EXISTS idx_governor_eos_governor
            ON governor_executive_orders(governor);
    """)
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(governor_executive_orders)").fetchall()}
    if "full_text" not in columns:
        conn.execute("ALTER TABLE governor_executive_orders ADD COLUMN full_text TEXT")
        conn.commit()


def store_eo(
    conn: sqlite3.Connection,
    source_url: str,
    fallback_title: str,
    data: dict,
    full_text: str | None = None,
) -> None:
    conn.execute("""
        INSERT INTO governor_executive_orders (
            order_number,
            title,
            signed_date,
            governor,
            jurisdiction,
            summary,
            policy_topics,
            agencies,
            actions_required,
            effective_date,
            full_text,
            source_type,
            source_url,
            raw_json,
            fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_url) DO UPDATE SET
            order_number = excluded.order_number,
            title = excluded.title,
            signed_date = excluded.signed_date,
            governor = excluded.governor,
            jurisdiction = excluded.jurisdiction,
            summary = excluded.summary,
            policy_topics = excluded.policy_topics,
            agencies = excluded.agencies,
            actions_required = excluded.actions_required,
            effective_date = excluded.effective_date,
            full_text = COALESCE(excluded.full_text, governor_executive_orders.full_text),
            source_type = excluded.source_type,
            raw_json = excluded.raw_json,
            fetched_at = excluded.fetched_at
    """, (
        data.get("order_number"),
        data.get("title") or fallback_title,
        data.get("signed_date"),
        data.get("governor"),
        data.get("jurisdiction"),
        data.get("summary"),
        json.dumps(data.get("policy_topics") or [], ensure_ascii=False),
        json.dumps(data.get("agencies") or [], ensure_ascii=False),
        json.dumps(data.get("actions_required") or [], ensure_ascii=False),
        data.get("effective_date"),
        full_text,
        data.get("source_type") or "executive order",
        source_url,
        json.dumps(data, ensure_ascii=False),
        now_iso(),
    ))
    conn.commit()


def ingest_all(db_path: str = str(DB_PATH), force: bool = False, limit: int | None = None) -> int:
    eos = scrape_eo_urls()
    if limit:
        eos = eos[:limit]
    print(f"Found {len(eos)} EO URLs")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    setup_db(conn)

    stored = skipped = failed = 0
    try:
        for idx, eo in enumerate(eos, start=1):
            title = eo["title"]
            url = eo["url"]
            if not force:
                exists = conn.execute(
                    "SELECT 1 FROM governor_executive_orders WHERE source_url = ?",
                    (url,),
                ).fetchone()
                if exists:
                    skipped += 1
                    print(f"[{idx}/{len(eos)}] skip existing: {title}", flush=True)
                    continue

            print(f"[{idx}/{len(eos)}] extracting: {title}", flush=True)
            try:
                pdf_bytes = fetch_pdf_bytes(url)
                full_text = extract_pdf_text_from_bytes(pdf_bytes)
                data = extract_eo_from_pdf(url)
                store_eo(conn, url, title, data, full_text=full_text)
                stored += 1
                print(f"  + stored {data.get('order_number') or ''} {data.get('title') or title}", flush=True)
            except Exception as exc:
                failed += 1
                print(f"  ! failed: {exc}", flush=True)
            time.sleep(0.8)
    finally:
        conn.close()

    print(f"\nDone. {stored} stored, {skipped} skipped, {failed} failed.")
    return stored


def backfill_full_text(db_path: str = str(DB_PATH)) -> int:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    setup_db(conn)
    rows = conn.execute("""
        SELECT id, title, source_url
        FROM governor_executive_orders
        WHERE COALESCE(full_text, '') = ''
        ORDER BY id
    """).fetchall()

    updated = 0
    try:
        for row_id, title, url in rows:
            print(f"Backfilling full_text: {title}", flush=True)
            try:
                pdf_bytes = fetch_pdf_bytes(url)
                full_text = extract_pdf_text_from_bytes(pdf_bytes)
                conn.execute(
                    "UPDATE governor_executive_orders SET full_text = ?, fetched_at = ? WHERE id = ?",
                    (full_text, now_iso(), row_id),
                )
                conn.commit()
                updated += 1
            except Exception as exc:
                print(f"  ! failed: {exc}", flush=True)
            time.sleep(0.4)
    finally:
        conn.close()
    print(f"Backfilled full_text for {updated} rows.")
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest Virginia governor executive orders.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--force", action="store_true", help="Re-extract already stored PDFs")
    parser.add_argument("--backfill-text", action="store_true", help="Only backfill full_text for stored rows")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    if args.backfill_text:
        backfill_full_text(db_path=args.db)
    else:
        ingest_all(db_path=args.db, force=args.force, limit=args.limit)
    return 0


__all__ = ["extract_eo_from_pdf", "scrape_eo_urls"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
