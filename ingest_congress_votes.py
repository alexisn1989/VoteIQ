#!/usr/bin/env python3
"""
Ingest roll-call votes for Virginia's federal delegation into polls.db.

Sources:
  House: clerk.house.gov/evs/{year}/roll{NNN}.xml  (bioguide ID in name-id attr)
  Senate: senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{c}_{s}_{NNN}.xml

Usage:
    python ingest_congress_votes.py                 # latest 150 House + 200 Senate
    python ingest_congress_votes.py --house-limit 300
    python ingest_congress_votes.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "polls.db"

HOUSE_BASE   = "https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"
SENATE_MENU  = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"
SENATE_VOTE  = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{vote:05d}.xml"

HEADERS = {"User-Agent": "VoteIQ/1.0 (Virginia civic data; alexisnieuwenhuys89@gmail.com)"}
CONGRESS = 119
SESSION  = 1
YEAR     = 2025

# VA member bioguide IDs (House)
VA_HOUSE_BIOGUIDES = {
    "W000804", "K000399", "S000185", "M001227", "M001239",
    "C001118", "V000138", "B001292", "G000568", "S001230", "W000831",
}
# VA senators matched by last name
VA_SENATORS = {"Warner", "Kaine"}


def _get(url: str, timeout: int = 20) -> bytes | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        return urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:
        return None


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_votes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id  TEXT NOT NULL,
            chamber      TEXT NOT NULL,
            congress     INTEGER NOT NULL,
            vote_number  INTEGER NOT NULL,
            vote_date    TEXT,
            bill         TEXT,
            question     TEXT,
            member_vote  TEXT,
            result       TEXT,
            fetched_at   TEXT NOT NULL,
            UNIQUE(bioguide_id, chamber, congress, vote_number)
        );
        CREATE INDEX IF NOT EXISTS idx_cv_bioguide ON congress_votes(bioguide_id);
        CREATE INDEX IF NOT EXISTS idx_cv_date     ON congress_votes(vote_date);
    """)
    conn.commit()


def already_stored(conn: sqlite3.Connection, chamber: str, vote_number: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM congress_votes WHERE chamber=? AND congress=? AND vote_number=? LIMIT 1",
        (chamber, CONGRESS, vote_number),
    ).fetchone()
    return row is not None


def _parse_house_roll(data: bytes, roll_number: int) -> list[dict]:
    """Parse a House Clerk roll-call XML and return VA member votes."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    question = root.findtext(".//vote-question") or ""
    result   = root.findtext(".//vote-result") or ""
    date_raw = root.findtext(".//action-date") or ""
    bill     = root.findtext(".//legis-num") or ""

    records = []
    for rv in root.findall(".//recorded-vote"):
        leg = rv.find("legislator")
        if leg is None:
            continue
        bgid = leg.get("name-id", "")
        if bgid not in VA_HOUSE_BIOGUIDES:
            continue
        records.append({
            "bioguide_id": bgid,
            "chamber":     "house",
            "congress":    CONGRESS,
            "vote_number": roll_number,
            "vote_date":   date_raw,
            "bill":        bill,
            "question":    question,
            "member_vote": rv.findtext("vote") or "",
            "result":      result,
        })
    return records


def ingest_house(conn: sqlite3.Connection, limit: int = 150, dry_run: bool = False) -> int:
    """Fetch the most recent `limit` House roll-call votes and store VA member votes."""
    # Detect latest roll by binary search from high end
    latest = 1
    for candidate in [500, 400, 350, 320, 300, 250, 200, 150, 100]:
        data = _get(HOUSE_BASE.format(year=YEAR, roll=candidate))
        if data:
            latest = candidate
            break

    # Walk backwards from latest, stop after `limit` rolls processed
    print(f"House: latest roll found = {latest}, fetching up to {limit} rolls")
    inserted = 0
    for roll in range(latest, max(0, latest - limit), -1):
        if already_stored(conn, "house", roll) and not dry_run:
            continue
        data = _get(HOUSE_BASE.format(year=YEAR, roll=roll))
        if not data:
            continue
        records = _parse_house_roll(data, roll)
        if not records:
            time.sleep(0.2)
            continue
        print(f"  roll {roll:03d}: {records[0]['question'][:60]} | {len(records)} VA votes")
        if dry_run:
            inserted += len(records)
            time.sleep(0.2)
            continue
        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            conn.execute(
                """INSERT INTO congress_votes
                       (bioguide_id, chamber, congress, vote_number, vote_date,
                        bill, question, member_vote, result, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(bioguide_id, chamber, congress, vote_number) DO UPDATE SET
                       member_vote=excluded.member_vote, fetched_at=excluded.fetched_at""",
                (r["bioguide_id"], r["chamber"], r["congress"], r["vote_number"],
                 r["vote_date"], r["bill"], r["question"], r["member_vote"],
                 r["result"], now),
            )
        conn.commit()
        inserted += len(records)
        time.sleep(0.3)
    return inserted


def _parse_senate_vote(data: bytes, vote_number: int) -> list[dict]:
    """Parse a Senate vote XML and return VA senator votes."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    question = root.findtext(".//question") or root.findtext(".//vote_question_text") or ""
    result   = root.findtext(".//vote_result") or root.findtext(".//vote_result_text") or ""
    date_raw = root.findtext(".//vote_date") or ""
    doc      = root.find(".//document")
    bill     = ""
    if doc is not None:
        dtype  = doc.findtext("document_type") or ""
        dnum   = doc.findtext("document_number") or ""
        bill   = f"{dtype} {dnum}".strip()

    records = []
    for member in root.findall(".//member"):
        state = member.findtext("state") or ""
        if state != "VA":
            continue
        last = member.findtext("last_name") or ""
        party = member.findtext("party") or ""
        vote  = member.findtext("vote_cast") or ""

        # Map last name to bioguide_id
        bgid = "W000805" if last == "Warner" else "K000384" if last == "Kaine" else None
        if not bgid:
            continue
        records.append({
            "bioguide_id": bgid,
            "chamber":     "senate",
            "congress":    CONGRESS,
            "vote_number": vote_number,
            "vote_date":   date_raw,
            "bill":        bill,
            "question":    question,
            "member_vote": vote,
            "result":      result,
        })
    return records


def ingest_senate(conn: sqlite3.Connection, limit: int = 200, dry_run: bool = False) -> int:
    """Fetch Senate votes from the menu and store VA senator votes."""
    menu_data = _get(SENATE_MENU.format(congress=CONGRESS, session=SESSION))
    if not menu_data:
        print("Senate: could not fetch vote menu")
        return 0

    root = ET.fromstring(menu_data)
    all_votes = root.findall(".//vote")
    # Most recent first
    all_votes = list(reversed(all_votes))[:limit]
    print(f"Senate: {len(all_votes)} recent votes to process")

    inserted = 0
    for vote_el in all_votes:
        num_str = vote_el.findtext("vote_number") or "0"
        try:
            vote_num = int(num_str.lstrip("0") or "0")
        except ValueError:
            continue

        if already_stored(conn, "senate", vote_num) and not dry_run:
            continue

        url  = SENATE_VOTE.format(congress=CONGRESS, session=SESSION, vote=vote_num)
        data = _get(url)
        if not data:
            time.sleep(0.5)
            continue

        records = _parse_senate_vote(data, vote_num)
        if records:
            print(f"  senate vote {vote_num:03d}: {records[0]['question'][:60]} | {len(records)} VA votes")
        if dry_run:
            inserted += len(records)
            time.sleep(0.3)
            continue

        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            conn.execute(
                """INSERT INTO congress_votes
                       (bioguide_id, chamber, congress, vote_number, vote_date,
                        bill, question, member_vote, result, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(bioguide_id, chamber, congress, vote_number) DO UPDATE SET
                       member_vote=excluded.member_vote, fetched_at=excluded.fetched_at""",
                (r["bioguide_id"], r["chamber"], r["congress"], r["vote_number"],
                 r["vote_date"], r["bill"], r["question"], r["member_vote"],
                 r["result"], now),
            )
        conn.commit()
        inserted += len(records)
        time.sleep(0.3)
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest VA federal delegation roll-call votes")
    parser.add_argument("--house-limit",  type=int, default=150)
    parser.add_argument("--senate-limit", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)

    h = ingest_house(conn,  limit=args.house_limit,  dry_run=args.dry_run)
    s = ingest_senate(conn, limit=args.senate_limit, dry_run=args.dry_run)
    conn.close()

    verb = "would insert" if args.dry_run else "inserted/updated"
    print(f"\nDone. {verb} {h} House vote records, {s} Senate vote records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
