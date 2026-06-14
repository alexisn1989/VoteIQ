#!/usr/bin/env python3
"""
Build Virginia legislative SQLite database from OpenStates API (2022-2026).

Tables:
  bills      - bill metadata per session
  votes      - individual named votes per bill (who voted yes/no)
  legislators - unique legislators with party/district info

Usage:
    python build_openstates_db.py              # all sessions 2022-2026
    python build_openstates_db.py --session 2026
    python build_openstates_db.py --dry-run    # fetch only, no DB write
"""
import argparse
import os
import re
import sqlite3
import time
from dotenv import load_dotenv
import build_bill_descriptions

load_dotenv()

import requests

API_KEY  = os.getenv("OPENSTATES_API_KEY")
BASE     = "https://v3.openstates.org"
DB_PATH  = os.path.join(os.path.dirname(__file__), "openstates_va.db")
SESSIONS = ["2022", "2023", "2024", "2025", "2026"]


def get(path, params=None, _retries=12):
    for attempt in range(_retries):
        r = requests.get(
            BASE + path,
            headers={"X-API-KEY": API_KEY, "User-Agent": "VoteIQ/1.0"},
            params=params or {},
            timeout=60,
        )
        if r.status_code == 429:
            header_wait = int(r.headers.get("Retry-After", 60))
            # Exponential backoff on top of Retry-After: 1×, 2×, 4×, … capped at 10 min
            wait = min(header_wait * (2 ** attempt), 600)
            print(f"  [rate limit attempt {attempt+1}/{_retries}] waiting {wait}s …")
            time.sleep(wait)
            continue
        if r.status_code in (502, 503, 504) and attempt < _retries - 1:
            wait = min(10 * (2 ** attempt), 300)
            print(f"  [server error {r.status_code} attempt {attempt+1}/{_retries}] waiting {wait}s …")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Failed after {_retries} retries on {path}")


def fetch_bills_page(session: str, page: int) -> tuple[list[dict], int]:
    """Fetch a single page of bills. Returns (items, max_page)."""
    data = get("/bills", {
        "jurisdiction": "Virginia",
        "session": session,
        "per_page": 20,
        "page": page,
        "include": ["sponsorships", "abstracts", "votes", "actions", "documents", "versions", "amendments"],
    })
    items = data.get("results", [])
    max_page = data.get("pagination", {}).get("max_page") or 1
    return items, max_page


def setup_db(conn):
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS bills (
            bill_id        TEXT,
            session        TEXT,
            title          TEXT,
            classification TEXT,
            subjects       TEXT,
            sponsors       TEXT,
            latest_action  TEXT,
            latest_date    TEXT,
            result         TEXT,
            openstates_url TEXT,
            PRIMARY KEY (bill_id, session)
        );

        CREATE TABLE IF NOT EXISTS votes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id        TEXT,
            session        TEXT,
            vote_date      TEXT,
            chamber        TEXT,
            motion         TEXT,
            result         TEXT,
            voter_name     TEXT,
            option         TEXT,
            party          TEXT,
            district       TEXT
        );

        CREATE TABLE IF NOT EXISTS legislators (
            ocd_id         TEXT PRIMARY KEY,
            name           TEXT,
            party          TEXT,
            chamber        TEXT,
            district       TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_votes_bill    ON votes(bill_id, session);
        CREATE INDEX IF NOT EXISTS idx_votes_voter   ON votes(voter_name);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_votes_nodup ON votes(voter_name, bill_id, session, motion);
        CREATE INDEX IF NOT EXISTS idx_bills_session ON bills(session);

        CREATE TABLE IF NOT EXISTS bill_actions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id     TEXT,
            session     TEXT,
            date        TEXT,
            description TEXT,
            chamber     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_actions_bill ON bill_actions(bill_id, session);

        CREATE TABLE IF NOT EXISTS bill_documents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id     TEXT,
            session     TEXT,
            name        TEXT,
            url         TEXT,
            media_type  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_docs_bill ON bill_documents(bill_id, session);

        CREATE TABLE IF NOT EXISTS bill_versions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id     TEXT,
            session     TEXT,
            name        TEXT,
            date        TEXT,
            url         TEXT,
            media_type  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ver_bill ON bill_versions(bill_id, session);

        CREATE TABLE IF NOT EXISTS bill_amendments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id         TEXT,
            session         TEXT,
            identifier      TEXT,
            classification  TEXT,
            description     TEXT,
            date            TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_amend_bill ON bill_amendments(bill_id, session);

        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT UNIQUE,
            name        TEXT,
            description TEXT,
            start_date  TEXT,
            end_date    TEXT,
            status      TEXT,
            location    TEXT,
            chamber     TEXT
        );

        CREATE TABLE IF NOT EXISTS event_bills (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT,
            bill_id     TEXT,
            note        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_evbills_event ON event_bills(event_id);

        CREATE TABLE IF NOT EXISTS va_committees (
            ocd_id          TEXT PRIMARY KEY,
            name            TEXT,
            chamber         TEXT,
            classification  TEXT,
            parent_id       TEXT
        );

        CREATE TABLE IF NOT EXISTS va_committee_members (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            committee_ocd   TEXT,
            person_name     TEXT,
            person_ocd      TEXT,
            role            TEXT,
            UNIQUE(committee_ocd, person_ocd)
        );
    """)
    conn.commit()


def insert_bill(cur, bill: dict, session: str):
    identifier = re.sub(r"\s+", "", (bill.get("identifier") or "").upper())
    if not identifier:
        return
    sponsors = ", ".join(
        (s.get("name") or s.get("person", {}).get("name") or "")
        for s in (bill.get("sponsorships") or [])
    )
    abstracts = bill.get("abstracts") or []
    summary = next(
        (a.get("abstract") or a.get("note") or "" for a in abstracts if isinstance(a, dict)), ""
    )
    actions = bill.get("actions") or []
    last_action = actions[-1] if actions else {}
    classification = ", ".join(bill.get("classification") or [])
    subjects = ", ".join(bill.get("subject") or bill.get("subjects") or [])

    # Overall bill result from votes
    vote_results = [v.get("result", "") for v in (bill.get("votes") or [])]
    result = "pass" if "pass" in vote_results else ("fail" if "fail" in vote_results else "")

    cur.execute("""
        INSERT OR REPLACE INTO bills
        (bill_id, session, title, classification, subjects, sponsors,
         latest_action, latest_date, result, openstates_url)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        identifier, session,
        (bill.get("title") or summary or "")[:500],
        classification, subjects, sponsors,
        (bill.get("latest_action_description") or last_action.get("description") or "")[:300],
        (bill.get("latest_action_date") or last_action.get("date") or ""),
        result,
        bill.get("openstates_url") or "",
    ))
    return identifier


def insert_votes(cur, bill_id: str, session: str, bill: dict):
    for vote_record in (bill.get("votes") or []):
        org = vote_record.get("organization") or {}
        chamber = (org.get("name") or "") if isinstance(org, dict) else str(org)
        motion = vote_record.get("motion_text") or ""
        result = vote_record.get("result") or ""
        date   = vote_record.get("start_date") or ""

        for v in (vote_record.get("votes") or []):
            voter = v.get("voter") or {}
            role  = voter.get("current_role") or {}
            ocd_id = voter.get("id") or ""
            name   = v.get("voter_name") or voter.get("name") or ""
            party  = voter.get("party") or ""
            district = role.get("district") or ""
            title    = role.get("title") or ""

            cur.execute("""
                INSERT OR IGNORE INTO votes
                (bill_id, session, vote_date, chamber, motion, result,
                 voter_name, option, party, district)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (bill_id, session, date, chamber, motion, result,
                  name, v.get("option") or "", party, district))

            if ocd_id:
                cur.execute("""
                    INSERT OR REPLACE INTO legislators (ocd_id, name, party, chamber, district)
                    VALUES (?,?,?,?,?)
                """, (ocd_id, voter.get("name") or name, party, title, district))


def insert_actions(cur, bill_id: str, session: str, bill: dict):
    for action in (bill.get("actions") or []):
        org = action.get("organization") or {}
        chamber = org.get("name", "") if isinstance(org, dict) else str(org)
        cur.execute(
            "INSERT INTO bill_actions (bill_id, session, date, description, chamber) VALUES (?,?,?,?,?)",
            (bill_id, session,
             action.get("date") or "",
             (action.get("description") or "")[:500],
             chamber),
        )


def insert_documents(cur, bill_id: str, session: str, bill: dict):
    for doc in (bill.get("documents") or []):
        links = doc.get("links") or []
        url = links[0].get("url", "") if links else ""
        media_type = links[0].get("media_type", "") if links else ""
        cur.execute(
            "INSERT INTO bill_documents (bill_id, session, name, url, media_type) VALUES (?,?,?,?,?)",
            (bill_id, session,
             (doc.get("name") or "")[:300],
             url, media_type),
        )


def insert_versions(cur, bill_id: str, session: str, bill: dict):
    for ver in (bill.get("versions") or []):
        links = ver.get("links") or []
        url = links[0].get("url", "") if links else ""
        media_type = links[0].get("media_type", "") if links else ""
        cur.execute(
            "INSERT INTO bill_versions (bill_id, session, name, date, url, media_type) VALUES (?,?,?,?,?,?)",
            (bill_id, session,
             (ver.get("name") or "")[:300],
             ver.get("date") or "",
             url, media_type),
        )


def insert_amendments(cur, bill_id: str, session: str, bill: dict):
    for amend in (bill.get("amendments") or []):
        cur.execute(
            "INSERT INTO bill_amendments (bill_id, session, identifier, classification, description, date) VALUES (?,?,?,?,?,?)",
            (bill_id, session,
             (amend.get("identifier") or "")[:100],
             ", ".join(amend.get("classification") or []),
             (amend.get("description") or "")[:500],
             amend.get("date") or ""),
        )


def fetch_and_store_events(conn):
    cur = conn.cursor()
    page, total = 1, 0
    print("\n=== Fetching Events ===")
    while True:
        data = get("/events", {
            "jurisdiction": "ocd-jurisdiction/country:us/state:va/government",
            "per_page": 20,
            "page": page,
        })
        items = data.get("results", [])
        if not items:
            break
        for ev in items:
            event_id = ev.get("id") or ev.get("ocd_id") or ""
            org = ev.get("location") or {}
            location = org.get("name", "") if isinstance(org, dict) else str(org or "")
            orgs = ev.get("organizations") or []
            chamber = orgs[0].get("name", "") if orgs else ""
            cur.execute("""
                INSERT OR REPLACE INTO events
                (event_id, name, description, start_date, end_date, status, location, chamber)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                event_id,
                (ev.get("name") or "")[:300],
                (ev.get("description") or "")[:1000],
                ev.get("start_date") or "",
                ev.get("end_date") or "",
                ev.get("status") or "",
                location[:300],
                chamber[:200],
            ))
            # agenda items that reference bills
            for agenda_item in (ev.get("agenda") or []):
                for related in (agenda_item.get("related_entities") or []):
                    if related.get("entity_type") == "bill":
                        bill_id = re.sub(r"\s+", "", (related.get("name") or "").upper())
                        if bill_id:
                            cur.execute(
                                "INSERT INTO event_bills (event_id, bill_id, note) VALUES (?,?,?)",
                                (event_id, bill_id, (agenda_item.get("description") or "")[:300]),
                            )
            total += 1
        conn.commit()
        print(f"  [page {page}] {len(items)} events — {total} total")
        max_page = data.get("pagination", {}).get("max_page") or 1
        if page >= max_page:
            break
        page += 1
        time.sleep(1)
    print(f"  Events done: {total} stored")


def fetch_and_store_committees(conn):
    cur = conn.cursor()
    page, total = 1, 0
    print("\n=== Fetching VA State Committees ===")
    while True:
        data = get("/committees", {
            "jurisdiction": "ocd-jurisdiction/country:us/state:va/government",
            "per_page": 20,
            "page": page,
        })
        items = data.get("results", [])
        if not items:
            break
        for c in items:
            ocd_id = c.get("id") or c.get("ocd_id") or ""
            parent = c.get("parent") or {}
            parent_id = parent.get("id", "") if isinstance(parent, dict) else ""
            org = c.get("organization") or {}
            chamber = org.get("classification", "") if isinstance(org, dict) else ""
            cur.execute("""
                INSERT OR REPLACE INTO va_committees (ocd_id, name, chamber, classification, parent_id)
                VALUES (?,?,?,?,?)
            """, (
                ocd_id,
                (c.get("name") or "")[:300],
                chamber[:100],
                (c.get("classification") or "")[:100],
                parent_id,
            ))
            for m in (c.get("members") or []):
                person = m.get("person") or {}
                person_ocd = person.get("id") or person.get("ocd_id") or ""
                person_name = person.get("name") or m.get("name") or ""
                cur.execute("""
                    INSERT OR REPLACE INTO va_committee_members
                    (committee_ocd, person_name, person_ocd, role)
                    VALUES (?,?,?,?)
                """, (ocd_id, person_name, person_ocd, m.get("role") or ""))
            total += 1
        conn.commit()
        print(f"  [page {page}] {len(items)} committees — {total} total")
        max_page = data.get("pagination", {}).get("max_page") or 1
        if page >= max_page:
            break
        page += 1
        time.sleep(0.5)
    print(f"  Committees done: {total} stored")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", help="Single session e.g. 2026 (default: all 2022-2026)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip bills already in DB, continue from last page")
    parser.add_argument("--start-page", type=int, default=1, help="Manually start from this page number")
    args = parser.parse_args()

    sessions = [args.session] if args.session else SESSIONS

    if not args.dry_run:
        conn = sqlite3.connect(DB_PATH)
        setup_db(conn)
        cur = conn.cursor()

    for session in sessions:
        print(f"\n=== Session {session} ===")

        # Determine start page
        start_page = args.start_page
        if args.resume and not args.dry_run:
            existing = cur.execute("SELECT COUNT(*) FROM bills WHERE session=?", (session,)).fetchone()[0]
            start_page = (existing // 20) + 1
            if existing:
                print(f"  Resuming from page {start_page} ({existing} bills already saved)")

        page, max_page = start_page, 999
        bill_count = vote_count = 0

        while page <= max_page:
            items, max_page = fetch_bills_page(session, page)
            print(f"  [page {page}/{max_page}] {len(items)} bills")

            if args.dry_run:
                if items:
                    sample = items[0]
                    vid = re.sub(r"\s+", "", (sample.get("identifier") or "").upper())
                    nvotes = sum(len(v.get("votes") or []) for v in (sample.get("votes") or []))
                    print(f"  Sample: {vid} — {len(sample.get('votes') or [])} vote records, {nvotes} individual votes")
            else:
                for bill in items:
                    bill_id = insert_bill(cur, bill, session)
                    if bill_id:
                        insert_votes(cur, bill_id, session, bill)
                        insert_actions(cur, bill_id, session, bill)
                        insert_documents(cur, bill_id, session, bill)
                        insert_versions(cur, bill_id, session, bill)
                        insert_amendments(cur, bill_id, session, bill)
                        bill_count += 1
                        vote_count += sum(len(v.get("votes") or []) for v in (bill.get("votes") or []))
                conn.commit()  # save after every page
                print(f"    saved so far: {bill_count} bills, {vote_count} votes")

            if page >= max_page:
                break
            page += 1
            time.sleep(3)

        if not args.dry_run:
            print(f"  Session {session} done: {bill_count} bills, {vote_count} individual votes")

    if not args.dry_run:
        fetch_and_store_events(conn)
        fetch_and_store_committees(conn)

        total_bills  = cur.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
        total_votes  = cur.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
        total_legs   = cur.execute("SELECT COUNT(*) FROM legislators").fetchone()[0]
        total_events = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        total_cmts   = cur.execute("SELECT COUNT(*) FROM va_committees").fetchone()[0]
        print(f"\n=== Database totals ===")
        print(f"  Bills:       {total_bills}")
        print(f"  Votes:       {total_votes}")
        print(f"  Legislators: {total_legs}")
        print(f"  Events:      {total_events}")
        print(f"  Committees:  {total_cmts}")
        print(f"  Saved to:    {DB_PATH}")
        conn.close()

    if not args.dry_run:
        print("\n=== Building bill descriptions cache ===")
        try:
            for s in sessions:
                build_bill_descriptions.build_cache(session=s)
            print("=== Cache build complete ===")
        except Exception as e:
            print(f"[warn] Cache build failed (pull data is safe): {e}")


if __name__ == "__main__":
    main()
