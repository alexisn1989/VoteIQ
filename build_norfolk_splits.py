"""
build_norfolk_splits.py
Precompute non-unanimous Norfolk council votes into norfolk_split_votes.

For every vote where at least one member voted No:
- Parse yes/no counts from vote_count string (e.g. "7-1")
- Pull topic from norfolk_vote_enrichment (pre-enriched)
- Record no_voter last names from norfolk_council_member_votes
- Store in norfolk_split_votes for fast named-member AI context lookup

Usage:
    python build_norfolk_splits.py
    python build_norfolk_splits.py --reset
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _last_name(full: str) -> str:
    name = full.strip().rstrip('"').strip()
    parts = name.split()
    while parts and parts[-1].lower().rstrip(".") in _SUFFIXES:
        parts.pop()
    return parts[-1] if parts else full


def _parse_vote_count(vc: str) -> tuple[int, int]:
    """Parse '7-1' → (yes=7, no=1). Returns (0,0) on failure."""
    if not vc or "-" not in vc:
        return 0, 0
    parts = vc.split("-", 1)
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_split_votes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_date  TEXT NOT NULL,
            meeting_id    INTEGER NOT NULL,
            agenda_item   TEXT NOT NULL,
            title         TEXT NOT NULL,
            topic         TEXT,
            yes_count     INTEGER,
            no_count      INTEGER,
            no_voters     TEXT,
            result        TEXT,
            updated_at    TEXT DEFAULT (datetime('now')),
            UNIQUE(meeting_id, agenda_item)
        )
    """)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM norfolk_split_votes")
        conn.commit()
        print("Cleared.")

    # Pull all votes with at least one No (no_count > 0)
    rows = conn.execute("""
        SELECT cv.id, cv.meeting_id, cv.meeting_date, cv.agenda_item,
               cv.title, cv.vote_count, cv.result, e.topic
        FROM norfolk_council_votes cv
        LEFT JOIN norfolk_vote_enrichment e ON lower(e.title) = lower(cv.title)
        WHERE cv.vote_count LIKE '%-%'
          AND CAST(SUBSTR(cv.vote_count, INSTR(cv.vote_count,'-')+1) AS INTEGER) > 0
        ORDER BY cv.meeting_date DESC
    """).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    for vote_id, meeting_id, meeting_date, agenda_item, title, vote_count, result, topic in rows:
        yes_count, no_count = _parse_vote_count(vote_count or "")
        if no_count == 0:
            continue

        # Get no-voters for this specific vote
        no_voter_rows = conn.execute("""
            SELECT member_name FROM norfolk_council_member_votes
            WHERE vote_id = ? AND lower(vote) IN ('no', 'nay', 'no/nay')
        """, (vote_id,)).fetchall()

        no_voters = ", ".join(
            _last_name(r[0]) for r in no_voter_rows if r[0]
        )

        conn.execute("""
            INSERT OR REPLACE INTO norfolk_split_votes
                (meeting_date, meeting_id, agenda_item, title, topic,
                 yes_count, no_count, no_voters, result, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (meeting_date, meeting_id, agenda_item, title, topic,
              yes_count, no_count, no_voters, result, now))
        inserted += 1

    conn.commit()
    print(f"\nInserted {inserted} split votes.\n")

    # Per-member No-vote tallies
    from collections import Counter
    tally: Counter = Counter()
    for (voters_str,) in conn.execute("SELECT no_voters FROM norfolk_split_votes").fetchall():
        for name in (voters_str or "").split(","):
            name = name.strip()
            if name:
                tally[name] += 1

    print("No-vote totals:")
    for name, cnt in tally.most_common():
        print(f"  {cnt:3d}  {name}")

    # Most contested
    print("\nMost contested votes:")
    top = conn.execute("""
        SELECT meeting_date, topic, no_count, yes_count, no_voters, title
        FROM norfolk_split_votes
        ORDER BY no_count DESC, meeting_date DESC
        LIMIT 10
    """).fetchall()
    for dt, topic, no, yes, voters, title in top:
        t = f"[{topic}]" if topic else "[other]"
        print(f"  {dt}  {t:22s}  {yes}Y/{no}N  ({voters})  {title[:55]}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
