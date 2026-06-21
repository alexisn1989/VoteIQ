"""
build_norfolk_blocs.py
Precompute pairwise voting agreement rates for Norfolk City Council members.
Stores results in norfolk_voting_blocs for fast context injection.

Usage:
    python build_norfolk_blocs.py
    python build_norfolk_blocs.py --reset
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_voting_blocs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            member_a        TEXT NOT NULL,
            member_b        TEXT NOT NULL,
            agreement_pct   REAL,
            shared_votes    INTEGER,
            agree_count     INTEGER,
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(member_a, member_b)
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
        conn.execute("DELETE FROM norfolk_voting_blocs")
        conn.commit()
        print("Cleared.")

    rows = conn.execute("""
        SELECT member_name, agenda_item, meeting_date, vote
        FROM norfolk_council_member_votes
        WHERE category = 'substantive'
          AND LOWER(vote) IN ('yes', 'no')
    """).fetchall()

    # (agenda_item, meeting_date) -> {member: vote}
    vote_map: dict[tuple, dict[str, str]] = {}
    for member, item, date, vote in rows:
        vote_map.setdefault((item, date), {})[member] = vote.lower()

    members = sorted({r[0] for r in rows})
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    for a, b in combinations(members, 2):
        shared = agree = 0
        for votes in vote_map.values():
            if a in votes and b in votes:
                shared += 1
                if votes[a] == votes[b]:
                    agree += 1
        if shared < 5:
            continue
        pct = round(100.0 * agree / shared, 1)
        for x, y in ((a, b), (b, a)):
            conn.execute("""
                INSERT OR REPLACE INTO norfolk_voting_blocs
                    (member_a, member_b, agreement_pct, shared_votes, agree_count, updated_at)
                VALUES (?,?,?,?,?,?)
            """, (x, y, pct, shared, agree, now))
        inserted += 2

    conn.commit()

    # Print matrix
    print(f"\nPairwise voting agreement — substantive votes only (min 5 shared)\n")
    col_w = 14
    header = " " * col_w + "  ".join(f"{m[:col_w]:>{col_w}}" for m in members)
    print(header)
    for a in members:
        row_parts = []
        for b in members:
            if a == b:
                row_parts.append(f"{'---':>{col_w}}")
            else:
                r = conn.execute(
                    "SELECT agreement_pct FROM norfolk_voting_blocs "
                    "WHERE member_a=? AND member_b=?", (a, b)
                ).fetchone()
                row_parts.append(f"{str(round(r[0])) + '%':>{col_w}}" if r else f"{'N/A':>{col_w}}")
        print(f"{a[:col_w]:>{col_w}}  {'  '.join(row_parts)}")

    conn.close()
    print(f"\nInserted {inserted} row pairs. Done.")


if __name__ == "__main__":
    main()
