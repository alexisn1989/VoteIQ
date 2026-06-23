"""
build_vb_dissent.py
Precompute per-member No-vote rates vs council average by topic.
Stores results in vb_member_dissent for fast context injection.

Topics are classified from vote titles via regex (same patterns as
build_vb_dvadj.py) — no separate enrichment table required.

Usage:
    python build_vb_dissent.py
    python build_vb_dissent.py --reset
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"

TOPIC_PATTERNS: list[tuple[str, str]] = [
    ("rezoning",       r"rezon|conditional use permit|subdivision|variance|encroachment|"
                       r"street closure|comprehensive plan|change of zoning|alley closure|"
                       r"nonconforming|special exception"),
    ("development",    r"develop|property acquisition|purchase of property|sale of property|"
                       r"easement|right.of.way|acquisition of|ground lease|cda "),
    ("budget",         r"appropriat|budget|transfer of funds|bond issue|tax levy|cip |"
                       r"capital improvement|fiscal year|tax rate|revenue sharing"),
    ("defense",        r"navy|naval|military|joint expeditionary|oceana|dam neck|"
                       r"shore command|littoral|armed forces"),
    ("infrastructure", r"infrastructure|road|roadway|utility|water system|sewer|storm "
                       r"|stormwater|bridge|transit|light rail|bus rapid|highway|traffic"),
    ("public-safety",  r"police|fire station|fire department|rescue|emergency|public safety|"
                       r"crime|ems |ambulance|communications center"),
    ("economic-dev",   r"economic develop|tourism|resort|convention center|sports complex|"
                       r"event center|amphitheater|hotel|entertainment district|tif "),
    ("governance",     r"charter amendment|ordinance to adopt|ordinance to amend|policy|"
                       r"appointment of|task force|personnel|labor agreement|collective "
                       r"|contract with|service agreement"),
    ("environment",    r"environment|climate|wetland|shoreline|beach nourishment|"
                       r"tree canopy|open space|preserve|bay|creek|living shoreline"),
    ("schools",        r"school|education|library|recreation center|park |parks and"),
]


def classify_topic(title: str) -> str | None:
    t = (title or "").lower()
    for topic, pat in TOPIC_PATTERNS:
        if re.search(pat, t):
            return topic
    return None


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vb_member_dissent (
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
        conn.execute("DELETE FROM vb_member_dissent")
        conn.commit()
        print("Cleared.")

    # Load all Yes/No votes with classified topics
    rows = conn.execute("""
        SELECT member_name, title, vote
        FROM vb_council_member_votes
        WHERE vote IN ('Yes/Aye', 'No/Nay')
    """).fetchall()

    # Accumulate per-topic council totals and per-member per-topic counts
    council: dict[str, dict] = {}   # {topic: {no, total}}
    member_map: dict[str, dict[str, dict]] = {}  # {member: {topic: {no, total}}}

    for member, title, vote in rows:
        topic = classify_topic(title)
        if not topic:
            continue
        is_no = vote == "No/Nay"

        c = council.setdefault(topic, {"no": 0, "total": 0})
        c["total"] += 1
        if is_no:
            c["no"] += 1

        m = member_map.setdefault(member, {}).setdefault(topic, {"no": 0, "total": 0})
        m["total"] += 1
        if is_no:
            m["no"] += 1

    # Council No-rate per topic
    council_rates: dict[str, tuple[float, int]] = {}
    for topic, c in council.items():
        if c["total"] >= 10:
            council_rates[topic] = (round(100.0 * c["no"] / c["total"], 1), c["total"])

    now = datetime.now(timezone.utc).isoformat()
    members_done: set[str] = set()

    for member, topics in member_map.items():
        for topic, m in topics.items():
            if m["total"] < 5 or topic not in council_rates:
                continue
            c_rate, _ = council_rates[topic]
            m_rate = round(100.0 * m["no"] / m["total"], 1)
            delta = round(m_rate - c_rate, 1)
            conn.execute("""
                INSERT OR REPLACE INTO vb_member_dissent
                    (member_name, topic, member_no_pct, council_no_pct,
                     delta_pp, member_no_count, topic_vote_count, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (member, topic, m_rate, c_rate, delta,
                  m["no"], m["total"], now))
            members_done.add(member)

    conn.commit()

    # Print: top distinctive rows per member (|delta| >= 1)
    for member in sorted(members_done):
        rows_out = conn.execute("""
            SELECT topic, member_no_pct, council_no_pct, delta_pp, member_no_count
            FROM vb_member_dissent
            WHERE member_name = ? AND ABS(delta_pp) >= 1
            ORDER BY ABS(delta_pp) DESC LIMIT 5
        """, (member,)).fetchall()
        if not rows_out:
            continue
        print(f"\n{member}:")
        for topic, m, c, d, nos in rows_out:
            sign = "+" if d >= 0 else ""
            print(f"  {topic:25s}  {m:.1f}% No vs {c:.1f}% avg  "
                  f"delta {sign}{d:.1f}pp  ({nos} No votes)")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
