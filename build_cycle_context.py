"""
build_cycle_context.py

Populates the cycle_context table in polls.db with:
  1. HARDCODED entries for known VA regulatory/legislative fights 2013-2021
     (pre-dates our structured bill data)
  2. BILL-MATCHED entries for 2022+ using legiscan_va_bills keyword search

The table annotates donation spikes in donor_cycle_trends with "what was
at stake" — enabling the investment-return narrative.

Usage:
    python build_cycle_context.py
    python build_cycle_context.py --cycles 2022 2024  # bill-match only
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cycle_context (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    election_cycle TEXT    NOT NULL,
    donor_sector   TEXT,            -- NULL = cross-sector; sector name for targeted
    event_type     TEXT    NOT NULL, -- 'legislation' | 'regulatory' | 'rate_case' | 'election'
    title          TEXT    NOT NULL,
    description    TEXT,
    bill_id        TEXT,            -- NULL for hardcoded; bill_id from legiscan for matched
    bill_number    TEXT,
    significance   TEXT    DEFAULT 'medium', -- 'high' | 'medium' | 'low'
    source         TEXT    DEFAULT 'hardcoded', -- 'hardcoded' | 'bill_match'
    UNIQUE(election_cycle, title)
);
CREATE INDEX IF NOT EXISTS idx_cc_cycle  ON cycle_context(election_cycle);
CREATE INDEX IF NOT EXISTS idx_cc_sector ON cycle_context(donor_sector);
CREATE INDEX IF NOT EXISTS idx_cc_sig    ON cycle_context(significance);
"""

# ── Hardcoded historical context (2013–2021) ──────────────────────────────────
# Sources: Virginia General Assembly records, SCC docket history, press reporting.
# Each entry maps a legislative cycle to the dominant regulatory fight that drove
# unusual donor activity.

HARDCODED: list[dict] = [
    # ── 2013 ──────────────────────────────────────────────────────────────────
    {
        "election_cycle": "2013",
        "donor_sector":   "Energy/Utilities",
        "event_type":     "legislation",
        "title":          "Rate Freeze Act (SB1349 / HB2690)",
        "description":    (
            "Froze SCC biennial review of Dominion Virginia Power's base rates "
            "through 2022. Prevented $1B+ in potential customer refunds. Dominion "
            "donated $4.6M in 2013 — 7x its 2012 baseline. Passed with bipartisan "
            "support after $646K in legislator donations during the session."
        ),
        "bill_number":  "SB1349",
        "significance": "high",
    },
    {
        "election_cycle": "2013",
        "donor_sector":   "Energy/Utilities",
        "event_type":     "legislation",
        "title":          "Uranium Mining Moratorium debate",
        "description":    (
            "Competing interests over lifting VA's decades-long uranium mining ban "
            "at Coles Hill (est. 119M lbs of ore). Mining and energy sector donors "
            "active on both sides."
        ),
        "significance": "medium",
    },
    # ── 2015 ──────────────────────────────────────────────────────────────────
    {
        "election_cycle": "2015",
        "donor_sector":   "Energy/Utilities",
        "event_type":     "legislation",
        "title":          "Rate Freeze Extension (SB1349 follow-on)",
        "description":    (
            "Extended Dominion's rate freeze protection; blocked SCC from ordering "
            "refunds despite Dominion earning $300M above its authorized return. "
            "Dominion donated $3.2M in 2015 — 7x its 2014 even-year baseline."
        ),
        "significance": "high",
    },
    {
        "election_cycle": "2015",
        "donor_sector":   "Finance",
        "event_type":     "legislation",
        "title":          "Consumer lending and payday loan regulations",
        "description":    (
            "Multiple bills targeting payday/title lending rates and consumer "
            "finance disclosures. Finance sector donations elevated in both "
            "2015 and 2016 cycles."
        ),
        "significance": "medium",
    },
    # ── 2017 ──────────────────────────────────────────────────────────────────
    {
        "election_cycle": "2017",
        "donor_sector":   "Energy/Utilities",
        "event_type":     "legislation",
        "title":          "Grid Transformation and Security Act (HB2291 / SB1416)",
        "description":    (
            "Authorized Dominion and Appalachian Power to invest $4.3B in grid "
            "modernization while shielding that spending from SCC prudency review. "
            "Included offshore wind pilot at Virginia Beach. Critics called it a "
            "blank check; supporters cited resilience and jobs."
        ),
        "bill_number":  "HB2291",
        "significance": "high",
    },
    {
        "election_cycle": "2017",
        "donor_sector":   None,
        "event_type":     "election",
        "title":          "Governor's race — Northam vs. Gillespie",
        "description":    (
            "High-spending gubernatorial cycle. Total VA campaign finance "
            "reached $202M in 2017 — highest odd-year total to that point. "
            "Both energy and ideological donors elevated across all sectors."
        ),
        "significance": "medium",
    },
    # ── 2019 ──────────────────────────────────────────────────────────────────
    {
        "election_cycle": "2019",
        "donor_sector":   "Energy/Utilities",
        "event_type":     "legislation",
        "title":          "Virginia Clean Economy Act — initial drafts",
        "description":    (
            "Pre-legislative negotiations over what would become the VCEA. "
            "Environmental groups and Clean Virginia Fund ramp donations to "
            "counter Dominion. Democratic takeover of both chambers in Nov 2019 "
            "shifts the legislative landscape."
        ),
        "significance": "high",
    },
    {
        "election_cycle": "2019",
        "donor_sector":   "Alcohol/Gambling",
        "event_type":     "legislation",
        "title":          "Alcohol Beverage Control privatization and casino bills",
        "description":    (
            "Series of bills to permit casino gaming in five Virginia cities "
            "(Bristol, Danville, Norfolk, Portsmouth, Richmond) and restructure "
            "ABC. Gaming and hospitality donors sharply elevated."
        ),
        "significance": "high",
    },
    # ── 2020 ──────────────────────────────────────────────────────────────────
    {
        "election_cycle": "2020",
        "donor_sector":   "Energy/Utilities",
        "event_type":     "legislation",
        "title":          "Virginia Clean Economy Act signed (April 2020)",
        "description":    (
            "Landmark law requiring 100% clean electricity by 2045 (Dominion) / "
            "2050 (Appalachian). Dominion's even-year spend jumped to $1.4M "
            "(1.1x even-year mean) — elevated vs. prior even years as the bill "
            "moved through the session."
        ),
        "significance": "high",
    },
    {
        "election_cycle": "2020",
        "donor_sector":   "Alcohol/Gambling",
        "event_type":     "legislation",
        "title":          "Casino referendum enabling legislation",
        "description":    (
            "HB4 / SB36 authorizing casino referenda in Bristol, Danville, "
            "Norfolk, Portsmouth, and Richmond. Gaming donors ($MGM, $Caesars, "
            "Hard Rock, Rush Street) among largest even-year institutional givers."
        ),
        "significance": "high",
    },
    # ── 2021 ──────────────────────────────────────────────────────────────────
    {
        "election_cycle": "2021",
        "donor_sector":   "Energy/Utilities",
        "event_type":     "legislation",
        "title":          "VCEA implementation + Offshore Wind approval",
        "description":    (
            "Coastal Virginia Offshore Wind (CVOW) 2.6 GW project regulatory "
            "approvals; SCC rate review proceedings under VCEA framework. "
            "Dominion donated $6.1M in 2021 — highest odd-year total to that "
            "point. Clean Virginia Fund matched at $5.1M."
        ),
        "significance": "high",
    },
    {
        "election_cycle": "2021",
        "donor_sector":   None,
        "event_type":     "election",
        "title":          "Governor's race — Youngkin vs. McAuliffe",
        "description":    (
            "Highest-spending odd-year cycle in VA history to that point: "
            "$385M total. Youngkin's self-funded campaign plus national Democratic "
            "spending drove record donations across all sectors."
        ),
        "significance": "high",
    },
    {
        "election_cycle": "2021",
        "donor_sector":   "Healthcare",
        "event_type":     "legislation",
        "title":          "Medicaid expansion aftermath + mental health funding",
        "description":    (
            "Post-Medicaid expansion (2019) implementation; hospital capacity "
            "and behavioral health facility legislation. Healthcare sector "
            "donations reached a cycle peak in 2021."
        ),
        "significance": "medium",
    },
]


# ── Bill-matching config for 2022+ ────────────────────────────────────────────
# Each entry: (donor_sector, event_type, significance, [title_keywords])
# A bill matching ANY keyword in the list gets a cycle_context row.

BILL_MATCH_RULES: list[dict] = [
    {
        "donor_sector": "Energy/Utilities",
        "event_type":   "legislation",
        "significance": "high",
        "keywords": [
            "offshore wind", "coastal virginia", "dominion energy",
            "appalachian power", "rate case", "rate increase",
            "grid transformation", "grid modernization",
            "data center", "electric utility", "state corporation commission",
            "clean economy", "renewable energy standard",
        ],
    },
    {
        "donor_sector": "Energy/Utilities",
        "event_type":   "legislation",
        "significance": "medium",
        "keywords": [
            "net metering", "solar energy", "energy storage",
            "natural gas", "fossil fuel", "carbon",
        ],
    },
    {
        "donor_sector": "Alcohol/Gambling",
        "event_type":   "legislation",
        "significance": "high",
        "keywords": [
            "casino", "gaming", "sports betting", "lottery",
            "cannabis", "marijuana", "hemp",
        ],
    },
    {
        "donor_sector": "Alcohol/Gambling",
        "event_type":   "legislation",
        "significance": "medium",
        "keywords": [
            "mixed beverage", "wine", "beer", "spirits",
            "abc board", "alcohol beverage",
        ],
    },
    {
        "donor_sector": "Healthcare",
        "event_type":   "legislation",
        "significance": "high",
        "keywords": [
            "medicaid", "certificate of public need", "copn",
            "behavioral health", "mental health",
            "prescription drug", "pharmacy benefit",
            "hospital", "health insurance",
        ],
    },
    {
        "donor_sector": "Finance",
        "event_type":   "legislation",
        "significance": "high",
        "keywords": [
            "payday loan", "consumer finance", "interest rate cap",
            "bank regulation", "insurance rate",
            "mortgage", "credit union",
        ],
    },
    {
        "donor_sector": "Agriculture",
        "event_type":   "legislation",
        "significance": "medium",
        "keywords": [
            "agricultural", "farm", "pesticide", "livestock",
            "right to farm", "food safety",
        ],
    },
    {
        "donor_sector": "Transportation",
        "event_type":   "legislation",
        "significance": "high",
        "keywords": [
            "toll", "transurban", "congestion pricing",
            "rideshare", "transportation network",
            "autonomous vehicle", "electric vehicle incentive",
        ],
    },
    {
        "donor_sector": "Housing",
        "event_type":   "legislation",
        "significance": "high",
        "keywords": [
            "zoning", "affordable housing", "rent control",
            "eviction", "landlord tenant",
            "short-term rental", "accessory dwelling",
        ],
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _insert_hardcoded(conn: sqlite3.Connection) -> int:
    count = 0
    for row in HARDCODED:
        try:
            conn.execute(
                """
                INSERT INTO cycle_context
                    (election_cycle, donor_sector, event_type, title,
                     description, bill_id, bill_number, significance, source)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(election_cycle, title) DO UPDATE SET
                    donor_sector = excluded.donor_sector,
                    event_type   = excluded.event_type,
                    description  = excluded.description,
                    bill_number  = excluded.bill_number,
                    significance = excluded.significance,
                    source       = excluded.source
                """,
                (
                    row["election_cycle"],
                    row.get("donor_sector"),
                    row["event_type"],
                    row["title"],
                    row.get("description"),
                    row.get("bill_id"),
                    row.get("bill_number"),
                    row.get("significance", "medium"),
                    "hardcoded",
                ),
            )
            count += 1
        except sqlite3.Error as e:
            print(f"  WARN hardcoded row '{row['title']}': {e}")
    conn.commit()
    return count


def _insert_bill_matched(
    conn: sqlite3.Connection, sessions: list[str]
) -> int:
    """Match bills from legiscan_va_bills against BILL_MATCH_RULES."""
    # Load bills for the requested sessions
    placeholders = ",".join("?" for _ in sessions)
    bills = conn.execute(
        f"""
        SELECT bill_id, session, bill_number, title, subject
        FROM legiscan_va_bills
        WHERE session IN ({placeholders})
          AND title IS NOT NULL
        """,
        sessions,
    ).fetchall()
    print(f"  Matching {len(bills):,} bills across sessions {sessions}")

    count = 0
    for bill_id, session, bill_num, title, subject in bills:
        search_text = ((title or "") + " " + (subject or "")).lower()

        for rule in BILL_MATCH_RULES:
            matched_kw = next(
                (kw for kw in rule["keywords"] if kw in search_text), None
            )
            if matched_kw is None:
                continue

            # Use the VA legislative cycle: session year is the election_cycle
            # VA elects in odd years; session years map directly to cycles
            cycle = session  # "2022", "2023", "2024", "2025", "2026"

            entry_title = f"{bill_num}: {title[:80]}" if title else bill_num

            try:
                conn.execute(
                    """
                    INSERT INTO cycle_context
                        (election_cycle, donor_sector, event_type, title,
                         description, bill_id, bill_number, significance, source)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(election_cycle, title) DO UPDATE SET
                        bill_id    = excluded.bill_id,
                        bill_number= excluded.bill_number,
                        source     = excluded.source
                    """,
                    (
                        cycle,
                        rule["donor_sector"],
                        rule["event_type"],
                        entry_title,
                        f"Matched keyword: '{matched_kw}'",
                        bill_id,
                        bill_num,
                        rule["significance"],
                        "bill_match",
                    ),
                )
                count += 1
            except sqlite3.Error:
                pass
            break  # only first matching rule per bill

    conn.commit()
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def main(bill_match_sessions: list[str] | None = None) -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)

    print("Inserting hardcoded context (2013-2021) …")
    n_hard = _insert_hardcoded(conn)
    print(f"  {n_hard} rows written")

    sessions = bill_match_sessions or ["2022", "2023", "2024", "2025", "2026"]
    print(f"Bill-matching for sessions {sessions} …")
    n_match = _insert_bill_matched(conn, sessions)
    print(f"  {n_match} rows written")

    total = conn.execute("SELECT COUNT(*) FROM cycle_context").fetchone()[0]
    print(f"\nTotal cycle_context rows: {total}")

    print("\nBreakdown by cycle + sector:")
    rows = conn.execute("""
        SELECT election_cycle, donor_sector, COUNT(*) as n,
               SUM(CASE WHEN significance='high' THEN 1 ELSE 0 END) as high_n
        FROM cycle_context
        GROUP BY election_cycle, donor_sector
        ORDER BY election_cycle, donor_sector
    """).fetchall()
    for r in rows:
        sector = r[1] or "(cross-sector)"
        print(f"  {r[0]}  {sector:<25}  {r[2]:3d} entries  ({r[3]} high-significance)")

    conn.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--cycles", nargs="*", default=None,
                   help="Sessions to bill-match (default: 2022-2026)")
    args = p.parse_args()
    main(args.cycles)
