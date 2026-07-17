#!/usr/bin/env python3
"""
Ingest Congressional Record (CREC) floor statements for Virginia members
from api.govinfo.gov into polls.db (congress_floor_statements table).

The govinfo API is free and works without a key (rate-limited).
Set GOVINFO_API_KEY in .env to lift rate limits.

Examples:
    python ingest_crec.py
    python ingest_crec.py --start 2025-01-01 --end 2025-05-19
    python ingest_crec.py --dry-run
    python ingest_crec.py --fetch-text
    python ingest_crec.py --bioguide K000394   # single member
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
GOVINFO_BASE = "https://api.govinfo.gov"
USER_AGENT = "VoteIQ/1.0 (civic data; alexisnieuwenhuys89@gmail.com)"
CURRENT_CONGRESS = 119


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_floor_statements (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id     TEXT NOT NULL,
            member_name     TEXT NOT NULL,
            congress        INTEGER,
            chamber         TEXT,
            statement_date  TEXT,
            title           TEXT,
            text            TEXT,
            source_url      TEXT,
            granule_id      TEXT UNIQUE,
            package_id      TEXT,
            granule_class   TEXT,
            fetched_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_floor_bioguide
            ON congress_floor_statements(bioguide_id);
        CREATE INDEX IF NOT EXISTS idx_floor_date
            ON congress_floor_statements(statement_date DESC);
    """)
    conn.commit()


def govinfo_get(path: str, **params) -> dict:
    api_key = os.getenv("GOVINFO_API_KEY", "")
    if api_key:
        params["api_key"] = api_key
    qs = urllib.parse.urlencode(params)
    url = f"{GOVINFO_BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def fetch_text(url: str) -> str | None:
    """Download a granule text/HTM URL and return stripped plain text."""
    if not url:
        return None
    api_key = os.getenv("GOVINFO_API_KEY", "")
    full_url = f"{url}?api_key={api_key}" if api_key and "?" not in url else url
    try:
        req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    if "html" in content_type or body.lstrip().startswith("<"):
        body = BeautifulSoup(body, "html.parser").get_text("\n").strip()
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 50:
        return None
    return body


def search_crec_granules(
    last_name: str,
    start: str,
    end: str,
    page_size: int = 100,
) -> list[dict]:
    """Page through govinfo search results for a member's CREC granules.
    Uses POST — the /search endpoint returns 400 on GET requests."""
    api_key = os.getenv("GOVINFO_API_KEY", "")
    results: list[dict] = []
    offset_mark = "*"

    while True:
        try:
            r = requests.post(
                f"{GOVINFO_BASE}/search?api_key={api_key}",
                json={
                    "query":               f"{last_name} collection:CREC",
                    "pageSize":            page_size,
                    "offsetMark":          offset_mark,
                    "dateIssuedStartDate": start,
                    "dateIssuedEndDate":   end,
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                    "User-Agent":   USER_AGENT,
                },
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f"    Warning: search error for {last_name}: {exc}")
            break

        batch = data.get("results", [])
        results.extend(batch)

        next_mark = data.get("offsetMark")
        if not batch or not next_mark or next_mark == offset_mark:
            break
        offset_mark = next_mark
        time.sleep(0.4)

    return results


def upsert_statement(
    conn: sqlite3.Connection,
    bioguide_id: str,
    member_name: str,
    chamber: str,
    r: dict,
    text: str | None,
    dry_run: bool,
) -> int:
    granule_id = r.get("granuleId", "")
    package_id = r.get("packageId", "")
    title = (r.get("title") or "")[:400]
    stmt_date = r.get("dateIssued", "")
    granule_class = r.get("granuleClass", "")
    download = r.get("download") or {}
    source_url = download.get("txtLink") or download.get("pdfLink") or ""

    if dry_run:
        print(f"    [dry] {stmt_date}  {title[:70]}")
        return 1

    conn.execute(
        """INSERT INTO congress_floor_statements
               (bioguide_id, member_name, congress, chamber, statement_date,
                title, text, source_url, granule_id, package_id, granule_class, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(granule_id) DO UPDATE SET
               title=excluded.title,
               text=COALESCE(excluded.text, congress_floor_statements.text),
               source_url=excluded.source_url,
               fetched_at=excluded.fetched_at""",
        (
            bioguide_id, member_name, CURRENT_CONGRESS, chamber,
            stmt_date, title, text, source_url,
            granule_id, package_id, granule_class, now_iso(),
        ),
    )
    return 1


def ingest_member(
    conn: sqlite3.Connection,
    bioguide_id: str,
    member_name: str,
    chamber: str,
    start: str,
    end: str,
    fetch_text_flag: bool,
    refresh_text: bool,
    dry_run: bool,
) -> int:
    last = member_name.split(",")[0].strip().upper()
    print(f"  Searching CREC for {member_name} [{last}] {start} to {end}")

    granules = search_crec_granules(last, start, end)
    print(f"    {len(granules)} granules found")

    written = 0
    for r in granules:
        granule_id = r.get("granuleId", "")

        text: str | None = None
        if fetch_text_flag and granule_id:
            if not refresh_text and not dry_run:
                existing = conn.execute(
                    "SELECT text FROM congress_floor_statements WHERE granule_id = ?",
                    (granule_id,),
                ).fetchone()
                if existing and existing[0]:
                    written += upsert_statement(conn, bioguide_id, member_name, chamber, r, None, dry_run)
                    continue

            download = r.get("download") or {}
            txt_url = download.get("txtLink") or download.get("xmlLink")
            if txt_url:
                text = fetch_text(txt_url)
            time.sleep(0.25)

        written += upsert_statement(conn, bioguide_id, member_name, chamber, r, text, dry_run)

    return written


def load_va_members(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return (bioguide_id, name, chamber) for all members in DB."""
    rows = conn.execute(
        "SELECT bioguide_id, name, chamber FROM congress_members ORDER BY chamber, name"
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def ingest(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    bioguide_filter: str | None,
    fetch_text_flag: bool,
    refresh_text: bool,
    dry_run: bool,
) -> None:
    members = load_va_members(conn)
    if not members:
        print("ERROR: no members in congress_members table. Run ingest_congress.py first.")
        return

    if bioguide_filter:
        members = [m for m in members if m[0] == bioguide_filter]
        if not members:
            print(f"ERROR: bioguide_id {bioguide_filter} not found in congress_members.")
            return

    total = 0
    for bioguide_id, member_name, chamber in members:
        count = ingest_member(
            conn, bioguide_id, member_name, chamber,
            start, end, fetch_text_flag, refresh_text, dry_run,
        )
        total += count
        if not dry_run:
            conn.commit()
        time.sleep(0.5)

    if not dry_run:
        stored = conn.execute("SELECT COUNT(*) FROM congress_floor_statements").fetchone()[0]
        print(f"\nDone. {total} rows written. {stored} total statements in DB.")
    else:
        print(f"\n[dry-run] {total} rows would be written.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest CREC floor statements for VA members from api.govinfo.gov."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--start", default="2025-01-01",
        help="Start date YYYY-MM-DD (default: 2025-01-01)",
    )
    parser.add_argument(
        "--end", default=date.today().isoformat(),
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument("--bioguide", help="Ingest a single member by bioguide_id")
    parser.add_argument(
        "--fetch-text", action="store_true",
        help="Download full statement text (slower, more data)",
    )
    parser.add_argument(
        "--refresh-text", action="store_true",
        help="Re-download text even for rows that already have it",
    )
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    setup_db(conn)
    ingest(
        conn,
        start=args.start,
        end=args.end,
        bioguide_filter=args.bioguide,
        fetch_text_flag=args.fetch_text,
        refresh_text=args.refresh_text,
        dry_run=args.dry_run,
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
