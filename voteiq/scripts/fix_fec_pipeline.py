"""
voteiq/scripts/fix_fec_pipeline.py

Pulls FEC Schedule A individual contributions for all
Virginia delegation members and stores them in polls.db.

Uses cursor-based pagination (last_index + last_contribution_receipt_date)
for reliable large-dataset retrieval from the FEC API.
"""
import os
import sqlite3
import time

import requests

from voteiq.analysis.classify import classify_employer
from voteiq.config.finance_rules import FEDERAL_DB_PATH

FEC_API_KEY = os.getenv("FEC_API_KEY")

# bioguide_id → FEC candidate_id
VIRGINIA_CANDIDATES = {
    "W000804": "H6VA01054",  # Wittman     VA-01
    "K000400": "H2VA02184",  # Kiggans     VA-02
    "S000185": "H0VA03088",  # Scott       VA-03
    "M001220": "H2VA04246",  # McClellan   VA-04
    "G000591": "H0VA05094",  # Good        VA-05
    "C001118": "H8VA06094",  # Cline       VA-06
    "V000138": "H4VA07234",  # Vindman     VA-07
    "B001292": "H4VA08088",  # Beyer       VA-08
    "G000568": "H0VA09093",  # Griffith    VA-09
    "S001230": "H4VA10279",  # Subramanyam VA-10
    "W000831": "H6VA11066",  # Walkinshaw  VA-11
    "W000805": "S6VA00013",  # Warner      Senator
    "K000384": "S2VA00034",  # Kaine       Senator
}

_RETRIES = 3


# ── FEC FETCH ─────────────────────────────────────────────────────────────────

def fetch_schedule_a(candidate_id: str, cycle: int = 2024) -> list:
    """
    Pull all itemized individual contributions using cursor-based pagination.
    Falls back to next page on cursor error.
    """
    url         = "https://api.open.fec.gov/v1/schedules/schedule_a/"
    all_results = []
    last_index  = None
    last_date   = None

    while True:
        params = {
            "api_key":                     FEC_API_KEY,
            "candidate_id":                candidate_id,
            "two_year_transaction_period": cycle,
            "per_page":                    100,
            "sort":                        "-contribution_receipt_date",
            "is_individual":               True,
            "sort_hide_null":              True,
        }
        if last_index is not None:
            params["last_index"]                   = last_index
            params["last_contribution_receipt_date"] = last_date

        data = None
        for attempt in range(_RETRIES):
            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < _RETRIES - 1:
                    time.sleep(2 ** attempt)
                else:
                    print(f"  FEC error after {_RETRIES} attempts: {e}")

        if data is None:
            break

        results = data.get("results", [])
        if not results:
            break

        all_results.extend(results)

        pagination = data.get("pagination", {})
        last_indexes = pagination.get("last_indexes", {})
        last_index   = last_indexes.get("last_index")
        last_date    = last_indexes.get("last_contribution_receipt_date")

        if not last_index:
            break

        time.sleep(0.5)

    return all_results


# ── TABLE SETUP ───────────────────────────────────────────────────────────────

def _ensure_table(cur: sqlite3.Cursor):
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS fec_individual_contributions (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id               TEXT,
            candidate_id              TEXT,
            contributor_name          TEXT,
            contributor_name_key      TEXT,
            contributor_employer      TEXT,
            contributor_occupation    TEXT,
            employer_sector           TEXT,
            amount                    NUMERIC,
            contribution_receipt_date TEXT,
            contributor_city          TEXT,
            contributor_state         TEXT,
            contributor_zip           TEXT,
            cycle                     INTEGER,
            UNIQUE(
                bioguide_id,
                contributor_name_key,
                amount,
                contribution_receipt_date
            )
        );

        CREATE INDEX IF NOT EXISTS idx_fec_bioguide
            ON fec_individual_contributions(bioguide_id);

        CREATE INDEX IF NOT EXISTS idx_fec_cycle
            ON fec_individual_contributions(cycle);

        CREATE INDEX IF NOT EXISTS idx_fec_sector
            ON fec_individual_contributions(employer_sector);

        CREATE INDEX IF NOT EXISTS idx_fec_name_key
            ON fec_individual_contributions(contributor_name_key);

        CREATE INDEX IF NOT EXISTS idx_fec_date
            ON fec_individual_contributions(contribution_receipt_date);
    """)


# ── STORE ─────────────────────────────────────────────────────────────────────

def store_contributions(
    bioguide_id: str,
    candidate_id: str,
    contributions: list,
    cycle: int = 2024,
) -> int:
    conn = sqlite3.connect(FEDERAL_DB_PATH)
    cur  = conn.cursor()
    _ensure_table(cur)
    count = 0

    for c in contributions:
        name       = c.get("contributor_name", "") or ""
        employer   = c.get("contributor_employer",  "") or ""
        occupation = c.get("contributor_occupation", "") or ""
        name_key   = name.upper().strip()
        sector     = classify_employer(employer, occupation)

        try:
            cur.execute("""
                INSERT INTO fec_individual_contributions (
                    bioguide_id, candidate_id,
                    contributor_name, contributor_name_key,
                    contributor_employer, contributor_occupation,
                    employer_sector, amount,
                    contribution_receipt_date,
                    contributor_city, contributor_state,
                    contributor_zip, cycle
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(
                    bioguide_id,
                    contributor_name_key,
                    amount,
                    contribution_receipt_date
                ) DO UPDATE SET
                    employer_sector = excluded.employer_sector,
                    contributor_employer = excluded.contributor_employer
            """, (
                bioguide_id, candidate_id,
                name, name_key,
                employer, occupation,
                sector,
                c.get("contribution_receipt_amount", 0),
                c.get("contribution_receipt_date", ""),
                c.get("contributor_city", ""),
                c.get("contributor_state", ""),
                c.get("contributor_zip", ""),
                cycle,
            ))
            count += cur.rowcount
        except Exception as e:
            print(f"  Insert error: {e}")

    conn.commit()
    conn.close()
    return count


# ── PIPELINE ──────────────────────────────────────────────────────────────────

def run_full_fec_pipeline(cycle: int = 2024):
    if not FEC_API_KEY:
        print("ERROR: FEC_API_KEY environment variable not set")
        return

    print("Full FEC Pipeline — Virginia Delegation")
    print("=" * 40)

    total_new = 0

    for bioguide_id, candidate_id in VIRGINIA_CANDIDATES.items():
        print(f"\nFetching: {bioguide_id}  (FEC: {candidate_id})")
        contributions = fetch_schedule_a(candidate_id=candidate_id, cycle=cycle)
        print(f"  Retrieved {len(contributions):,} contributions")

        if not contributions:
            continue

        inserted = store_contributions(
            bioguide_id   = bioguide_id,
            candidate_id  = candidate_id,
            contributions = contributions,
            cycle         = cycle,
        )
        print(f"  Stored {inserted:,} new rows")
        total_new += inserted

    print()
    print(f"Done. {total_new:,} total new rows into polls.db")

    conn = sqlite3.connect(FEDERAL_DB_PATH)
    cur  = conn.cursor()
    cur.execute("""
        SELECT bioguide_id, COUNT(*) AS n
        FROM fec_individual_contributions
        WHERE cycle = ?
        GROUP BY bioguide_id ORDER BY n DESC
    """, (cycle,))
    print(f"\nCoverage (cycle={cycle}):")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]:,} contributions")
    conn.close()


if __name__ == "__main__":
    run_full_fec_pipeline(cycle=2024)
