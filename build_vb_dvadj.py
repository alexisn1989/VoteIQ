"""
build_vb_dvadj.py
Compute Virginia Beach City Council donor-sector ↔ vote-topic adjacency.

For each member's top donor sectors, maps to related vote topics and computes
their YES rate vs. the council average on those topics. Stores results in
vb_donor_vote_adjacency and vb_donor_vote_summary.

Topics are classified from vote titles via regex — no separate enrichment table
is required (unlike Norfolk, which uses norfolk_vote_enrichment).

No causal inference — adjacency facts only.

Usage:
    python build_vb_dvadj.py
    python build_vb_dvadj.py --reset
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"

# ── Topic patterns (checked in order; first match wins) ──────────────────────
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


# ── Sector → related topics ───────────────────────────────────────────────────
SECTOR_TOPIC_MAP: dict[str, list[str]] = {
    "Real Estate":   ["rezoning", "development"],
    "Hospitality":   ["economic-dev", "rezoning"],
    "Finance":       ["budget", "economic-dev"],
    "Legal":         ["rezoning", "governance"],
    "Healthcare":    ["public-safety", "budget"],
    "Defense":       ["defense", "infrastructure"],
    "Education":     ["schools", "budget"],
    "Government":    ["governance", "infrastructure"],
    "Energy":        ["environment", "infrastructure"],
    "Tech":          ["infrastructure", "economic-dev"],
    "Retired":       [],
    "Other":         [],
}

# Finance key → substring in vb_council_member_votes.member_name
_VOTE_NAME_PARTIAL: dict[str, str] = {
    "Berlucchi":     "Berlucchi",
    "Dyer":          "Dyer",
    "Henley":        "Henley",
    "Hutcheson":     "Hutcheson",
    "Wilson":        "Wilson",
    "Remick":        "Remick",
    "Ross-Hammond":  "Ross-Hammond",
    "Schulman":      "Schulman",
    "Rouse":         "Rouse",
    "Cummings":      "Cummings",
    "Jackson-Green": "Jackson-Green",
}


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vb_donor_vote_adjacency (
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
        );
        CREATE TABLE IF NOT EXISTS vb_donor_vote_summary (
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
        );
    """)
    conn.commit()


def resolve_vote_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Map finance short keys to full member_name strings from the vote table."""
    all_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT member_name FROM vb_council_member_votes"
    ).fetchall()]
    out: dict[str, str] = {}
    for fin_key, partial in _VOTE_NAME_PARTIAL.items():
        for name in all_names:
            if partial.lower() in name.lower():
                out[fin_key] = name
                break
    return out


def load_member_topic_rates(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, dict]]:
    """Returns {full_member_name: {topic: {yes_pct, vote_count}}}."""
    rows = conn.execute("""
        SELECT member_name, title, vote
        FROM vb_council_member_votes
        WHERE vote IN ('Yes/Aye', 'No/Nay')
    """).fetchall()

    # accumulate per-member per-topic
    accum: dict[str, dict[str, dict]] = {}
    for member, title, vote in rows:
        topic = classify_topic(title)
        if not topic:
            continue
        t = accum.setdefault(member, {}).setdefault(topic, {"yes": 0, "total": 0})
        t["total"] += 1
        if vote == "Yes/Aye":
            t["yes"] += 1

    out: dict[str, dict[str, dict]] = {}
    for member, topics in accum.items():
        for topic, c in topics.items():
            if c["total"] < 5:
                continue
            out.setdefault(member, {})[topic] = {
                "yes_pct": round(100.0 * c["yes"] / c["total"], 1),
                "vote_count": c["total"],
            }
    return out


def load_council_topic_rates(conn: sqlite3.Connection) -> dict[str, dict]:
    """Returns {topic: {yes_pct, vote_count}} aggregated across all members."""
    rows = conn.execute("""
        SELECT title,
               SUM(CASE WHEN vote='Yes/Aye' THEN 1 ELSE 0 END) yes_cnt,
               COUNT(*) total
        FROM vb_council_member_votes
        WHERE vote IN ('Yes/Aye', 'No/Nay')
        GROUP BY title
    """).fetchall()

    topic_accum: dict[str, dict] = {}
    for title, yes, total in rows:
        topic = classify_topic(title)
        if not topic:
            continue
        ta = topic_accum.setdefault(topic, {"yes": 0, "total": 0})
        ta["yes"] += yes
        ta["total"] += total

    return {
        topic: {
            "yes_pct": round(100.0 * c["yes"] / c["total"], 1),
            "vote_count": c["total"],
        }
        for topic, c in topic_accum.items()
        if c["total"] >= 10
    }


def load_finance(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Returns {fin_key: [{sector, pct, amt}, ...]} sorted by pct desc."""
    rows = conn.execute("""
        SELECT member_name, sector, pct_of_total, total_amount
        FROM vb_finance_totals
        ORDER BY member_name, pct_of_total DESC
    """).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append({"sector": r[1], "pct": r[2], "amt": r[3]})
    return out


def _signal_text(
    member: str, sector: str, sector_pct: float,
    topic: str, m_yes: float, c_yes: float, delta: float, n: int,
) -> str:
    return (
        f"{member} ({sector} {sector_pct:.0f}% of funds): "
        f"votes YES on {topic} {m_yes:.0f}% vs council avg {c_yes:.0f}% "
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
        conn.execute("DELETE FROM vb_donor_vote_adjacency")
        conn.execute("DELETE FROM vb_donor_vote_summary")
        conn.commit()
        print("Cleared existing VB adjacency data.")

    vote_name_map = resolve_vote_names(conn)
    member_rates = load_member_topic_rates(conn)
    council_rates = load_council_topic_rates(conn)
    finance = load_finance(conn)
    now = datetime.now(timezone.utc).isoformat()

    print(f"\nCouncil topic rates: {list(council_rates.keys())}")
    print(f"Members with vote data: {list(member_rates.keys())[:3]} ...")

    for fin_key, sectors in finance.items():
        vote_name = vote_name_map.get(fin_key)
        if not vote_name or vote_name not in member_rates:
            print(f"  {fin_key}: no vote data (skipping)")
            continue

        m_rates = member_rates[vote_name]

        for sec in sectors[:6]:
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
                    INSERT OR REPLACE INTO vb_donor_vote_adjacency
                        (member_name, sector, sector_pct, sector_amt,
                         topic, member_yes_pct, council_yes_pct, delta_pp,
                         topic_vote_count, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (fin_key, sector, sec["pct"], sec["amt"],
                      topic, m_yes, c_yes, delta, n, now))

            if not topic_rows:
                continue

            top = max(topic_rows, key=lambda x: abs(x[3]))
            sig = _signal_text(fin_key, sector, sec["pct"],
                               top[0], top[1], top[2], top[3], top[4])
            conn.execute("""
                INSERT OR REPLACE INTO vb_donor_vote_summary
                    (member_name, sector, sector_pct, sector_amt,
                     top_topic, top_topic_delta, top_topic_yes_pct,
                     council_yes_pct, topic_vote_count, signal_text, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (fin_key, sector, sec["pct"], sec["amt"],
                  top[0], top[3], top[1], top[2], top[4], sig, now))

        conn.commit()

        rows = conn.execute("""
            SELECT sector, sector_pct, top_topic, top_topic_delta
            FROM vb_donor_vote_summary WHERE member_name=?
            ORDER BY ABS(top_topic_delta) DESC
        """, (fin_key,)).fetchall()
        print(f"\n  {fin_key} ({vote_name}):")
        for r in rows:
            sign = "+" if r[3] >= 0 else ""
            print(f"    {r[1]:4.0f}% {r[0]:15s} -> {r[2]:22s} delta {sign}{r[3]:.0f}pp")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
