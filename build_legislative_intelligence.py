#!/usr/bin/env python3
"""
Build the legislative intelligence schema in polls.db.

Creates and populates:
  legislators          — from openstates_va.db + virginia_legislature.db (LIS IDs)
  va_bills             — from openstates_va.db
  va_votes             — from openstates_va.db (voter_name → lis_id)
  donor_sector_totals  — from finance_csv/ ScheduleA.csv files
  legislative_intelligence VIEW

Usage:
    python build_legislative_intelligence.py              # full build
    python build_legislative_intelligence.py --step legs  # only legislators
    python build_legislative_intelligence.py --step bills
    python build_legislative_intelligence.py --step votes
    python build_legislative_intelligence.py --step donors
    python build_legislative_intelligence.py --step view
    python build_legislative_intelligence.py --since 2020  # donors: only cycle >= 2020
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

try:
    from rapidfuzz import process as rf_process, fuzz
    _RF = True
except ImportError:
    _RF = False

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _data_or_base(filename: str) -> Path:
    data_path = DATA_DIR / filename
    return data_path if data_path.exists() else BASE_DIR / filename


POLLS_DB     = DATA_DIR / "polls.db"
LEG_DB       = DATA_DIR / "legislative_intelligence.db"   # separate file — never conflicts with app
OPENSTATES   = _data_or_base("openstates_va.db")
VA_LEG       = _data_or_base("virginia_legislature.db")
FINANCE_DIR  = DATA_DIR / "finance_csv" if (DATA_DIR / "finance_csv").exists() else BASE_DIR / "finance_csv"

CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

# ── Sector keyword classifier ─────────────────────────────────────────────────

SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Healthcare",    ["physician", "doctor", "nurse", "hospital", "medical", "dental",
                       "health", "pharmacy", "surgeon", "dentist", "therapist", "clinic",
                       "psychiatr", "optom", "veterinar", "chiropract", "radiol", "oncol"]),
    ("Legal",         ["attorney", "lawyer", "law firm", "counsel", "solicitor",
                       "paralegal", "esquire", "barrister", "litigation"]),
    ("Real Estate",   ["realtor", "real estate", "property", "developer", "construction",
                       "contractor", "builder", "architect", "land", "realty", "housing",
                       "mortgage", "title company", "appraiser", "plumb", "hvac", "electri"]),
    ("Finance",       ["bank", "financial", "investment", "insurance", "lender",
                       "accountant", "cpa", "auditor", "broker", "fund", "capital",
                       "wealth", "credit union", "trading", "securities", "financi"]),
    ("Technology",    ["software", "tech", "information technology", " it ", "computer",
                       "digital", "data", "cybersecurity", "engineer", "developer",
                       "programmer", "telecom", "network", "cloud", "ai ", "saas"]),
    ("Energy",        ["oil", "gas", "energy", "electric", "utility", "power", "solar",
                       "wind", "coal", "petroleum", "pipeline", "refin", "nuclear"]),
    ("Agriculture",   ["farm", "agricultur", "farmer", "livestock", "crop", "dairy",
                       "forestry", "timber", "rancher", "agronomist", "horticultur"]),
    ("Education",     ["teacher", "professor", "school", "universit", "college",
                       "educat", "principal", "librarian", "tutor", "academic",
                       "faculty", "superintendent"]),
    ("Transportation",["trucking", "logistics", "transport", "airline", "rail",
                       "shipping", "driver", "pilot", "fleet", "freight", "maritime"]),
    ("Labor/Union",   ["union", "afl-cio", "seiu", "ufcw", "teamster", "uaw",
                       "ibew", "iatse", "craft", "guild", "labor council"]),
    ("Defense",       ["defense", "military", "veteran", "aerospace", "weapon",
                       "contractor dod", "army", "navy", "marine", "air force"]),
    ("Hospitality",   ["hotel", "restaurant", "hospitality", "bar ", "tavern",
                       "brewery", "winery", "catering", "tourism", "resort", "motel"]),
    ("Manufacturing", ["manufactur", "factory", "industrial", "plant", "assembly",
                       "production", "machinery", "fabricat", "foundry", "mill"]),
    ("Retail",        ["retail", "store", "shop ", "merchant", "dealer", "wholesale",
                       "distributor", "sales rep", "grocery", "supermarket"]),
    ("Agriculture",   ["farm", "agri", "rural"]),
    ("Ideological",   ["pac", "political committee", "political action", "advocacy",
                       "association", "federation", "alliance", "coalition", "campaign",
                       "party committee", "democratic", "republican", "libertarian"]),
]

def classify_sector(occupation: str, employer: str, company: str) -> str:
    combined = " ".join([occupation, employer, company]).lower()
    for sector, keywords in SECTOR_KEYWORDS:
        for kw in keywords:
            if kw in combined:
                return sector
    if not occupation and not employer:
        return "Individual/Other"
    return "Individual/Other"


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_csv(path: Path) -> list[dict]:
    for enc in CSV_ENCODINGS:
        try:
            with path.open("r", encoding=enc, newline="") as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    return []


def fuzzy_match(name: str, choices: list[str], threshold: int = 80) -> str | None:
    if not _RF or not choices:
        return None
    result = rf_process.extractOne(name, choices, scorer=fuzz.token_sort_ratio)
    if result and result[1] >= threshold:
        return result[0]
    return None


def clean_candidate_name(raw: str) -> str:
    """Strip titles/prefixes and normalize spacing."""
    raw = re.sub(r"^(Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Hon\.?)\s+", "", raw.strip(), flags=re.I)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


# ── Step 1: Legislators ───────────────────────────────────────────────────────

def build_legislators(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS legislators")
    conn.execute("""
        CREATE TABLE legislators (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            lis_id          TEXT UNIQUE,
            name            TEXT,
            party           TEXT,
            chamber         TEXT,
            district        TEXT,
            active          INTEGER DEFAULT 1
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leg_name ON legislators(name)")

    os_conn = sqlite3.connect(OPENSTATES)
    va_conn = sqlite3.connect(VA_LEG)

    # Load openstates legislators (full name, party, chamber, district)
    os_legs = os_conn.execute(
        "SELECT ocd_id, name, party, chamber, district FROM legislators"
    ).fetchall()

    # Load virginia_legislature members (member_id = LIS ID, last name only)
    va_members = va_conn.execute("SELECT member_id, member_name FROM members").fetchall()
    # Build last-name → lis_id map (handle collisions by keeping all)
    lastname_to_lis: dict[str, list[str]] = defaultdict(list)
    for mid, mname in va_members:
        lastname_to_lis[mname.strip().lower()].append(mid)

    rows = []
    for ocd_id, name, party, chamber, district in os_legs:
        # Try to find LIS ID by last name
        last = name.strip().split()[-1].lower() if name.strip() else ""
        lis_candidates = lastname_to_lis.get(last, [])
        if len(lis_candidates) == 1:
            lis_id = lis_candidates[0]
        elif len(lis_candidates) > 1:
            # Ambiguous — pick the one matching chamber prefix
            prefix = "H" if "House" in (chamber or "") else "S" if "Senate" in (chamber or "") else ""
            matched = [l for l in lis_candidates if l.startswith(prefix)]
            lis_id = matched[0] if len(matched) == 1 else lis_candidates[0]
        else:
            lis_id = ocd_id  # fallback: use ocd_id as lis_id

        rows.append((lis_id, name, party, chamber, district))

    conn.executemany(
        "INSERT OR IGNORE INTO legislators (lis_id, name, party, chamber, district) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    os_conn.close()
    va_conn.close()
    print(f"  legislators: {len(rows)} rows")


# ── Step 2: Bills ─────────────────────────────────────────────────────────────

def build_bills(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS va_bills")
    conn.execute("""
        CREATE TABLE va_bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number     TEXT,
            session         TEXT,
            title           TEXT,
            description     TEXT,
            introduced_by   TEXT,
            introduced_date TEXT,
            status          TEXT,
            subjects        TEXT,
            UNIQUE(bill_number, session)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bills_sponsor ON va_bills(introduced_by)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bills_session ON va_bills(session)")

    os_conn = sqlite3.connect(OPENSTATES)

    # Load openstates bill descriptions for the description field
    desc_map: dict[tuple, str] = {}
    for row in os_conn.execute(
        "SELECT bill_id, session, description FROM bill_descriptions"
    ).fetchall():
        desc_map[(row[0], row[1])] = row[2]

    # Build name→lis_id lookup from legislators we just created
    name_to_lis: dict[str, str] = {}
    for row in conn.execute("SELECT lis_id, name FROM legislators").fetchall():
        name_to_lis[row[1].lower().strip()] = row[0]
    leg_names = list(name_to_lis.keys())

    bills_rows = os_conn.execute(
        "SELECT bill_id, session, title, subjects, sponsors, latest_action, latest_date FROM bills"
    ).fetchall()

    inserted = 0
    for bill_id, session, title, subjects, sponsors, latest_action, latest_date in bills_rows:
        # Find primary sponsor → lis_id
        primary_sponsor = (sponsors or "").split(",")[0].strip()
        lis_id = name_to_lis.get(primary_sponsor.lower())
        if not lis_id and primary_sponsor and _RF:
            matched = fuzzy_match(primary_sponsor.lower(), leg_names, threshold=82)
            if matched:
                lis_id = name_to_lis[matched]

        description = desc_map.get((bill_id, session), "")
        conn.execute(
            """INSERT OR IGNORE INTO va_bills
               (bill_number, session, title, description, introduced_by, introduced_date, status, subjects)
               VALUES (?,?,?,?,?,?,?,?)""",
            (bill_id, session, title, description, lis_id, latest_date, latest_action, subjects or ""),
        )
        inserted += 1

    conn.commit()
    os_conn.close()
    print(f"  va_bills: {inserted} rows")


# ── Step 3: Votes ─────────────────────────────────────────────────────────────

def build_votes(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS va_votes")
    conn.execute("""
        CREATE TABLE va_votes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number     TEXT,
            session         TEXT,
            legislator_id   TEXT,
            vote            TEXT,
            vote_date       TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_leg ON va_votes(legislator_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_bill ON va_votes(bill_number, session)")

    os_conn = sqlite3.connect(OPENSTATES)

    # Name → lis_id map
    name_to_lis: dict[str, str] = {}
    for row in conn.execute("SELECT lis_id, name FROM legislators").fetchall():
        name_to_lis[row[1].lower().strip()] = row[0]

    # Build a cache: voter_name → lis_id (avoid repeated fuzzy lookups)
    voter_cache: dict[str, str | None] = {}
    leg_names = list(name_to_lis.keys())

    def resolve_voter(voter_name: str) -> str | None:
        if voter_name in voter_cache:
            return voter_cache[voter_name]
        key = voter_name.lower().strip()
        lis = name_to_lis.get(key)
        if not lis and _RF:
            matched = fuzzy_match(key, leg_names, threshold=82)
            lis = name_to_lis[matched] if matched else None
        voter_cache[voter_name] = lis
        return lis

    # Normalize openstates vote result → YES/NO/ABSTAIN
    def norm_vote(v: str) -> str:
        v = (v or "").lower()
        if v in ("yes", "yea", "pass", "aye", "y"):
            return "YES"
        if v in ("no", "nay", "fail", "n"):
            return "NO"
        return "ABSTAIN"

    batch, batch_size = [], 50_000
    total = 0

    for row in os_conn.execute(
        "SELECT bill_id, session, voter_name, result, vote_date FROM votes"
    ):
        bill_id, session, voter_name, result, vote_date = row
        lis_id = resolve_voter(voter_name)
        if not lis_id:
            continue
        batch.append((bill_id, session, lis_id, norm_vote(result), vote_date or ""))
        if len(batch) >= batch_size:
            conn.executemany(
                "INSERT INTO va_votes (bill_number, session, legislator_id, vote, vote_date) VALUES (?,?,?,?,?)",
                batch,
            )
            conn.commit()
            total += len(batch)
            batch = []
            print(f"    votes written: {total:,}")

    if batch:
        conn.executemany(
            "INSERT INTO va_votes (bill_number, session, legislator_id, vote, vote_date) VALUES (?,?,?,?,?)",
            batch,
        )
        conn.commit()
        total += len(batch)

    os_conn.close()
    print(f"  va_votes: {total:,} rows")


# ── Step 4: Donor sector totals ───────────────────────────────────────────────

def build_donor_sectors(conn: sqlite3.Connection, since_cycle: int | None) -> None:
    conn.execute("DROP TABLE IF EXISTS donor_sector_totals")
    conn.execute("""
        CREATE TABLE donor_sector_totals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            legislator_id   TEXT,
            sector          TEXT,
            total_amount    REAL,
            donor_count     INTEGER,
            cycle           TEXT,
            UNIQUE(legislator_id, sector, cycle)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dst_leg ON donor_sector_totals(legislator_id)")

    # Build candidate name → lis_id map (for matching finance reports)
    name_to_lis: dict[str, str] = {}
    for row in conn.execute("SELECT lis_id, name FROM legislators").fetchall():
        name_to_lis[row[1].lower().strip()] = row[0]
    leg_names = list(name_to_lis.keys())

    # Cache: raw_candidate_name → lis_id
    candidate_cache: dict[str, str | None] = {}

    def resolve_candidate(raw: str) -> str | None:
        if raw in candidate_cache:
            return candidate_cache[raw]
        cleaned = clean_candidate_name(raw).lower()
        lis = name_to_lis.get(cleaned)
        if not lis and _RF and cleaned:
            matched = fuzzy_match(cleaned, leg_names, threshold=78)
            lis = name_to_lis[matched] if matched else None
        candidate_cache[raw] = lis
        return lis

    # Aggregate: (lis_id, sector, cycle) → {total, count}
    totals: dict[tuple[str, str, str], list] = defaultdict(lambda: [0.0, 0])

    periods = sorted(p.name for p in FINANCE_DIR.iterdir() if p.is_dir())

    for period in periods:
        report_path = FINANCE_DIR / period / "Report.csv"
        sched_a_path = FINANCE_DIR / period / "ScheduleA.csv"
        if not report_path.exists() or not sched_a_path.exists():
            continue

        # Extract cycle from period name (year part)
        year_match = re.match(r"^(\d{4})", period)
        cycle = year_match.group(1) if year_match else period[:4]
        if since_cycle and int(cycle) < since_cycle:
            continue

        # Build report_uid → (lis_id, cycle) map
        report_map: dict[str, tuple[str, str]] = {}
        for row in read_csv(report_path):
            candidate_name = (row.get("CandidateName") or "").strip()
            report_uid = (row.get("ReportUID") or "").strip()
            report_year = (row.get("ReportYear") or cycle).strip() or cycle
            if not candidate_name or not report_uid:
                continue
            lis_id = resolve_candidate(candidate_name)
            if lis_id:
                report_map[report_uid] = (lis_id, report_year)

        if not report_map:
            continue

        # Process ScheduleA contributions
        for row in read_csv(sched_a_path):
            report_uid = (row.get("ReportUID") or "").strip()
            if report_uid not in report_map:
                continue
            is_individual = (row.get("IsIndividual") or "True").strip().lower() == "true"
            amount_str = (row.get("Amount") or "0").replace("$", "").replace(",", "").strip()
            try:
                amount = float(amount_str)
            except ValueError:
                continue
            if amount <= 0:
                continue

            lis_id, report_year = report_map[report_uid]
            occupation = row.get("OccupationOrTypeOfBusiness", "") or ""
            employer   = row.get("NameOfEmployer", "") or ""
            company    = row.get("LastOrCompanyName", "") if not is_individual else ""

            sector = classify_sector(occupation, employer, company)
            key = (lis_id, sector, report_year)
            totals[key][0] += amount
            totals[key][1] += 1

    # Write aggregated rows
    rows = [
        (lis_id, sector, round(amt, 2), cnt, cycle)
        for (lis_id, sector, cycle), (amt, cnt) in totals.items()
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO donor_sector_totals
           (legislator_id, sector, total_amount, donor_count, cycle)
           VALUES (?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    print(f"  donor_sector_totals: {len(rows):,} rows")

    # Also build individual contributions flat table for per-donor queries
    _build_individual_contributions(conn)


# Virginia has NO contribution limits for state races — unlimited corporate giving.
# These thresholds classify donors by average gift size.
VA_THRESHOLDS = {
    "mega_donor":         10_000,   # avg >= $10,000 — legal in VA, not federal
    "major_institutional": 1_000,   # avg >= $1,000
    "mixed":                200,    # avg >= $200
    "grassroots":             0,    # avg < $200
}


def _donor_tier(avg_amount: float) -> str:
    if avg_amount >= VA_THRESHOLDS["mega_donor"]:
        return "Mega Donor"
    if avg_amount >= VA_THRESHOLDS["major_institutional"]:
        return "Major Institutional"
    if avg_amount >= VA_THRESHOLDS["mixed"]:
        return "Large Donor"
    return "Grassroots"


def _build_individual_contributions(conn: sqlite3.Connection) -> None:
    """Flat table of every itemized contribution with donor tier (Virginia thresholds)."""
    conn.execute("DROP TABLE IF EXISTS va_sbe_contributions")
    conn.execute("""
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
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbc_leg ON va_sbe_contributions(legislator_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbc_cycle ON va_sbe_contributions(cycle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbc_contrib ON va_sbe_contributions(contributor_name)")

    name_to_lis: dict[str, str] = {
        row[1].lower().strip(): row[0]
        for row in conn.execute("SELECT lis_id, name FROM legislators").fetchall()
    }
    leg_names = list(name_to_lis.keys())

    candidate_cache: dict[str, str | None] = {}

    def resolve(raw: str) -> str | None:
        if raw in candidate_cache:
            return candidate_cache[raw]
        cleaned = clean_candidate_name(raw).lower()
        lis = name_to_lis.get(cleaned)
        if not lis and _RF and cleaned:
            matched = fuzzy_match(cleaned, leg_names, threshold=78)
            lis = name_to_lis[matched] if matched else None
        candidate_cache[raw] = lis
        return lis

    batch: list = []
    total = 0

    periods = sorted(p.name for p in FINANCE_DIR.iterdir() if p.is_dir())
    for period in periods:
        report_path = FINANCE_DIR / period / "Report.csv"
        sched_a_path = FINANCE_DIR / period / "ScheduleA.csv"
        if not report_path.exists() or not sched_a_path.exists():
            continue

        year_match = re.match(r"^(\d{4})", period)
        cycle = year_match.group(1) if year_match else period[:4]

        report_map: dict[str, tuple[str, str, str]] = {}
        for row in read_csv(report_path):
            candidate_name = (row.get("CandidateName") or "").strip()
            report_uid = (row.get("ReportUID") or "").strip()
            report_year = (row.get("ReportYear") or cycle).strip() or cycle
            if not candidate_name or not report_uid:
                continue
            lis_id = resolve(candidate_name)
            if lis_id:
                report_map[report_uid] = (lis_id, candidate_name, report_year)

        for row in read_csv(sched_a_path):
            report_uid = (row.get("ReportUID") or "").strip()
            if report_uid not in report_map:
                continue
            try:
                amount = float((row.get("Amount") or "0").replace("$", "").replace(",", "").strip())
            except ValueError:
                continue
            if amount <= 0:
                continue

            lis_id, candidate_name, report_year = report_map[report_uid]
            is_individual = (row.get("IsIndividual") or "True").strip().lower() == "true"
            occupation = (row.get("OccupationOrTypeOfBusiness") or "").strip()
            employer   = (row.get("NameOfEmployer") or "").strip()
            company    = (row.get("LastOrCompanyName") or "").strip()
            first      = (row.get("FirstName") or "").strip()
            contrib_name = f"{first} {company}".strip() if is_individual else company

            sector = classify_sector(occupation, employer, company if not is_individual else "")
            tier   = _donor_tier(amount)

            batch.append((
                lis_id, candidate_name, report_year,
                contrib_name, employer, occupation,
                amount, (row.get("TransactionDate") or "").strip(),
                (row.get("City") or "").strip(), (row.get("StateCode") or "").strip(),
                1 if is_individual else 0, sector, tier,
            ))

            if len(batch) >= 50_000:
                conn.executemany(
                    """INSERT INTO va_sbe_contributions
                       (legislator_id, candidate_name, cycle, contributor_name,
                        employer, occupation, amount, transaction_date,
                        city, state, is_individual, sector, donor_tier)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    batch,
                )
                conn.commit()
                total += len(batch)
                batch = []

    if batch:
        conn.executemany(
            """INSERT INTO va_sbe_contributions
               (legislator_id, candidate_name, cycle, contributor_name,
                employer, occupation, amount, transaction_date,
                city, state, is_individual, sector, donor_tier)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )
        conn.commit()
        total += len(batch)

    print(f"  va_sbe_contributions: {total:,} rows")


# ── Step 5: legislative_intelligence view ─────────────────────────────────────

def build_view(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW IF EXISTS legislative_intelligence")
    conn.execute("""
        CREATE VIEW legislative_intelligence AS
        SELECT
            l.name,
            l.party,
            l.district,
            l.chamber,
            dst.sector,
            dst.total_amount,
            dst.donor_count,
            dst.cycle,
            COUNT(DISTINCT b.bill_number)                              AS bills_introduced,
            COUNT(DISTINCT CASE
                WHEN b.subjects LIKE '%' || dst.sector || '%'
                THEN b.bill_number END)                                AS sector_aligned_bills,
            COUNT(DISTINCT v.bill_number)                              AS total_votes,
            COUNT(DISTINCT CASE WHEN v.vote = 'YES' THEN v.bill_number END) AS yes_votes
        FROM legislators l
        JOIN donor_sector_totals dst ON l.lis_id = dst.legislator_id
        LEFT JOIN va_bills  b ON b.introduced_by = l.lis_id
        LEFT JOIN va_votes  v ON v.legislator_id = l.lis_id
        GROUP BY l.name, l.party, l.district, l.chamber,
                 dst.sector, dst.total_amount, dst.donor_count, dst.cycle
    """)
    conn.commit()
    print("  legislative_intelligence view created")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["legs", "bills", "votes", "donors", "view"],
                        help="Run only one step")
    parser.add_argument("--since", type=int, default=None,
                        help="Donor step: only include cycles >= this year (e.g. 2020)")
    args = parser.parse_args()

    # Use a dedicated DB so the running app (polls.db) is never blocked by DDL
    conn = sqlite3.connect(LEG_DB, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    steps = [args.step] if args.step else ["legs", "bills", "votes", "donors", "view"]

    if "legs" in steps:
        print("Step 1: legislators")
        build_legislators(conn)

    if "bills" in steps:
        print("Step 2: va_bills")
        build_bills(conn)

    if "votes" in steps:
        print("Step 3: va_votes")
        build_votes(conn)

    if "donors" in steps:
        print("Step 4: donor_sector_totals")
        build_donor_sectors(conn, args.since)

    if "view" in steps:
        print("Step 5: legislative_intelligence view")
        build_view(conn)

    conn.close()
    print("\nDone. Query with:")
    print('  SELECT * FROM legislative_intelligence WHERE name = "Chap Petersen" ORDER BY total_amount DESC;')


if __name__ == "__main__":
    main()
