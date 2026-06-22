#!/usr/bin/env python3
"""
ingest_vb_votes.py

Pulls all VB City Council member vote history from the Accela IQM2 public API
and stores in vb_council_member_votes table in polls.db.

API base: https://virginiabeachva.iqm2.com/api
  GET /Department/1000/Members        -> list of council members with UserID
  GET /BoardMember/{UserID}/VoteHistory -> list of vote records per member

Usage:
  python ingest_vb_votes.py [--db PATH] [--reset]
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")

_API_BASE   = "https://virginiabeachva.iqm2.com/api"
_DEPT_ID    = 1000   # City Council department ID
_HEADERS    = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _get(path: str) -> dict | list:
    url = f"{_API_BASE}/{path}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _iso_to_date(s: str) -> str:
    """'2026-06-16T00:00:00.0000000-04:00' -> '2026-06-16'"""
    try:
        return datetime.fromisoformat(s[:10]).strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vb_council_member_votes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            member_name   TEXT    NOT NULL,
            district      TEXT,
            meeting_date  TEXT    NOT NULL,
            meeting_id    INTEGER NOT NULL,
            resolution_id INTEGER NOT NULL,
            title         TEXT,
            vote          TEXT,
            vote_yes      INTEGER DEFAULT 0,
            vote_no       INTEGER DEFAULT 0,
            vote_absent   INTEGER DEFAULT 0,
            vote_abstain  INTEGER DEFAULT 0,
            result        TEXT,
            UNIQUE(user_id, meeting_id, resolution_id)
        );
        CREATE INDEX IF NOT EXISTS idx_vbv_member   ON vb_council_member_votes(member_name);
        CREATE INDEX IF NOT EXISTS idx_vbv_meeting  ON vb_council_member_votes(meeting_id);
        CREATE INDEX IF NOT EXISTS idx_vbv_resol    ON vb_council_member_votes(resolution_id);
        CREATE INDEX IF NOT EXISTS idx_vbv_date     ON vb_council_member_votes(meeting_date);
    """)
    conn.commit()


def _upsert_votes(conn: sqlite3.Connection, rows: list[dict]) -> int:
    conn.executemany("""
        INSERT INTO vb_council_member_votes
            (user_id, member_name, district, meeting_date, meeting_id,
             resolution_id, title, vote, vote_yes, vote_no,
             vote_absent, vote_abstain, result)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, meeting_id, resolution_id) DO UPDATE SET
            title        = excluded.title,
            vote         = excluded.vote,
            vote_yes     = excluded.vote_yes,
            vote_no      = excluded.vote_no,
            vote_absent  = excluded.vote_absent,
            vote_abstain = excluded.vote_abstain,
            result       = excluded.result
    """, [
        (
            r["user_id"], r["member_name"], r["district"], r["meeting_date"],
            r["meeting_id"], r["resolution_id"], r["title"], r["vote"],
            r["vote_yes"], r["vote_no"], r["vote_absent"], r["vote_abstain"],
            r["result"],
        )
        for r in rows
    ])
    conn.commit()
    return len(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest VB City Council votes from Accela IQM2 API")
    p.add_argument("--db",    default=_DEFAULT_DB)
    p.add_argument("--reset", action="store_true", help="Drop and recreate vote table")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    if args.reset:
        conn.execute("DROP TABLE IF EXISTS vb_council_member_votes")
        conn.commit()
        print("Dropped vb_council_member_votes")

    _create_tables(conn)

    print(f"Fetching VB City Council members (dept {_DEPT_ID})…")
    try:
        members = _get(f"Department/{_DEPT_ID}/Members")
    except Exception as exc:
        print(f"ERROR fetching members: {exc}", file=sys.stderr)
        conn.close()
        return 1

    print(f"  Found {len(members)} members")

    total = 0
    for m in members:
        uid   = m["UserID"]
        name  = m["FullName"]
        title = m.get("Title", "")
        print(f"  Fetching votes for {name} (UserID={uid})…", end=" ", flush=True)

        try:
            data  = _get(f"BoardMember/{uid}/VoteHistory")
            votes = data.get("Votes", [])
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            time.sleep(1)
            continue

        rows = []
        for v in votes:
            rows.append({
                "user_id":       uid,
                "member_name":   name,
                "district":      title,
                "meeting_date":  _iso_to_date(v.get("MeetingDate", "")),
                "meeting_id":    v.get("MeetingID", 0),
                "resolution_id": v.get("ResolutionID", 0),
                "title":         (v.get("ResolutionShortTitle") or "")[:400],
                "vote":          v.get("UserVote", ""),
                "vote_yes":      v.get("VoteYes", 0),
                "vote_no":       v.get("VoteNo", 0),
                "vote_absent":   v.get("VoteAbsent", 0),
                "vote_abstain":  v.get("VoteAbstain", 0),
                "result":        v.get("VoteResultName", ""),
            })

        n = _upsert_votes(conn, rows)
        total += n
        print(f"{n} votes")
        time.sleep(0.3)   # be a polite scraper

    conn.close()
    print(f"\nDone — {total} vote records stored in {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
