"""
build_norfolk_dissent.py
Precompute per-member No-vote rates vs council average by topic.
Stores results in norfolk_member_dissent for fast chat context injection.

Usage:
    python build_norfolk_dissent.py
    python build_norfolk_dissent.py --reset
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_member_dissent (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            member_name      TEXT NOT NULL,
            topic            TEXT NOT NULL,
            member_no_pct    REAL,
            council_no_pct   REAL,
            delta_pp         REAL,
            member_no_count  INTEGER,
            topic_vote_count INTEGER,
            updated_at       TEXT DEFAULT (datetime('now')),
            UNIQUE(member_name, topic)
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
        conn.execute("DELETE FROM norfolk_member_dissent")
        conn.commit()
        print("Cleared.")

    # Council-wide No rate per topic
    council_rates: dict[str, tuple[float, int]] = {}
    for r in conn.execute("""
        SELECT e.topic,
               SUM(CASE WHEN LOWER(mv.vote)='no' THEN 1 ELSE 0 END),
               COUNT(*)
        FROM norfolk_council_member_votes mv
        JOIN norfolk_vote_enrichment e ON e.title = mv.title
        WHERE mv.category IN ('substantive','consent')
          AND e.topic NOT IN ('procedural','other','')
        GROUP BY e.topic
    """):
        topic, nos, total = r
        council_rates[topic] = (round(100.0 * nos / total, 1) if total else 0, total)

    now = datetime.now(timezone.utc).isoformat()
    members_done: set[str] = set()

    for r in conn.execute("""
        SELECT mv.member_name, e.topic,
               SUM(CASE WHEN LOWER(mv.vote)='no' THEN 1 ELSE 0 END) nos,
               COUNT(*) total
        FROM norfolk_council_member_votes mv
        JOIN norfolk_vote_enrichment e ON e.title = mv.title
        WHERE mv.category IN ('substantive','consent')
          AND e.topic NOT IN ('procedural','other','')
        GROUP BY mv.member_name, e.topic
        HAVING total >= 5
    """):
        member, topic, nos, total = r
        c_rate, _ = council_rates.get(topic, (0.0, 0))
        m_rate = round(100.0 * nos / total, 1)
        delta = round(m_rate - c_rate, 1)
        conn.execute("""
            INSERT OR REPLACE INTO norfolk_member_dissent
                (member_name, topic, member_no_pct, council_no_pct,
                 delta_pp, member_no_count, topic_vote_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (member, topic, m_rate, c_rate, delta, nos, total, now))
        members_done.add(member)

    conn.commit()

    # Print summary: top distinctive rows per member
    for member in sorted(members_done):
        rows = conn.execute("""
            SELECT topic, member_no_pct, council_no_pct, delta_pp, member_no_count
            FROM norfolk_member_dissent
            WHERE member_name = ? AND ABS(delta_pp) >= 2
            ORDER BY ABS(delta_pp) DESC LIMIT 4
        """, (member,)).fetchall()
        if not rows:
            continue
        print(f"\n{member}:")
        for topic, m, c, d, nos in rows:
            sign = "+" if d >= 0 else ""
            print(f"  {topic:25s}  {m:.1f}% vs {c:.1f}% avg  delta {sign}{d:.1f}pp  ({nos} No votes)")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
