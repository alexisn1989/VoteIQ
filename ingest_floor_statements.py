"""
ingest_floor_statements.py
Fetch Congressional Record (CREC) floor statements for all 13 VA federal members
from api.govinfo.gov and store them in congress_floor_statements (polls.db).

Requires: GOVINFO_API_KEY in .env  (free at https://api.govinfo.gov/docs/)
Run: python ingest_floor_statements.py [--dry-run] [--bioguide K000399] [--fetch-text]
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
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
GOVINFO_BASE = "https://api.govinfo.gov"
USER_AGENT = "VoteIQ/1.0 (civic data; alexisnieuwenhuys89@gmail.com)"
CURRENT_CONGRESS = 119
START_DATE = "2025-01-03"  # 119th Congress convened
END_DATE = date.today().isoformat()
MIN_TEXT = 300  # skip granules shorter than this (stub / co-sponsor lists)

# bioguide_id -> (display_name, chamber, last_name_for_search)
VA_MEMBERS: dict[str, tuple[str, str, str]] = {
    "W000804": ("Rob Wittman",        "House",  "WITTMAN"),
    "K000399": ("Jennifer Kiggans",   "House",  "KIGGANS"),
    "S000185": ("Bobby Scott",        "House",  "SCOTT"),
    "M001227": ("Jennifer McClellan", "House",  "MCCLELLAN"),
    "M001239": ("John McGuire",       "House",  "MCGUIRE"),
    "C001118": ("Ben Cline",          "House",  "CLINE"),
    "V000138": ("Eugene Vindman",     "House",  "VINDMAN"),
    "B001292": ("Don Beyer",          "House",  "BEYER"),
    "G000568": ("Morgan Griffith",    "House",  "GRIFFITH"),
    "S001230": ("Suhas Subramanyam",  "House",  "SUBRAMANYAM"),
    "W000831": ("James Walkinshaw",   "House",  "WALKINSHAW"),
    "W000805": ("Mark Warner",        "Senate", "WARNER"),
    "K000384": ("Tim Kaine",          "Senate", "KAINE"),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def govinfo_search_page(
    last_name: str,
    offset_mark: str = "*",
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    page_size: int = 100,
) -> dict:
    """POST to govinfo /search — GET returns 400, POST is required."""
    api_key = os.getenv("GOVINFO_API_KEY", "")
    r = requests.post(
        f"{GOVINFO_BASE}/search?api_key={api_key}",
        json={
            "query":               f"{last_name} collection:CREC",
            "pageSize":            page_size,
            "offsetMark":          offset_mark,
            "dateIssuedStartDate": start_date,
            "dateIssuedEndDate":   end_date,
        },
        headers={
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   USER_AGENT,
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def fetch_granule_text(txt_url: str) -> str | None:
    """Fetch and strip HTML from a govinfo granule htm/txt link."""
    if not txt_url:
        return None
    api_key = os.getenv("GOVINFO_API_KEY", "")
    url = f"{txt_url}?api_key={api_key}" if api_key else txt_url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    if body.lstrip().startswith("<"):
        body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) < MIN_TEXT:
        return None
    low = body[:400].lower()
    if any(m in low for m in ("page not found", "404", "access denied")):
        return None
    return body


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_floor_statements (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id     TEXT NOT NULL,
            member_name     TEXT,
            congress        INTEGER,
            chamber         TEXT,
            statement_date  TEXT,
            title           TEXT,
            text            TEXT,
            source_url      TEXT,
            fetched_at      TEXT NOT NULL,
            UNIQUE(bioguide_id, statement_date, title)
        );
        CREATE INDEX IF NOT EXISTS idx_cfs_member
            ON congress_floor_statements(bioguide_id);
        CREATE INDEX IF NOT EXISTS idx_cfs_date
            ON congress_floor_statements(statement_date DESC);
    """)
    # Migrate: add columns introduced after the table was first created
    existing = {row[1] for row in conn.execute("PRAGMA table_info(congress_floor_statements)")}
    for col, ddl in [
        ("granule_id", "ALTER TABLE congress_floor_statements ADD COLUMN granule_id TEXT"),
        ("package_id", "ALTER TABLE congress_floor_statements ADD COLUMN package_id TEXT"),
    ]:
        if col not in existing:
            conn.execute(ddl)
    conn.commit()


def ingest_member(
    conn: sqlite3.Connection,
    bioguide_id: str,
    name: str,
    chamber: str,
    last: str,
    start: str,
    end: str,
    fetch_text_flag: bool,
    dry_run: bool,
    max_pages: int | None = None,
    sleep_seconds: float = 0.4,
) -> int:
    print(f"\n[{name}] CREC search for '{last}'...", flush=True)

    offset_mark = "*"
    written = skipped = 0
    pages = 0

    while True:
        if max_pages is not None and pages >= max_pages:
            break
        try:
            data = govinfo_search_page(
                last,
                offset_mark=offset_mark,
                start_date=start,
                end_date=end,
                page_size=100,
            )
        except Exception as exc:
            print(f"  search error: {exc}", flush=True)
            break
        pages += 1

        results   = data.get("results", [])
        next_mark = data.get("offsetMark", "")
        if not results:
            break

        for item in results:
            title      = (item.get("title") or "").strip()
            stmt_date  = item.get("dateIssued", "")
            pkg_id     = item.get("packageId", "")
            gran_id    = item.get("granuleId", "")
            download   = item.get("download") or {}
            txt_url    = download.get("txtLink") or download.get("htm") or ""
            source_url = f"https://www.govinfo.gov/app/details/{pkg_id}/{gran_id}" if gran_id else ""

            if dry_run:
                print(f"  [dry] {stmt_date}  {title[:70]}", flush=True)
                written += 1
                continue

            # Skip already-stored (without fetching text unnecessarily)
            if conn.execute(
                "SELECT 1 FROM congress_floor_statements WHERE bioguide_id=? AND statement_date=? AND title=?",
                (bioguide_id, stmt_date, title),
            ).fetchone():
                skipped += 1
                continue

            text: str | None = None
            if fetch_text_flag and txt_url:
                text = fetch_granule_text(txt_url)
                time.sleep(sleep_seconds)

            try:
                conn.execute(
                    """INSERT INTO congress_floor_statements
                           (bioguide_id, member_name, congress, chamber,
                            statement_date, title, text, source_url,
                            granule_id, package_id, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(bioguide_id, statement_date, title) DO UPDATE SET
                           text       = COALESCE(excluded.text, congress_floor_statements.text),
                           source_url = excluded.source_url,
                           granule_id = excluded.granule_id,
                           package_id = excluded.package_id,
                           fetched_at = excluded.fetched_at""",
                    (bioguide_id, name, CURRENT_CONGRESS, chamber,
                     stmt_date, title, text, source_url,
                     gran_id, pkg_id, now_iso()),
                )
                conn.commit()
                written += 1
                print(f"  + {stmt_date}  {title[:70]}", flush=True)
            except Exception as exc:
                print(f"  upsert error: {exc}", flush=True)
                skipped += 1

            time.sleep(sleep_seconds)

        if not next_mark or next_mark == offset_mark:
            break
        offset_mark = next_mark

    print(f"  done: {written} stored, {skipped} skipped, {pages} pages", flush=True)
    return written


def load_members_from_db(conn: sqlite3.Connection) -> dict[str, tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT bioguide_id, name, chamber FROM congress_members"
    ).fetchall()
    out: dict[str, tuple[str, str, str]] = {}
    for bioguide_id, name, chamber in rows:
        # name is stored as "Last, First M." — extract last name for CREC search
        last = name.split(",")[0].strip().upper()
        out[bioguide_id] = (name, chamber, last)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest CREC floor statements for VA members from api.govinfo.gov."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start", "--start-date", dest="start", default=START_DATE,
                        help="Start date YYYY-MM-DD (default: 2025-01-03, 119th Congress)")
    parser.add_argument("--end", "--end-date", dest="end", default=END_DATE,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--bioguide", help="Ingest a single member by bioguide_id")
    parser.add_argument("--fetch-text", action="store_true",
                        help="Download full granule text (slower)")
    parser.add_argument("--max-pages", type=int,
                        help="Maximum GovInfo search pages to process per member")
    parser.add_argument("--sleep", type=float, default=0.4,
                        help="Seconds to sleep between GovInfo text fetches/upserts")
    args = parser.parse_args(argv)

    if not os.getenv("GOVINFO_API_KEY"):
        print("ERROR: GOVINFO_API_KEY not set in .env", file=sys.stderr)
        print("Get a free key at: https://api.govinfo.gov/docs/", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    setup_db(conn)

    # Prefer DB so newly added/removed members stay current; fall back to hardcoded
    db_members = load_members_from_db(conn)
    members: dict[str, tuple[str, str, str]] = db_members if db_members else {
        bid: (name, chamber, last) for bid, (name, chamber, last) in VA_MEMBERS.items()
    }

    if args.bioguide:
        if args.bioguide not in members:
            print(f"ERROR: {args.bioguide} not found", file=sys.stderr)
            conn.close()
            return 1
        members = {args.bioguide: members[args.bioguide]}

    print(f"Ingesting CREC statements for {len(members)} VA members")
    print(f"Date range: {args.start} to {args.end}")

    total = 0
    for bioguide_id, (name, chamber, last) in members.items():
        total += ingest_member(
            conn, bioguide_id, name, chamber, last,
            args.start, args.end,
            fetch_text_flag=args.fetch_text,
            dry_run=args.dry_run,
            max_pages=args.max_pages,
            sleep_seconds=args.sleep,
        )
        time.sleep(args.sleep)

    if not args.dry_run:
        stored = conn.execute(
            "SELECT COUNT(*) FROM congress_floor_statements"
        ).fetchone()[0]
        print(f"\nDone. {total} new statements. Total in DB: {stored:,}")
    else:
        print(f"\n[dry-run] {total} statements found.")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
