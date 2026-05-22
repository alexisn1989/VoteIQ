"""
ingest_hearings.py
Fetch Congressional Hearing (CHRG) records for VA federal members
from api.govinfo.gov and store them in congress_hearings (polls.db).

Hearings capture committee testimony, questioning, and witness appearances
that don't show up in floor statements (CREC).

Requires: GOVINFO_API_KEY in .env
Run:
    python ingest_hearings.py                        # all VA Senate members
    python ingest_hearings.py --bioguide W000805     # Warner only
    python ingest_hearings.py --fetch-text           # include full transcript text
    python ingest_hearings.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
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
START_DATE = "2025-01-03"
END_DATE = date.today().isoformat()
MIN_TEXT = 300

# Senators first — richest hearing records. House members included too.
VA_MEMBERS: dict[str, tuple[str, str, str]] = {
    "W000805": ("Mark Warner",        "Senate", "WARNER"),
    "K000384": ("Tim Kaine",          "Senate", "KAINE"),
    "W000804": ("Rob Wittman",        "House",  "WITTMAN"),
    "K000399": ("Jennifer Kiggans",   "House",  "KIGGANS"),
    "S000185": ("Bobby Scott",        "House",  "SCOTT"),
    "M001227": ("Jennifer McClellan", "House",  "MCCLELLAN"),
    "B001292": ("Don Beyer",          "House",  "BEYER"),
    "G000568": ("Morgan Griffith",    "House",  "GRIFFITH"),
    "V000138": ("Eugene Vindman",     "House",  "VINDMAN"),
    "S001230": ("Suhas Subramanyam",  "House",  "SUBRAMANYAM"),
    "M001239": ("John McGuire",       "House",  "MCGUIRE"),
    "C001118": ("Ben Cline",          "House",  "CLINE"),
    "W000831": ("James Walkinshaw",   "House",  "WALKINSHAW"),
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
    api_key = os.getenv("GOVINFO_API_KEY", "")
    r = requests.post(
        f"{GOVINFO_BASE}/search?api_key={api_key}",
        json={
            "query":               f"{last_name} collection:CHRG",
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


def _extract_committee(title: str, granule_class: str) -> str:
    """Best-effort committee extraction from hearing title."""
    title_l = (title or "").lower()
    for keyword, committee in [
        ("intelligence",          "Senate Select Committee on Intelligence"),
        ("banking",               "Senate Banking Committee"),
        ("armed services",        "Senate Armed Services Committee"),
        ("foreign relations",     "Senate Foreign Relations Committee"),
        ("judiciary",             "Senate Judiciary Committee"),
        ("finance",               "Senate Finance Committee"),
        ("commerce",              "Senate Commerce Committee"),
        ("homeland security",     "Senate Homeland Security Committee"),
        ("appropriations",        "Appropriations Committee"),
        ("veterans",              "Veterans Affairs Committee"),
        ("education",             "Education Committee"),
        ("energy",                "Energy Committee"),
        ("environment",           "Environment & Public Works Committee"),
        ("rules",                 "Rules Committee"),
        ("budget",                "Budget Committee"),
        ("ways and means",        "House Ways and Means Committee"),
        ("oversight",             "House Oversight Committee"),
        ("foreign affairs",       "House Foreign Affairs Committee"),
        ("science, space",        "House Science Committee"),
        ("small business",        "Small Business Committee"),
        ("transportation",        "Transportation Committee"),
    ]:
        if keyword in title_l:
            return committee
    return granule_class or ""


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_hearings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id    TEXT NOT NULL,
            member_name    TEXT,
            congress       INTEGER,
            chamber        TEXT,
            committee      TEXT,
            hearing_date   TEXT,
            title          TEXT,
            text           TEXT,
            source_url     TEXT,
            granule_id     TEXT,
            package_id     TEXT,
            fetched_at     TEXT NOT NULL,
            UNIQUE(bioguide_id, hearing_date, title)
        );
        CREATE INDEX IF NOT EXISTS idx_ch_member
            ON congress_hearings(bioguide_id);
        CREATE INDEX IF NOT EXISTS idx_ch_date
            ON congress_hearings(hearing_date DESC);
        CREATE INDEX IF NOT EXISTS idx_ch_committee
            ON congress_hearings(committee);
    """)
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
    print(f"\n[{name}] CHRG search for '{last}'...", flush=True)

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
            gran_class = item.get("granuleClass", "")
            download   = item.get("download") or {}
            txt_url    = download.get("txtLink") or download.get("htm") or ""
            source_url = f"https://www.govinfo.gov/app/details/{pkg_id}/{gran_id}" if gran_id else (
                f"https://www.govinfo.gov/app/details/{pkg_id}" if pkg_id else ""
            )
            committee = _extract_committee(title, gran_class)

            if dry_run:
                print(f"  [dry] {stmt_date}  [{committee or 'unknown committee'}]  {title[:60]}", flush=True)
                written += 1
                continue

            if conn.execute(
                "SELECT 1 FROM congress_hearings WHERE bioguide_id=? AND hearing_date=? AND title=?",
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
                    """INSERT INTO congress_hearings
                           (bioguide_id, member_name, congress, chamber,
                            committee, hearing_date, title, text,
                            source_url, granule_id, package_id, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(bioguide_id, hearing_date, title) DO UPDATE SET
                           text       = COALESCE(excluded.text, congress_hearings.text),
                           committee  = excluded.committee,
                           source_url = excluded.source_url,
                           granule_id = excluded.granule_id,
                           package_id = excluded.package_id,
                           fetched_at = excluded.fetched_at""",
                    (bioguide_id, name, CURRENT_CONGRESS, chamber,
                     committee, stmt_date, title, text,
                     source_url, gran_id, pkg_id, now_iso()),
                )
                conn.commit()
                written += 1
                print(f"  + {stmt_date}  [{committee or 'committee'}]  {title[:60]}", flush=True)
            except Exception as exc:
                print(f"  upsert error: {exc}", flush=True)
                skipped += 1

            time.sleep(sleep_seconds)

        if not next_mark or next_mark == offset_mark:
            break
        offset_mark = next_mark

    print(f"  done: {written} stored, {skipped} skipped, {pages} pages", flush=True)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest CHRG congressional hearing records for VA members from api.govinfo.gov."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start", dest="start", default=START_DATE)
    parser.add_argument("--end",   dest="end",   default=END_DATE)
    parser.add_argument("--bioguide", help="Single member by bioguide_id")
    parser.add_argument("--fetch-text", action="store_true",
                        help="Download full hearing transcript text (slower)")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--sleep", type=float, default=0.4)
    args = parser.parse_args(argv)

    if not os.getenv("GOVINFO_API_KEY"):
        print("ERROR: GOVINFO_API_KEY not set in .env", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    setup_db(conn)

    members = dict(VA_MEMBERS)
    if args.bioguide:
        if args.bioguide not in members:
            print(f"ERROR: {args.bioguide} not found", file=sys.stderr)
            conn.close()
            return 1
        members = {args.bioguide: members[args.bioguide]}

    print(f"Ingesting CHRG hearings for {len(members)} VA members")
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

    stored = conn.execute("SELECT COUNT(*) FROM congress_hearings").fetchone()[0]
    print(f"\nDone. {total} new hearings stored. Total in DB: {stored:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
