"""
build_norfolk_dvadj.py
Compute Norfolk council donor-sector ↔ vote-topic adjacency.

For each member's top donor sectors, maps to related vote topics and computes
their YES rate vs. council average on those topics. Stores results in
norfolk_donor_vote_adjacency. No causal inference — adjacency facts only.

Usage:
    python build_norfolk_dvadj.py
    python build_norfolk_dvadj.py --reset
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"

# Finance table uses short names; member_votes uses suffixed names.
_VOTE_NAME: dict[str, str] = {
    "Alexander": "Alexander",
    "Clanton":   "Clanton",
    "Doyle":     "Doyle",
    "Johnson":   "Johnson",
    "McGee":     "McGee",
    "Paige":     "Paige",
    "Royster":   "Royster",
    "Smigiel":   "Smigiel Jr.",
    "Thomas":    "Thomas Jr.",
}

# Which vote topics are substantively related to each donor sector.
SECTOR_TOPIC_MAP: dict[str, list[str]] = {
    "Real Estate":   ["rezoning", "housing", "short-term-rental"],
    "Hospitality":   ["short-term-rental", "economic-development"],
    "Finance":       ["budget", "economic-development"],
    "Legal":         ["rezoning", "economic-development"],
    "Healthcare":    ["public-safety", "budget"],
    "Defense":       ["public-safety", "infrastructure"],
    "Education":     ["schools", "budget"],
    "Government":    ["infrastructure", "personnel"],
    "Energy":        ["environment", "infrastructure"],
    "Tech":          ["infrastructure", "economic-development"],
    "Retired":       [],   # no meaningful topic mapping
    "Other":         [],
}

_SKIP_CATEGORIES = {"procedural", "other", ""}


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_donor_vote_adjacency (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            member_name      TEXT NOT NULL,
            sector           TEXT NOT NULL,
            sector_pct       REAL,
            sector_amt       REAL,
            topic            TEXT NOT NULL,
            member_yes_pct   REAL,
            council_yes_pct  REAL,
            delta_pp         REAL,
            topic_vote_count INTEGER,
            updated_at       TEXT DEFAULT (datetime('now')),
            UNIQUE(member_name, sector, topic)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_donor_vote_summary (
            member_name       TEXT NOT NULL,
            sector            TEXT NOT NULL,
            sector_pct        REAL,
            sector_amt        REAL,
            top_topic         TEXT,
            top_topic_delta   REAL,
            top_topic_yes_pct REAL,
            council_yes_pct   REAL,
            topic_vote_count  INTEGER,
            signal_text       TEXT,
            updated_at        TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (member_name, sector)
        )
    """)
    conn.commit()


def load_member_topic_rates(conn: sqlite3.Connection) -> dict[str, dict[str, dict]]:
    """Returns {vote_name: {topic: {yes_pct, vote_count}}}.
    Topics come from norfolk_vote_enrichment joined on title."""
    rows = conn.execute("""
        SELECT mv.member_name, e.topic,
               COUNT(*) total,
               SUM(CASE WHEN LOWER(mv.vote)='yes' THEN 1 ELSE 0 END) yes_cnt
        FROM norfolk_council_member_votes mv
        JOIN norfolk_vote_enrichment e ON e.title = mv.title
        WHERE mv.category IN ('substantive', 'consent')
          AND e.topic NOT IN ('procedural', 'other', '')
          AND e.topic IS NOT NULL
        GROUP BY mv.member_name, e.topic
    """).fetchall()
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        m, topic, total, yes = r
        if total < 3:
            continue
        out.setdefault(m, {})[topic] = {
            "yes_pct": round(100.0 * yes / total, 1),
            "vote_count": total,
        }
    return out


def load_council_topic_rates(conn: sqlite3.Connection) -> dict[str, dict]:
    """Returns {topic: {yes_pct, vote_count}} across all members."""
    rows = conn.execute("""
        SELECT e.topic,
               COUNT(*) total,
               SUM(CASE WHEN LOWER(mv.vote)='yes' THEN 1 ELSE 0 END) yes_cnt
        FROM norfolk_council_member_votes mv
        JOIN norfolk_vote_enrichment e ON e.title = mv.title
        WHERE mv.category IN ('substantive', 'consent')
          AND e.topic NOT IN ('procedural', 'other', '')
          AND e.topic IS NOT NULL
        GROUP BY e.topic
    """).fetchall()
    return {
        r[0]: {"yes_pct": round(100.0 * r[2] / r[1], 1), "vote_count": r[1]}
        for r in rows if r[1] >= 5
    }


def load_finance(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Returns {finance_name: [{sector, pct, amt}, ...]} sorted by pct desc."""
    rows = conn.execute("""
        SELECT member_name, sector, pct_of_total, total_amount
        FROM norfolk_finance_totals
        ORDER BY member_name, pct_of_total DESC
    """).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append({
            "sector": r[1], "pct": r[2], "amt": r[3]
        })
    return out


def _signal_text(member: str, sector: str, sector_pct: float,
                 topic: str, member_yes: float, council_yes: float,
                 delta: float, n: int) -> str:
    direction = "more" if delta > 0 else "less"
    return (
        f"{member} ({sector} {sector_pct:.0f}% of funds): "
        f"votes YES on {topic} {member_yes:.0f}% vs council avg {council_yes:.0f}% "
        f"(delta {delta:+.0f}pp, {n} votes). "
        f"[adjacency only — no causal inference]"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_tables(conn)

    if args.reset:
        conn.execute("DELETE FROM norfolk_donor_vote_adjacency")
        conn.execute("DELETE FROM norfolk_donor_vote_summary")
        conn.commit()
        print("Cleared existing adjacency data.")

    member_rates = load_member_topic_rates(conn)
    council_rates = load_council_topic_rates(conn)
    finance = load_finance(conn)
    now = datetime.now(timezone.utc).isoformat()

    for fin_name, sectors in finance.items():
        vote_name = _VOTE_NAME.get(fin_name)
        if not vote_name or vote_name not in member_rates:
            print(f"  {fin_name}: no vote data (skipping)")
            continue

        m_rates = member_rates[vote_name]

        for sec in sectors[:5]:           # top 5 sectors only
            sector = sec["sector"]
            topics = SECTOR_TOPIC_MAP.get(sector, [])
            if not topics:
                continue

            topic_rows: list[tuple] = []
            for topic in topics:
                if topic not in m_rates or topic not in council_rates:
                    continue
                m_yes = m_rates[topic]["yes_pct"]
                c_yes = council_rates[topic]["yes_pct"]
                n     = m_rates[topic]["vote_count"]
                delta = round(m_yes - c_yes, 1)
                topic_rows.append((topic, m_yes, c_yes, delta, n))
                conn.execute("""
                    INSERT OR REPLACE INTO norfolk_donor_vote_adjacency
                        (member_name, sector, sector_pct, sector_amt,
                         topic, member_yes_pct, council_yes_pct, delta_pp,
                         topic_vote_count, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (fin_name, sector, sec["pct"], sec["amt"],
                      topic, m_yes, c_yes, delta, n, now))

            if not topic_rows:
                continue

            # Pick the topic with the largest absolute delta for summary
            top = max(topic_rows, key=lambda x: abs(x[3]))
            sig = _signal_text(fin_name, sector, sec["pct"],
                               top[0], top[1], top[2], top[3], top[4])
            conn.execute("""
                INSERT OR REPLACE INTO norfolk_donor_vote_summary
                    (member_name, sector, sector_pct, sector_amt,
                     top_topic, top_topic_delta, top_topic_yes_pct,
                     council_yes_pct, topic_vote_count, signal_text, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (fin_name, sector, sec["pct"], sec["amt"],
                  top[0], top[3], top[1], top[2], top[4], sig, now))

        conn.commit()
        # Print summary for this member
        rows = conn.execute("""
            SELECT sector, sector_pct, top_topic, top_topic_delta
            FROM norfolk_donor_vote_summary WHERE member_name=?
            ORDER BY ABS(top_topic_delta) DESC
        """, (fin_name,)).fetchall()
        print(f"\n  {fin_name} ({vote_name}):")
        for r in rows:
            sign = "+" if r[3] >= 0 else ""
            print(f"    {r[1]:4.0f}% {r[0]:15s} -> {r[2]:22s} delta {sign}{r[3]:.0f}pp")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
