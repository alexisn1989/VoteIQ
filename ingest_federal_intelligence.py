#!/usr/bin/env python3
"""
Ingest sponsored bills and votes from Congress.gov for Virginia delegation,
then create the federal_intelligence three-way correlation view
(donors × bills × votes) in polls.db.

Usage:
    python ingest_federal_intelligence.py              # all members, congress 119
    python ingest_federal_intelligence.py --congress 118
    python ingest_federal_intelligence.py --bioguide K000399   # single member
    python ingest_federal_intelligence.py --view-only          # just rebuild the view
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).resolve().parent / "polls.db"
BASE = "https://api.congress.gov/v3"
USER_AGENT = "VoteIQ/1.0 (Virginia civic intelligence research)"

# Maps FEC candidate_id → bioguide_id for the three-way join
FEC_TO_BIOGUIDE = {
    "H2VA02064": "K000399",  # Kiggans
    "H6VA01117": "S000185",  # Bobby Scott
    "H8VA01147": "W000804",  # Wittman
    "S6VA00093": "W000805",  # Warner
    "S2VA00142": "K000384",  # Kaine
    "H4VA04066": "M001239",  # McClellan
    "H4VA08224": "B001292",  # Beyer
    "H8VA11062": "C001118",  # Connolly
    "H0VA09055": "G000568",  # Griffith
    "H0VA05160": "G000599",  # Good
    "H8VA06104": "C001025",  # Cline
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bioguide_fec_bridge (
            bioguide_id TEXT PRIMARY KEY,
            fec_candidate_id TEXT
        );

        CREATE TABLE IF NOT EXISTS federal_bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id     TEXT,
            congress        INTEGER,
            bill_number     TEXT,
            bill_type       TEXT,
            title           TEXT,
            introduced_date TEXT,
            policy_area     TEXT,
            subjects        TEXT,   -- JSON array
            status          TEXT,
            fetched_at      TEXT,
            UNIQUE(bioguide_id, congress, bill_number)
        );

        CREATE TABLE IF NOT EXISTS federal_votes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id TEXT,
            bill_number TEXT,
            congress    INTEGER,
            chamber     TEXT,
            vote        TEXT,       -- Yea / Nay / Not Voting
            vote_date   TEXT,
            roll_call   TEXT,
            fetched_at  TEXT,
            UNIQUE(bioguide_id, congress, roll_call)
        );
    """)
    conn.commit()


def populate_bridge(conn: sqlite3.Connection) -> None:
    for fec_id, bioguide_id in FEC_TO_BIOGUIDE.items():
        conn.execute("""
            INSERT INTO bioguide_fec_bridge (bioguide_id, fec_candidate_id)
            VALUES (?, ?)
            ON CONFLICT(bioguide_id) DO UPDATE SET fec_candidate_id = excluded.fec_candidate_id
        """, (bioguide_id, fec_id))
    conn.commit()


# ── Congress.gov fetchers ────────────────────────────────────────────────────

def _get(url: str, params: dict, api_key: str) -> dict:
    params["api_key"] = api_key
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
            if r.status_code == 429:
                wait = 2 ** attempt * 3
                print(f"    rate limited — waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            wait = 2 ** attempt * 3
            print(f"    timeout — waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Congress API failed after retries: {url}")


def fetch_sponsored_bills(bioguide_id: str, congress: int, api_key: str) -> list[dict]:
    url = f"{BASE}/member/{bioguide_id}/sponsored-legislation"
    all_bills: list[dict] = []
    offset = 0
    while True:
        data = _get(url, {"limit": 250, "offset": offset}, api_key)
        bills = data.get("sponsoredLegislation", [])
        if not bills:
            break
        all_bills.extend(bills)
        total = data.get("pagination", {}).get("count", 0)
        offset += 250
        if offset >= total:
            break
        time.sleep(0.3)
    return [b for b in all_bills if b.get("congress") == congress]


def fetch_member_votes(bioguide_id: str, congress: int, api_key: str) -> list[dict]:
    """Read votes from the existing congress_votes table instead of Congress.gov API."""
    conn = get_db()
    rows = conn.execute("""
        SELECT bill, member_vote, vote_date, chamber, vote_number, congress
        FROM congress_votes
        WHERE bioguide_id = ? AND congress = ?
        ORDER BY vote_date DESC
    """, (bioguide_id, congress)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_bill_subjects(congress: int, bill_type: str, bill_number: str, api_key: str) -> list[str]:
    url = f"{BASE}/bill/{congress}/{bill_type.lower()}/{bill_number}/subjects"
    try:
        data = _get(url, {}, api_key)
        subjects = data.get("subjects", {}).get("legislativeSubjects", [])
        return [s["name"] for s in subjects]
    except Exception:
        return []


# ── DB writers ───────────────────────────────────────────────────────────────

def insert_bills(conn: sqlite3.Connection, bioguide_id: str, bills: list[dict],
                 congress: int, api_key: str) -> int:
    written = 0
    for bill in bills:
        bill_type = (bill.get("type") or "").upper()
        bill_number = str(bill.get("number") or "")
        policy_area = (bill.get("policyArea") or {}).get("name") or "Other"

        subjects = fetch_bill_subjects(congress, bill_type, bill_number, api_key)
        time.sleep(0.2)

        conn.execute("""
            INSERT INTO federal_bills
                (bioguide_id, congress, bill_number, bill_type, title,
                 introduced_date, policy_area, subjects, status, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(bioguide_id, congress, bill_number) DO UPDATE SET
                title           = excluded.title,
                policy_area     = excluded.policy_area,
                subjects        = excluded.subjects,
                status          = excluded.status,
                fetched_at      = excluded.fetched_at
        """, (
            bioguide_id,
            congress,
            bill_number,
            bill_type,
            bill.get("title") or "",
            bill.get("introducedDate") or "",
            policy_area,
            json.dumps(subjects),
            (bill.get("latestAction") or {}).get("text") or "",
            now_iso(),
        ))
        written += 1

    conn.commit()
    return written


def insert_votes(conn: sqlite3.Connection, bioguide_id: str, votes: list[dict]) -> int:
    written = 0
    for vote in votes:
        roll_call = str(vote.get("vote_number") or "")
        conn.execute("""
            INSERT INTO federal_votes
                (bioguide_id, bill_number, congress, chamber, vote, vote_date, roll_call, fetched_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(bioguide_id, congress, roll_call) DO UPDATE SET
                vote       = excluded.vote,
                vote_date  = excluded.vote_date,
                fetched_at = excluded.fetched_at
        """, (
            bioguide_id,
            vote.get("bill") or "",
            vote.get("congress"),
            vote.get("chamber") or "",
            vote.get("member_vote") or "",
            vote.get("vote_date") or "",
            roll_call,
            now_iso(),
        ))
        written += 1

    conn.commit()
    return written


# ── Three-way intelligence view ───────────────────────────────────────────────

def create_intelligence_view(conn: sqlite3.Connection) -> None:
    """
    Joins donors (candidate_sector_totals via bridge) × bills (federal_bills)
    × votes (federal_votes) into one queryable view.
    SQLite doesn't support FILTER on aggregates, so CASE WHEN is used instead.
    """
    conn.execute("DROP VIEW IF EXISTS federal_intelligence")
    conn.execute("""
        CREATE VIEW federal_intelligence AS
        SELECT
            m.name,
            m.party,
            m.state,
            m.chamber,
            dst.sector,
            ROUND(dst.total_amount, 2)          AS total_amount,
            dst.donor_count,
            dst.cycle,
            COUNT(DISTINCT fb.bill_number)       AS bills_introduced,
            COUNT(DISTINCT CASE
                WHEN LOWER(fb.policy_area) = LOWER(dst.sector) THEN fb.bill_number
            END)                                 AS sector_aligned_bills,
            COUNT(DISTINCT cv.vote_number)       AS total_votes,
            COUNT(DISTINCT CASE
                WHEN cv.member_vote = 'Yea' THEN cv.vote_number
            END)                                 AS yes_votes,
            COUNT(DISTINCT CASE
                WHEN cv.member_vote = 'Nay' THEN cv.vote_number
            END)                                 AS no_votes,
            COUNT(DISTINCT CASE
                WHEN cv.member_vote = 'Yea' AND cv.result LIKE '%Passed%' THEN cv.vote_number
            END)                                 AS yea_passed,
            COUNT(DISTINCT CASE
                WHEN cv.member_vote = 'Nay' AND cv.result LIKE '%Passed%' THEN cv.vote_number
            END)                                 AS nay_on_passed
        FROM congress_members m
        JOIN bioguide_fec_bridge bridge
            ON bridge.bioguide_id = m.bioguide_id
        JOIN candidate_sector_totals dst
            ON dst.candidate_id = bridge.fec_candidate_id
        LEFT JOIN federal_bills fb
            ON fb.bioguide_id = m.bioguide_id
        LEFT JOIN congress_votes cv
            ON cv.bioguide_id = m.bioguide_id
        GROUP BY
            m.name, m.party, m.state, m.chamber,
            dst.sector, dst.total_amount, dst.donor_count, dst.cycle
    """)
    conn.commit()
    print("  federal_intelligence view created")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--congress", type=int, default=119)
    parser.add_argument("--bioguide", help="Single member bioguide ID")
    parser.add_argument("--view-only", action="store_true", help="Just rebuild the view, skip fetching")
    parser.add_argument("--no-subjects", action="store_true", help="Skip per-bill subject lookups (faster)")
    args = parser.parse_args()

    api_key = os.getenv("CONGRESS_API_KEY", "")
    if not api_key and not args.view_only:
        raise SystemExit("CONGRESS_API_KEY not set in .env")

    conn = get_db()
    create_tables(conn)
    populate_bridge(conn)

    if not args.view_only:
        members = conn.execute(
            "SELECT bioguide_id, name FROM congress_members"
            + (" WHERE bioguide_id = ?" if args.bioguide else ""),
            (args.bioguide,) if args.bioguide else (),
        ).fetchall()

        for member in members:
            bioguide_id = member["bioguide_id"]
            name = member["name"]
            print(f"\n{'='*55}")
            print(f"  {name} ({bioguide_id}) — Congress {args.congress}")
            print(f"{'='*55}")

            print("  Fetching sponsored bills...")
            bills = fetch_sponsored_bills(bioguide_id, args.congress, api_key)
            print(f"  Found {len(bills)} bills")
            if bills:
                n = insert_bills(conn, bioguide_id, bills, args.congress,
                                 api_key if not args.no_subjects else "")
                print(f"  Stored {n} bills")

            print("  Fetching votes...")
            votes = fetch_member_votes(bioguide_id, args.congress, api_key)
            print(f"  Found {len(votes)} votes")
            if votes:
                n = insert_votes(conn, bioguide_id, votes)
                print(f"  Stored {n} votes")

            time.sleep(0.5)

    print("\nBuilding federal_intelligence view...")
    create_intelligence_view(conn)

    # Quick sanity check
    rows = conn.execute("""
        SELECT name, party, sector, total_amount, bills_introduced,
               sector_aligned_bills, total_votes, yes_votes
        FROM federal_intelligence
        ORDER BY total_amount DESC
        LIMIT 10
    """).fetchall()

    if rows:
        print(f"\n{'Name':<25} {'Party':<4} {'Sector':<18} {'Donors $':>10} {'Bills':>5} {'Aligned':>7} {'Votes':>6} {'Yea':>5}")
        print("-" * 90)
        for r in rows:
            print(f"{r['name']:<25} {r['party']:<4} {r['sector']:<18} "
                  f"{r['total_amount']:>10,.0f} {r['bills_introduced']:>5} "
                  f"{r['sector_aligned_bills']:>7} {r['total_votes']:>6} {r['yes_votes']:>5}")
    else:
        print("  View is empty — run without --view-only to ingest data first.")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
