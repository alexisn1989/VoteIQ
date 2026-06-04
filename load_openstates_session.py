"""
load_openstates_session.py

Downloads a Virginia legislative session from OpenStates API v3 and
loads it directly into openstates_va.db (bills + individual vote records).

Usage:
    python load_openstates_session.py --session 2021
    python load_openstates_session.py --session 2021 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "openstates_va.db"

API_KEY  = os.getenv("OPENSTATES_API_KEY")
BASE_URL = "https://v3.openstates.org"
PER_PAGE = 20
INCLUDE  = ["sponsorships", "actions", "votes"]  # include individual vote records
SLEEP    = 1.5                            # seconds between pages


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").upper())


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _get(path: str, params: dict, max_retries: int = 8) -> dict:
    headers = {
        "X-API-KEY": API_KEY,
        "User-Agent": "VoteIQ/1.0 (+https://voteiq.io)",
    }
    url = f"{BASE_URL}{path}"
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=90)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            wait = min(30 * 2**attempt, 180)
            print(f"  [net-error] {exc} — retry in {wait}s")
            time.sleep(wait)
            continue
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 75)) + 5
            print(f"  [429] rate-limited — wait {wait}s")
            time.sleep(wait)
            continue
        if r.status_code in (500, 502, 503, 504):
            wait = min(15 * 2**attempt, 120)
            print(f"  [{r.status_code}] server error — retry in {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"OpenStates request failed after {max_retries} retries: {url}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bills (
            bill_id        TEXT,
            session        TEXT NOT NULL,
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
        CREATE INDEX IF NOT EXISTS idx_bills_session ON bills(session);

        CREATE TABLE IF NOT EXISTS legislators (
            ocd_id   TEXT PRIMARY KEY,
            name     TEXT,
            party    TEXT,
            chamber  TEXT,
            district TEXT
        );

        CREATE TABLE IF NOT EXISTS votes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id    TEXT NOT NULL,
            session    TEXT NOT NULL,
            vote_date  TEXT,
            chamber    TEXT,
            motion     TEXT,
            result     TEXT,
            voter_name TEXT,
            option     TEXT,
            party      TEXT,
            district   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_votes_bill    ON votes(bill_id, session);
        CREATE INDEX IF NOT EXISTS idx_votes_voter   ON votes(voter_name);
        CREATE INDEX IF NOT EXISTS idx_votes_session ON votes(session);
    """)
    conn.commit()


def _load_session(session: str, dry_run: bool = False) -> None:
    if not API_KEY:
        raise RuntimeError("OPENSTATES_API_KEY not set in .env")

    conn = sqlite3.connect(DB_PATH) if not dry_run else None
    if conn:
        _ensure_schema(conn)

    print(f"Downloading session {session} from OpenStates API (with votes)…")

    page         = 1
    bills_saved  = 0
    votes_saved  = 0
    max_page     = 999

    while page <= max_page:
        print(f"  [page {page}] fetching…", end=" ", flush=True)
        payload = _get("/bills", {
            "jurisdiction": "Virginia",
            "session":       session,
            "per_page":      PER_PAGE,
            "page":          page,
            "include":       INCLUDE,
        })

        items = payload.get("results", [])
        pagination = payload.get("pagination", {})
        max_page = int(pagination.get("max_page", 1))

        if not items:
            print("no items — done")
            break

        for bill in items:
            bid    = _norm(bill.get("identifier") or bill.get("id") or "")
            if not bid:
                continue

            # Sponsors
            sponsors_list = [
                s.get("name") or (s.get("entity", {}) or {}).get("name", "")
                for s in (bill.get("sponsorships") or [])
            ]
            sponsors_str = ", ".join(s for s in sponsors_list if s)

            # Latest action
            actions = sorted(
                bill.get("actions") or [],
                key=lambda a: a.get("date", ""),
                reverse=True,
            )
            latest_action = _clean(actions[0].get("description", "")) if actions else ""
            latest_date   = actions[0].get("date", "") if actions else ""

            # Result from last action
            result = ""
            for a in actions:
                cats = a.get("classification") or []
                if "passage" in cats:
                    result = "passed"
                    break
                if "failure" in cats:
                    result = "failed"
                    break

            if not dry_run and conn:
                conn.execute("""
                    INSERT OR REPLACE INTO bills
                        (bill_id, session, title, classification, subjects,
                         sponsors, latest_action, latest_date, result, openstates_url)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    bid, session,
                    _clean(bill.get("title") or bill.get("short_title") or ""),
                    _clean(bill.get("classification") or ""),
                    json.dumps(bill.get("subject") or []),
                    sponsors_str,
                    latest_action,
                    latest_date,
                    result,
                    bill.get("openstates_url") or bill.get("web_url") or "",
                ))
                bills_saved += 1

            # Vote records
            for vote_event in (bill.get("votes") or []):
                vote_date  = vote_event.get("date", "")
                raw_chamber = (vote_event.get("chamber") or "").lower()
                chamber = "House" if "lower" in raw_chamber or "house" in raw_chamber else "Senate"
                motion  = _clean(vote_event.get("motion_text") or vote_event.get("description") or "")
                v_result = vote_event.get("result") or ""

                for voter in (vote_event.get("votes") or []):
                    voter_name = voter.get("voter_name") or (
                        (voter.get("voter") or {}).get("name") or ""
                    )
                    option = (voter.get("option") or voter.get("vote") or "").lower()
                    party  = ""
                    dist   = ""
                    person = voter.get("voter") or {}
                    if isinstance(person, dict):
                        party = person.get("party") or (
                            (person.get("current_role") or {}).get("party") or ""
                        )
                        dist  = person.get("current_district") or (
                            (person.get("current_role") or {}).get("district") or ""
                        )

                    if not voter_name or not option:
                        continue

                    if not dry_run and conn:
                        conn.execute("""
                            INSERT INTO votes
                                (bill_id, session, vote_date, chamber, motion,
                                 result, voter_name, option, party, district)
                            VALUES (?,?,?,?,?,?,?,?,?,?)
                        """, (bid, session, vote_date, chamber, motion,
                              v_result, voter_name, option, party, dist))
                        votes_saved += 1

        print(f"{len(items)} bills | page {page}/{max_page} | "
              f"{bills_saved} bills, {votes_saved:,} votes saved")

        if not dry_run and conn and page % 10 == 0:
            conn.commit()

        if page >= max_page or len(items) == 0:
            break

        page += 1
        time.sleep(SLEEP)

    if not dry_run and conn:
        conn.commit()
        conn.close()

    print(f"\nDone: {bills_saved} bills, {votes_saved:,} vote records loaded into {DB_PATH.name}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True, help="Session year, e.g. 2021")
    p.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    args = p.parse_args()
    _load_session(args.session, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
