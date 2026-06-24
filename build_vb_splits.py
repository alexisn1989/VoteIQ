"""
build_vb_splits.py
Precompute non-unanimous VB council votes into vb_split_votes.

For every resolution where at least one member voted No/Nay:
- Classify topic using same regex as dissent/dvadj
- Record yes_count, no_count, comma-separated No-voter names
- Store in vb_split_votes for fast AI context lookup

Usage:
    python build_vb_splits.py
    python build_vb_splits.py --reset
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


def _last_name(full: str) -> str:
    """Extract last name from 'First M. Last' or 'Dr. First Last' forms."""
    # strip trailing quote artifacts, then take last word
    name = full.strip().rstrip('"').strip()
    return name.split()[-1] if name else full


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vb_split_votes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_date  TEXT NOT NULL,
            meeting_id    INTEGER NOT NULL,
            resolution_id INTEGER NOT NULL,
            title         TEXT NOT NULL,
            topic         TEXT,
            yes_count     INTEGER,
            no_count      INTEGER,
            no_voters     TEXT,
            result        TEXT,
            updated_at    TEXT DEFAULT (datetime('now')),
            UNIQUE(meeting_id, resolution_id)
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
        conn.execute("DELETE FROM vb_split_votes")
        conn.commit()
        print("Cleared.")

    # Pull all resolutions where at least one member voted No
    rows = conn.execute("""
        SELECT
            MAX(meeting_date)  AS meeting_date,
            meeting_id,
            resolution_id,
            MAX(title)         AS title,
            MAX(vote_yes)      AS yes_count,
            MAX(vote_no)       AS no_count,
            MAX(result)        AS result,
            GROUP_CONCAT(CASE WHEN vote = 'No/Nay' THEN member_name END) AS no_voter_names
        FROM vb_council_member_votes
        GROUP BY meeting_id, resolution_id
        HAVING MAX(vote_no) > 0
        ORDER BY meeting_date DESC, meeting_id DESC
    """).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    for row in rows:
        meeting_date, meeting_id, resolution_id, title, yes_count, no_count, result, no_voter_names = row
        topic = classify_topic(title)

        # Compact no_voters to last names only
        if no_voter_names:
            no_voters = ", ".join(
                _last_name(n.strip())
                for n in no_voter_names.split(",")
                if n.strip()
            )
        else:
            no_voters = ""

        conn.execute("""
            INSERT OR REPLACE INTO vb_split_votes
                (meeting_date, meeting_id, resolution_id, title, topic,
                 yes_count, no_count, no_voters, result, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (meeting_date, meeting_id, resolution_id, title, topic,
              yes_count, no_count, no_voters, result, now))
        inserted += 1

    conn.commit()

    print(f"\nInserted {inserted} split votes.\n")

    # Print summary per member
    member_counts = conn.execute("""
        SELECT no_voters, COUNT(*) as cnt
        FROM vb_split_votes
        GROUP BY 1
        ORDER BY cnt DESC
        LIMIT 20
    """).fetchall()

    # Count per individual member
    from collections import Counter
    tally: Counter = Counter()
    for (voters_str,) in conn.execute("SELECT no_voters FROM vb_split_votes").fetchall():
        for name in (voters_str or "").split(","):
            name = name.strip()
            if name:
                tally[name] += 1

    print("No-vote totals:")
    for name, cnt in tally.most_common():
        print(f"  {cnt:3d}  {name}")

    # Print most contested (highest no_count)
    print("\nMost contested votes:")
    top = conn.execute("""
        SELECT meeting_date, topic, no_count, yes_count, no_voters, title
        FROM vb_split_votes
        ORDER BY no_count DESC, meeting_date DESC
        LIMIT 10
    """).fetchall()
    for dt, topic, no, yes, voters, title in top:
        t = f"[{topic}]" if topic else "[other]"
        print(f"  {dt}  {t:18s}  {yes}Y/{no}N  ({voters})  {title[:60]}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
