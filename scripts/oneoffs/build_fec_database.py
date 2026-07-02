#!/usr/bin/env python3
"""
Build FEC campaign finance database from downloaded JSON files.

Creates two databases:
  polls.db
    fec_candidates         — candidate info (name, office, state, cycle)
    fec_contributions      — individual contributions (Schedule A)
    fec_financial_summary  — candidate totals (raised, spent, cash on hand)

  legislative_intelligence.db
    federal_contributions  — contributions matched to elected officials
    federal_donor_sectors  — contributions aggregated by sector

Usage:
    python build_fec_database.py
    python build_fec_database.py --candidate "Spanberger"
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

FEC_DATA_DIR = Path("fec_data")
POLLS_DB = Path("polls.db")
LEG_DB = Path("legislative_intelligence.db")

# Sector keyword classifier for federal contributions
SECTOR_KEYWORDS = [
    ("Finance", ["bank", "investment", "insurance", "financial", "hedge fund"]),
    ("Technology", ["software", "tech", "it", "computer", "data", "ai"]),
    ("Healthcare", ["hospital", "medical", "health", "pharma", "doctor"]),
    ("Energy", ["oil", "gas", "energy", "utility", "power", "solar"]),
    ("Real Estate", ["real estate", "property", "developer", "construction"]),
    ("Legal", ["attorney", "lawyer", "law firm"]),
    ("Consulting", ["consultant", "consulting", "strategic"]),
    ("Manufacturing", ["manufacturing", "industrial", "factory"]),
    ("Retail", ["retail", "store", "commerce"]),
    ("Defense", ["defense", "military", "aerospace"]),
    ("Agriculture", ["agricultural", "farm", "food"]),
    ("Labor/Union", ["union", "labor", "afl-cio"]),
    ("Ideological", ["pac", "political", "committee", "super pac"]),
    ("Individual/Other", []),
]


def classify_sector(employer: str, occupation: str) -> str:
    """Classify contribution sector based on employer and occupation"""
    combined = f"{employer} {occupation}".lower()
    for sector, keywords in SECTOR_KEYWORDS[:-1]:
        if any(k in combined for k in keywords):
            return sector
    return "Individual/Other"


def donor_tier(amount: float) -> str:
    """Classify donor based on contribution amount"""
    if amount >= 10000:
        return "Mega Donor"
    elif amount >= 2700:
        return "Major Donor"
    elif amount >= 500:
        return "Substantial Donor"
    else:
        return "Grassroots"


def setup_polls_db():
    """Create FEC tables in polls.db"""
    conn = sqlite3.connect(str(POLLS_DB))
    conn.executescript("""
        DROP TABLE IF EXISTS fec_candidates;
        CREATE TABLE fec_candidates (
            candidate_id        TEXT PRIMARY KEY,
            candidate_name      TEXT NOT NULL,
            office_sought       TEXT,
            state               TEXT,
            district            TEXT,
            party_affiliation   TEXT,
            election_cycle      INTEGER,
            first_file_date     TEXT,
            last_file_date      TEXT
        );

        DROP TABLE IF EXISTS fec_contributions;
        CREATE TABLE fec_contributions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id        TEXT NOT NULL,
            contributor_name    TEXT,
            employer            TEXT,
            occupation          TEXT,
            amount              REAL,
            contribution_date   TEXT,
            receipt_date        TEXT,
            election_cycle      INTEGER,
            contribution_type   TEXT,
            FOREIGN KEY (candidate_id) REFERENCES fec_candidates(candidate_id)
        );
        CREATE INDEX idx_fec_contrib_cand ON fec_contributions(candidate_id);
        CREATE INDEX idx_fec_contrib_cycle ON fec_contributions(election_cycle);
        CREATE INDEX idx_fec_contrib_amount ON fec_contributions(amount);

        DROP TABLE IF EXISTS fec_financial_summary;
        CREATE TABLE fec_financial_summary (
            candidate_id        TEXT PRIMARY KEY,
            election_cycle      INTEGER,
            total_receipts      REAL,
            total_disbursements REAL,
            cash_on_hand        REAL,
            debt_owed           REAL,
            individual_contrib  REAL,
            committee_contrib   REAL,
            candidate_contrib   REAL,
            last_updated        TEXT,
            FOREIGN KEY (candidate_id) REFERENCES fec_candidates(candidate_id)
        );
        CREATE INDEX idx_fec_summary_cand ON fec_financial_summary(candidate_id);
    """)
    conn.commit()
    conn.close()


def setup_leg_db():
    """Create federal contribution tables in legislative_intelligence.db"""
    conn = sqlite3.connect(str(LEG_DB))
    conn.executescript("""
        DROP TABLE IF EXISTS federal_contributions;
        CREATE TABLE federal_contributions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id        TEXT,
            candidate_name      TEXT,
            office              TEXT,
            state               TEXT,
            contributor_name    TEXT,
            employer            TEXT,
            occupation          TEXT,
            amount              REAL,
            contribution_date   TEXT,
            election_cycle      INTEGER,
            sector              TEXT,
            donor_tier          TEXT
        );
        CREATE INDEX idx_fed_contrib_cand ON federal_contributions(candidate_id);
        CREATE INDEX idx_fed_contrib_sector ON federal_contributions(sector);

        DROP TABLE IF EXISTS federal_donor_sectors;
        CREATE TABLE federal_donor_sectors (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id        TEXT,
            candidate_name      TEXT,
            sector              TEXT,
            total_amount        REAL,
            donor_count         INTEGER,
            election_cycle      INTEGER,
            UNIQUE(candidate_id, sector, election_cycle)
        );
        CREATE INDEX idx_fed_sector_cand ON federal_donor_sectors(candidate_id);
    """)
    conn.commit()
    conn.close()


def load_candidate_data(candidate_dir: Path) -> dict:
    """Load candidate info from JSON"""
    candidate_file = candidate_dir / "candidate.json"
    if not candidate_file.exists():
        return None

    with open(candidate_file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_contributions(candidate_dir: Path) -> list:
    """Load contributions from JSONL"""
    contrib_file = candidate_dir / "contributions.jsonl"
    if not contrib_file.exists():
        return []

    contributions = []
    with open(contrib_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    contributions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return contributions


def load_summary(candidate_dir: Path) -> dict:
    """Load financial summary from JSON"""
    summary_file = candidate_dir / "summary.json"
    if not summary_file.exists():
        return {}

    with open(summary_file, "r", encoding="utf-8") as f:
        return json.load(f)


def build_fec_database(candidate_name: str = None):
    """Build FEC databases from downloaded JSON files"""
    print(f"\n{'='*70}")
    print("Building FEC Campaign Finance Database")
    print(f"{'='*70}\n")

    if not FEC_DATA_DIR.exists():
        print(f"[ERROR] {FEC_DATA_DIR} not found. Run download_fec_data.py first.")
        return False

    setup_polls_db()
    setup_leg_db()

    polls_conn = sqlite3.connect(str(POLLS_DB))
    leg_conn = sqlite3.connect(str(LEG_DB))

    total_candidates = 0
    total_contributions = 0
    sector_totals = defaultdict(float)

    # Iterate through candidate directories
    for candidate_dir in sorted(FEC_DATA_DIR.iterdir()):
        if not candidate_dir.is_dir():
            continue

        candidate = load_candidate_data(candidate_dir)
        if not candidate:
            continue

        candidate_id = candidate.get("candidate_id")
        candidate_name_val = candidate.get("name", "Unknown")

        # Filter by name if specified
        if candidate_name and candidate_name.lower() not in candidate_name_val.lower():
            continue

        print(f"\nProcessing: {candidate_name_val} ({candidate_id})")

        # Extract candidate info
        office = candidate.get("office_full", "Unknown")
        state = candidate.get("state", "")
        party = candidate.get("party_full", "")

        # Insert into candidates table
        polls_conn.execute(
            """
            INSERT OR REPLACE INTO fec_candidates
            (candidate_id, candidate_name, office_sought, state, party_affiliation)
            VALUES (?, ?, ?, ?, ?)
            """,
            (candidate_id, candidate_name_val, office, state, party),
        )
        polls_conn.commit()
        total_candidates += 1

        # Load and process contributions
        contributions = load_contributions(candidate_dir)
        print(f"  Contributions: {len(contributions)}")

        contrib_batch = []
        fed_batch = []
        sector_batch = defaultdict(lambda: {"amount": 0, "count": 0})

        for contrib in contributions:
            # Parse contribution data
            amount = float(contrib.get("contribution_receipt_amount", 0) or 0)
            if amount <= 0:
                continue

            contributor = contrib.get("contributor_name", "Unknown")
            employer = contrib.get("employer", "")
            occupation = contrib.get("occupation", "")
            contrib_date = contrib.get("contribution_receipt_date", "")

            # Get election cycle from contribution
            cycle = contrib.get("election_cycle", 2024)

            # Insert into polls.db
            contrib_batch.append(
                (
                    candidate_id,
                    contributor,
                    employer,
                    occupation,
                    amount,
                    contrib_date,
                    None,
                    cycle,
                    "individual",
                )
            )

            # Insert into legislative_intelligence.db with sector
            sector = classify_sector(employer, occupation)
            tier = donor_tier(amount)

            fed_batch.append(
                (
                    candidate_id,
                    candidate_name_val,
                    office,
                    state,
                    contributor,
                    employer,
                    occupation,
                    amount,
                    contrib_date,
                    cycle,
                    sector,
                    tier,
                )
            )

            # Track sector totals
            sector_batch[sector]["amount"] += amount
            sector_batch[sector]["count"] += 1
            sector_totals[sector] += amount

            total_contributions += 1

        # Batch insert contributions
        if contrib_batch:
            polls_conn.executemany(
                """
                INSERT INTO fec_contributions
                (candidate_id, contributor_name, employer, occupation,
                 amount, contribution_date, receipt_date, election_cycle, contribution_type)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                contrib_batch,
            )
            polls_conn.commit()

        if fed_batch:
            leg_conn.executemany(
                """
                INSERT INTO federal_contributions
                (candidate_id, candidate_name, office, state,
                 contributor_name, employer, occupation, amount, contribution_date,
                 election_cycle, sector, donor_tier)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                fed_batch,
            )
            leg_conn.commit()

        # Insert sector summaries
        for sector, data in sector_batch.items():
            leg_conn.execute(
                """
                INSERT INTO federal_donor_sectors
                (candidate_id, candidate_name, sector, total_amount, donor_count, election_cycle)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    candidate_name_val,
                    sector,
                    data["amount"],
                    data["count"],
                    cycle,
                ),
            )
            leg_conn.commit()

        # Load and insert financial summary
        summary = load_summary(candidate_dir)
        if summary:
            polls_conn.execute(
                """
                INSERT OR REPLACE INTO fec_financial_summary
                (candidate_id, election_cycle, total_receipts, total_disbursements,
                 cash_on_hand, debt_owed, individual_contrib, committee_contrib, candidate_contrib)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    cycle,
                    summary.get("total_receipts", 0),
                    summary.get("total_disbursements", 0),
                    summary.get("cash_on_hand", 0),
                    summary.get("debt_owed", 0),
                    summary.get("individual_contrib", 0),
                    summary.get("committee_contrib", 0),
                    summary.get("candidate_contrib", 0),
                ),
            )
            polls_conn.commit()

    polls_conn.close()
    leg_conn.close()

    print(f"\n{'='*70}")
    print(f"Database Build Complete!")
    print(f"  Candidates: {total_candidates}")
    print(f"  Contributions: {total_contributions}")
    print(f"  Top Sectors: {sorted(sector_totals.items(), key=lambda x: x[1], reverse=True)[:5]}")
    print(f"{'='*70}\n")

    return True


def main():
    parser = argparse.ArgumentParser(description="Build FEC campaign finance database")
    parser.add_argument("--candidate", type=str, help="Filter by candidate name")
    args = parser.parse_args()

    build_fec_database(args.candidate)


if __name__ == "__main__":
    main()
