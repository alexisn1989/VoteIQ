#!/usr/bin/env python3
"""
build_va_state_finance.py
Full Virginia SBE campaign finance pipeline.

Reads all finance_csv/<period>/ScheduleA.csv files,
classifies sectors, resolves contributor → legislator,
and writes:

  polls.db
    va_cf_schedule_a   — raw normalized contributions (all candidates)

  legislative_intelligence.db
    va_sbe_contributions — enriched, legislator-matched contributions
    donor_sector_totals  — aggregated by (legislator, sector, cycle)

Usage:
    python build_va_state_finance.py            # full rebuild
    python build_va_state_finance.py --since 2022  # only cycle >= 2022
    python build_va_state_finance.py --dry-run  # count rows, don't write
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    from rapidfuzz import process as rf_process, fuzz
    _RF = True
except ImportError:
    _RF = False

BASE_DIR    = Path(__file__).resolve().parent
DATA_DIR    = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
POLLS_DB    = DATA_DIR / "polls.db"
LEG_DB      = DATA_DIR / "legislative_intelligence.db"
FINANCE_DIR = DATA_DIR / "finance_csv"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BATCH_SIZE  = 25_000

# ── Sector keyword classifier ─────────────────────────────────────────────────

SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Healthcare",     ["physician", "doctor", "nurse", "hospital", "medical",
                        "dental", "health", "pharmacy", "surgeon", "dentist",
                        "therapist", "clinic", "psychiatr", "optom", "veterinar",
                        "chiropract", "radiol", "oncol"]),
    ("Legal",          ["attorney", "lawyer", "law firm", "counsel", "solicitor",
                        "paralegal", "esquire", "barrister", "litigation"]),
    ("Real Estate",    ["realtor", "real estate", "property", "developer",
                        "construction", "contractor", "builder", "architect",
                        "land", "realty", "housing", "mortgage", "title company",
                        "appraiser", "plumb", "hvac", "electri"]),
    ("Finance",        ["bank", "financial", "investment", "insurance", "lender",
                        "accountant", "cpa", "auditor", "broker", "fund",
                        "capital", "wealth", "credit union", "trading",
                        "securities", "financi"]),
    ("Technology",     ["software", "tech", "information technology", " it ",
                        "computer", "digital", "data", "cybersecurity",
                        "engineer", "developer", "programmer", "telecom",
                        "network", "cloud", "ai ", "saas"]),
    ("Energy",         ["oil", "gas", "energy", "electric", "utility", "power",
                        "solar", "wind", "coal", "petroleum", "pipeline",
                        "refin", "nuclear"]),
    ("Agriculture",    ["farm", "agricultur", "farmer", "livestock", "crop",
                        "dairy", "forestry", "timber", "rancher", "agronomist",
                        "horticultur"]),
    ("Education",      ["teacher", "professor", "school", "universit", "college",
                        "educat", "principal", "librarian", "tutor", "academic",
                        "faculty", "superintendent"]),
    ("Transportation", ["trucking", "logistics", "transport", "airline", "rail",
                        "shipping", "driver", "pilot", "fleet", "freight",
                        "maritime"]),
    ("Labor/Union",    ["union", "afl-cio", "seiu", "ufcw", "teamster", "uaw",
                        "ibew", "iatse", "craft", "guild", "labor council"]),
    ("Defense",        ["defense", "military", "veteran", "aerospace", "weapon",
                        "contractor dod", "army", "navy", "marine", "air force"]),
    ("Hospitality",    ["hotel", "restaurant", "hospitality", "bar ", "tavern",
                        "brewery", "winery", "catering", "tourism", "resort",
                        "motel"]),
    ("Manufacturing",  ["manufactur", "factory", "industrial", "plant",
                        "assembly", "production", "machinery", "fabricat",
                        "foundry", "mill"]),
    ("Retail",         ["retail", "store", "shop ", "merchant", "dealer",
                        "wholesale"]),
    ("Ideological",    ["pac", "political", "committee", "democrat", "republican",
                        "libertarian", "green party", "action fund", "victory",
                        "caucus", "leadership", "senate fund", "house fund"]),
    ("Individual/Other", []),  # catch-all — must be last
]


def classify_sector(occupation: str, employer: str, company: str = "") -> str:
    combined = f"{occupation} {employer} {company}".lower()
    for sector, keywords in SECTOR_KEYWORDS[:-1]:    # skip catch-all
        if any(k in combined for k in keywords):
            return sector
    return "Individual/Other"


# ── Virginia donor tier thresholds ───────────────────────────────────────────

VA_THRESHOLDS = {
    "mega_donor":          10_000,
    "major_institutional":  1_000,
    "mixed":                  200,
    "grassroots":               0,
}


def donor_tier(amount: float) -> str:
    if amount >= VA_THRESHOLDS["mega_donor"]:
        return "Mega Donor"
    if amount >= VA_THRESHOLDS["major_institutional"]:
        return "Major Institutional"
    if amount >= VA_THRESHOLDS["mixed"]:
        return "Large Donor"
    return "Grassroots"


# ── Date normalization ────────────────────────────────────────────────────────

_DATE_FMTS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d")


def to_iso_date(raw: str) -> str:
    """Convert any common date string to YYYY-MM-DD. Return '' on failure."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw     # leave as-is if nothing matched


# ── Name helpers ──────────────────────────────────────────────────────────────

_PREFIXES = re.compile(
    r"^(mr\.?|mrs\.?|ms\.?|dr\.?|hon\.?|rev\.?|judge|senator|delegate|del\.?)\s+",
    re.I,
)

def clean_candidate_name(raw: str) -> str:
    name = _PREFIXES.sub("", raw.strip())
    return re.sub(r"\s+", " ", name).strip()


def build_name_map(leg_conn: sqlite3.Connection) -> dict[str, str]:
    """Return {normalised_name: lis_id} for all legislators."""
    rows = leg_conn.execute("SELECT member_id, member_name FROM members").fetchall()
    return {clean_candidate_name(r[1]).lower(): r[0] for r in rows}


def fuzzy_resolve(cleaned: str,
                  name_map: dict[str, str],
                  names_list: list[str],
                  threshold: int = 78) -> str | None:
    if not _RF or not cleaned:
        return None
    result = rf_process.extractOne(
        cleaned, names_list,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result:
        return name_map.get(result[0])
    return None


# ── CSV reader ────────────────────────────────────────────────────────────────

def read_csv(path: Path):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with path.open(encoding=enc, errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                yield from reader
            return
        except UnicodeDecodeError:
            continue


# ── Schema setup ──────────────────────────────────────────────────────────────

def setup_polls_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS va_cf_schedule_a;
        CREATE TABLE va_cf_schedule_a (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            report_uid           TEXT,
            schedule_a_id        TEXT,
            committee_contact_id TEXT,
            first_name           TEXT,
            last_or_company      TEXT,
            employer             TEXT,
            occupation           TEXT,
            city                 TEXT,
            state_code           TEXT,
            zip_code             TEXT,
            is_individual        INTEGER,
            transaction_date     TEXT,
            amount               REAL,
            total_to_date        REAL,
            source_period        TEXT,
            candidate_name       TEXT,
            election_cycle       TEXT
        );
        CREATE INDEX idx_sca_report  ON va_cf_schedule_a(report_uid);
        CREATE INDEX idx_sca_cycle   ON va_cf_schedule_a(election_cycle);
        CREATE INDEX idx_sca_amount  ON va_cf_schedule_a(amount);
    """)
    conn.commit()


def setup_leg_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS va_sbe_contributions;
        CREATE TABLE va_sbe_contributions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            legislator_id    TEXT,
            candidate_name   TEXT,
            cycle            TEXT,
            contributor_name TEXT,
            employer         TEXT,
            occupation       TEXT,
            amount           REAL,
            transaction_date TEXT,
            city             TEXT,
            state            TEXT,
            is_individual    INTEGER,
            sector           TEXT,
            donor_tier       TEXT
        );
        CREATE INDEX idx_sbc_leg   ON va_sbe_contributions(legislator_id);
        CREATE INDEX idx_sbc_cycle ON va_sbe_contributions(cycle);
        CREATE INDEX idx_sbc_date  ON va_sbe_contributions(transaction_date);

        DROP TABLE IF EXISTS donor_sector_totals;
        CREATE TABLE donor_sector_totals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            legislator_id TEXT,
            sector        TEXT,
            total_amount  REAL,
            donor_count   INTEGER,
            cycle         TEXT,
            UNIQUE(legislator_id, sector, cycle)
        );
        CREATE INDEX idx_dst_leg ON donor_sector_totals(legislator_id);
    """)
    conn.commit()


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(since_cycle: int | None = None, dry_run: bool = False) -> None:
    polls  = sqlite3.connect(str(POLLS_DB),  timeout=30)
    leg    = sqlite3.connect(str(LEG_DB),    timeout=30)
    for c in (polls, leg):
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")

    if not dry_run:
        print("Setting up schemas …")
        setup_polls_db(polls)
        setup_leg_db(leg)

    # Build name resolution index
    name_map   = build_name_map(leg)
    names_list = list(name_map.keys())
    print(f"Loaded {len(name_map):,} legislator names for matching")

    # Candidate resolution cache (raw name → lis_id or None)
    cache: dict[str, str | None] = {}

    def resolve(raw: str) -> str | None:
        if raw in cache:
            return cache[raw]
        cleaned = clean_candidate_name(raw).lower()
        lis_id  = name_map.get(cleaned) or fuzzy_resolve(cleaned, name_map, names_list)
        cache[raw] = lis_id
        return lis_id

    periods = sorted(p.name for p in FINANCE_DIR.iterdir() if p.is_dir())
    print(f"Scanning {len(periods)} period folders …\n")

    raw_batch: list = []
    enr_batch: list = []
    total_raw = total_enr = 0
    skipped   = 0

    for period in periods:
        year_str = period[:4]
        try:
            year = int(year_str)
        except ValueError:
            continue
        if since_cycle and year < since_cycle:
            continue

        report_path = FINANCE_DIR / period / "Report.csv"
        sched_path  = FINANCE_DIR / period / "ScheduleA.csv"
        if not report_path.exists() or not sched_path.exists():
            continue

        # Build ReportUID → (lis_id, candidate_name, cycle) for this period
        report_map: dict[str, tuple[str | None, str, str]] = {}
        for row in read_csv(report_path):
            uid   = (row.get("ReportUID") or "").strip()
            cname = (row.get("CandidateName") or "").strip()
            ryear = (row.get("ReportYear") or year_str).strip() or year_str
            if uid and cname:
                report_map[uid] = (resolve(cname), cname, ryear)

        if not report_map:
            continue

        period_raw = period_enr = 0

        for row in read_csv(sched_path):
            uid = (row.get("ReportUID") or "").strip()
            if uid not in report_map:
                skipped += 1
                continue

            # Parse amount
            try:
                amount = float(
                    (row.get("Amount") or "0")
                    .replace("$", "").replace(",", "").strip()
                )
            except ValueError:
                continue
            if amount <= 0:
                continue

            lis_id, cand_name, cycle = report_map[uid]

            is_individual = (
                row.get("IsIndividual", "True").strip().lower() == "true"
            )
            employer      = (row.get("NameOfEmployer") or "").strip()
            occupation    = (row.get("OccupationOrTypeOfBusiness") or "").strip()
            first         = (row.get("FirstName") or "").strip()
            last_company  = (row.get("LastOrCompanyName") or "").strip()
            contrib_name  = (
                f"{first} {last_company}".strip() if is_individual
                else last_company
            )
            city          = (row.get("City") or "").strip()
            state_code    = (row.get("StateCode") or "").strip()
            zip_code      = (row.get("ZipCode") or "").strip()
            raw_date      = (row.get("TransactionDate") or "").strip()
            iso_date      = to_iso_date(raw_date)
            sched_id      = (row.get("ScheduleAId") or "").strip()
            cc_id         = (row.get("CommitteeContactId") or "").strip()
            total_to_date = float(
                (row.get("TotalToDate") or "0")
                .replace("$", "").replace(",", "").strip() or "0"
            )

            # Raw row → polls.db
            raw_batch.append((
                uid, sched_id, cc_id,
                first, last_company,
                employer, occupation,
                city, state_code, zip_code,
                1 if is_individual else 0,
                iso_date, amount, total_to_date,
                period, cand_name, cycle,
            ))
            period_raw += 1

            # Enriched row → legislative_intelligence.db (legislator-matched only)
            if lis_id:
                sector = classify_sector(
                    occupation, employer,
                    last_company if not is_individual else "",
                )
                tier   = donor_tier(amount)
                enr_batch.append((
                    lis_id, cand_name, cycle,
                    contrib_name, employer, occupation,
                    amount, iso_date,
                    city, state_code,
                    1 if is_individual else 0,
                    sector, tier,
                ))
                period_enr += 1

            # Flush batches
            if len(raw_batch) >= BATCH_SIZE:
                if not dry_run:
                    polls.executemany(
                        """INSERT INTO va_cf_schedule_a
                           (report_uid, schedule_a_id, committee_contact_id,
                            first_name, last_or_company, employer, occupation,
                            city, state_code, zip_code, is_individual,
                            transaction_date, amount, total_to_date,
                            source_period, candidate_name, election_cycle)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        raw_batch,
                    )
                    polls.commit()
                total_raw += len(raw_batch)
                raw_batch = []

            if len(enr_batch) >= BATCH_SIZE:
                if not dry_run:
                    leg.executemany(
                        """INSERT INTO va_sbe_contributions
                           (legislator_id, candidate_name, cycle,
                            contributor_name, employer, occupation,
                            amount, transaction_date, city, state,
                            is_individual, sector, donor_tier)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        enr_batch,
                    )
                    leg.commit()
                total_enr += len(enr_batch)
                enr_batch = []

        if period_raw:
            print(f"  {period}: {period_raw:>6,} raw  {period_enr:>6,} matched")

    # Flush remainders
    if raw_batch:
        if not dry_run:
            polls.executemany(
                """INSERT INTO va_cf_schedule_a
                   (report_uid, schedule_a_id, committee_contact_id,
                    first_name, last_or_company, employer, occupation,
                    city, state_code, zip_code, is_individual,
                    transaction_date, amount, total_to_date,
                    source_period, candidate_name, election_cycle)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                raw_batch,
            )
            polls.commit()
        total_raw += len(raw_batch)

    if enr_batch:
        if not dry_run:
            leg.executemany(
                """INSERT INTO va_sbe_contributions
                   (legislator_id, candidate_name, cycle,
                    contributor_name, employer, occupation,
                    amount, transaction_date, city, state,
                    is_individual, sector, donor_tier)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                enr_batch,
            )
            leg.commit()
        total_enr += len(enr_batch)

    print(f"\nva_cf_schedule_a:     {total_raw:>10,} rows  (polls.db)")
    print(f"va_sbe_contributions:  {total_enr:>10,} rows  (legislative_intelligence.db)")
    print(f"Skipped (no report):  {skipped:>10,} rows")

    # Aggregate donor_sector_totals
    if not dry_run:
        print("\nAggregating donor_sector_totals …")
        leg.execute("""
            INSERT INTO donor_sector_totals
                (legislator_id, sector, total_amount, donor_count, cycle)
            SELECT
                legislator_id,
                sector,
                ROUND(SUM(amount), 2),
                COUNT(*),
                cycle
            FROM va_sbe_contributions
            GROUP BY legislator_id, sector, cycle
            ON CONFLICT(legislator_id, sector, cycle)
            DO UPDATE SET
                total_amount = excluded.total_amount,
                donor_count  = excluded.donor_count
        """)
        leg.commit()
        dst_count = leg.execute("SELECT COUNT(*) FROM donor_sector_totals").fetchone()[0]
        print(f"donor_sector_totals:   {dst_count:>10,} rows  (legislative_intelligence.db)")

    polls.close()
    leg.close()
    print("\nDone.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Virginia state finance pipeline")
    parser.add_argument(
        "--since", type=int, default=None,
        help="Only process contribution cycles >= this year (e.g. --since 2022)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count rows without writing to any database",
    )
    args = parser.parse_args()
    run(since_cycle=args.since, dry_run=args.dry_run)
