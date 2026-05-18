#!/usr/bin/env python3
"""
Ingest Virginia congressional member profiles and sponsored legislation
from the Congress.gov API v3.

Stores data in polls.db:
  congress_members  — all 13 current VA members (House + Senate)
  congress_bills    — bills sponsored or cosponsored by VA members

Examples:
    python ingest_congress.py
    python ingest_congress.py --dry-run
    python ingest_congress.py --congress 119
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"
CONGRESS_API_BASE = "https://api.congress.gov/v3"
USER_AGENT = "VoteIQ/1.0 (civic data; alexisnieuwenhuys89@gmail.com)"
CURRENT_CONGRESS = 119


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_members (
            bioguide_id     TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            party           TEXT,
            chamber         TEXT,
            state           TEXT,
            district        TEXT,
            website         TEXT,
            fetched_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS congress_bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            congress        INTEGER NOT NULL,
            bill_type       TEXT NOT NULL,
            bill_number     TEXT NOT NULL,
            title           TEXT,
            introduced_date TEXT,
            policy_area     TEXT,
            latest_action   TEXT,
            latest_action_date TEXT,
            sponsor_id      TEXT,
            role            TEXT,
            api_url         TEXT,
            fetched_at      TEXT NOT NULL,
            UNIQUE(congress, bill_type, bill_number, sponsor_id, role)
        );

        CREATE INDEX IF NOT EXISTS idx_congress_bills_sponsor
            ON congress_bills(sponsor_id);
        CREATE INDEX IF NOT EXISTS idx_congress_bills_date
            ON congress_bills(introduced_date DESC);
    """)
    conn.commit()


def api_get(path: str, api_key: str, **params) -> dict:
    params["api_key"] = api_key
    qs = urllib.parse.urlencode(params)
    url = f"{CONGRESS_API_BASE}/{path}?{qs}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def fetch_va_members(api_key: str) -> list[dict]:
    """Page through all current members and return Virginia ones."""
    va = []
    offset = 0
    while True:
        data = api_get("member", api_key, currentMember="true", limit=250, offset=offset)
        members = data.get("members", [])
        if not members:
            break
        for m in members:
            if m.get("state") == "Virginia":
                va.append(m)
        total = data.get("pagination", {}).get("count", 0)
        offset += len(members)
        if offset >= total:
            break
    return va


def fetch_member_detail(bioguide_id: str, api_key: str) -> dict:
    data = api_get(f"member/{bioguide_id}", api_key)
    return data.get("member", {})


def fetch_legislation(bioguide_id: str, role: str, api_key: str,
                      limit: int = 50) -> list[dict]:
    """role: 'sponsored' or 'cosponsored'"""
    key = "sponsoredLegislation" if role == "sponsored" else "cosponsoredLegislation"
    path = f"member/{bioguide_id}/{role}-legislation"
    try:
        data = api_get(path, api_key, limit=limit)
        return data.get(key, [])
    except Exception as exc:
        print(f"    Warning: could not fetch {role} legislation: {exc}")
        return []


def upsert_member(conn: sqlite3.Connection, m: dict, detail: dict,
                  dry_run: bool) -> None:
    bid = m.get("bioguideId", "")
    name = m.get("name", "")
    party = m.get("partyName", "")
    state = m.get("state", "")
    district = str(m.get("district", "S"))
    terms = m.get("terms", {}).get("item", [])
    chamber = terms[-1].get("chamber", "") if terms else ""
    website = detail.get("officialWebsiteUrl", "")

    if dry_run:
        print(f"  [dry] member: {name} ({party}) VA-{district} {chamber[:5]}")
        return

    conn.execute(
        """INSERT INTO congress_members
               (bioguide_id, name, party, chamber, state, district, website, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(bioguide_id) DO UPDATE SET
               name=excluded.name, party=excluded.party, chamber=excluded.chamber,
               state=excluded.state, district=excluded.district,
               website=excluded.website, fetched_at=excluded.fetched_at""",
        (bid, name, party, chamber, state, district, website, now_iso()),
    )


def upsert_bills(conn: sqlite3.Connection, bills: list[dict], sponsor_id: str,
                 role: str, dry_run: bool) -> int:
    written = 0
    for b in bills:
        congress = b.get("congress")
        btype = (b.get("type") or "").lower()
        bnum = str(b.get("number", ""))
        title = (b.get("title") or "")[:400]
        intro = b.get("introducedDate")
        policy = (b.get("policyArea") or {}).get("name")
        action = b.get("latestAction") or {}
        action_text = (action.get("text") or "")[:300]
        action_date = action.get("actionDate")
        api_url = b.get("url", "")

        if dry_run:
            print(f"    [dry] {btype.upper()}{bnum} ({role}): {title[:60]}")
            written += 1
            continue

        conn.execute(
            """INSERT INTO congress_bills
                   (congress, bill_type, bill_number, title, introduced_date,
                    policy_area, latest_action, latest_action_date,
                    sponsor_id, role, api_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(congress, bill_type, bill_number, sponsor_id, role)
               DO UPDATE SET
                   title=excluded.title,
                   latest_action=excluded.latest_action,
                   latest_action_date=excluded.latest_action_date,
                   fetched_at=excluded.fetched_at""",
            (congress, btype, bnum, title, intro, policy,
             action_text, action_date, sponsor_id, role, api_url, now_iso()),
        )
        written += 1
    return written


def ingest(conn: sqlite3.Connection, api_key: str, dry_run: bool,
           bill_limit: int = 50) -> None:
    print("Fetching current Virginia members...")
    members = fetch_va_members(api_key)
    print(f"  Found {len(members)} VA members")

    for m in members:
        bid = m.get("bioguideId", "")
        name = m.get("name", "")
        district = m.get("district", "S")
        print(f"\n  VA-{district} {name} [{bid}]")

        detail = fetch_member_detail(bid, api_key)
        upsert_member(conn, m, detail, dry_run)
        time.sleep(0.3)

        for role in ("sponsored", "cosponsored"):
            bills = fetch_legislation(bid, role, api_key, limit=bill_limit)
            n = upsert_bills(conn, bills, bid, role, dry_run)
            print(f"    {role}: {n} bills")
            time.sleep(0.3)

    if not dry_run:
        conn.commit()
        total_bills = conn.execute("SELECT COUNT(*) FROM congress_bills").fetchone()[0]
        total_members = conn.execute("SELECT COUNT(*) FROM congress_members").fetchone()[0]
        print(f"\nDone. {total_members} members, {total_bills} bill records in DB.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest VA congressional members and bills from Congress.gov."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--congress", type=int, default=CURRENT_CONGRESS)
    parser.add_argument("--bill-limit", type=int, default=50,
                        help="Max sponsored/cosponsored bills to fetch per member")
    parser.add_argument("--api-key", default=os.getenv("CONGRESS_API_KEY", ""),
                        help="Congress.gov API key (or set CONGRESS_API_KEY env var)")
    args = parser.parse_args(argv)

    if not args.api_key:
        print("ERROR: CONGRESS_API_KEY not set.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    setup_db(conn)
    ingest(conn, args.api_key, args.dry_run, bill_limit=args.bill_limit)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
