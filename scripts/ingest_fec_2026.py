#!/usr/bin/env python3
"""
ingest_fec_2026.py

Pull 2026-cycle FEC campaign finance data for the Virginia federal delegation
and upsert into polls.db:
  - fec_industry_totals        — contributions aggregated by sector
  - fec_individual_contributions — top itemised contributions

Flow per member:
  1. /candidate/{fec_id}/committees/?cycle=2026&designation=P
       -> principal campaign committee ID
  2. /schedules/schedule_a/by_employer/?committee_id=...&two_year_transaction_period=2026
       -> employer-level aggregates; classified into sectors
  3. /schedules/schedule_a/?committee_id=...&two_year_transaction_period=2026&sort=-contribution_receipt_amount
       -> top 200 itemised contributions (for fec_individual_contributions)

Usage:
    python scripts/ingest_fec_2026.py
    python scripts/ingest_fec_2026.py --cycle 2026
    python scripts/ingest_fec_2026.py --bioguide K000399
    python scripts/ingest_fec_2026.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────

_HERE       = Path(__file__).resolve().parent
PROJECT_DIR = _HERE.parent
DB_PATH     = Path(os.getenv("DATA_DIR", str(PROJECT_DIR))) / "polls.db"

# ── FEC API ───────────────────────────────────────────────────────────────────

FEC_API_KEY  = os.getenv("FEC_API_KEY", "DEMO_KEY")
FEC_BASE     = "https://api.open.fec.gov/v1"
HEADERS      = {"User-Agent": "VoteIQ/1.0 (election research; contact alexisnieuwenhuys89@gmail.com)"}
REQUEST_PAUSE = 0.25   # seconds between calls (well under 1 000 req/hour limit)

# ── Industry classifier ───────────────────────────────────────────────────────
# Order matters — first match wins.

SECTOR_MAP: list[tuple[str, list[str]]] = [
    # Pass-through fundraising platforms — classify before generic terms fire
    ("Grassroots/Small Donor",   ["winred", "actblue", "ngp van", "revv"]),

    # Party & ideological committees
    ("Ideological/PAC",          ["pac", "political action", "victory fund", "majority fund",
                                   "minority fund", "conservative", "progressive", "freedom caucus",
                                   "club for growth", "emily's list", "nrcc", "dccc", "nrsc", "dscc",
                                   "republican party", "democratic party"]),

    # Defense — critical for VA (Hampton Roads, Northern VA)
    ("Defense & Aerospace",      ["lockheed", "northrop", "raytheon", "boeing", "general dynamics",
                                   "l3harris", "bae systems", "leidos", "saic", "booz allen",
                                   "huntington ingalls", "mantech", "caci", "dxc", "perspecta",
                                   "dyncorp", "csc", "accenture federal", "amentum",
                                   "shipyard", "ship repair", "naval", "nsc ", "colonna",
                                   "aerospace", "defense contractor", "military contractor"]),

    # Finance
    ("Finance & Banking",        ["bank", "financial", "bancorp", "capital group", "investment",
                                   "securities", "goldman", "jpmorgan", "morgan stanley", "blackstone",
                                   "kkr", "carlyle", "apollo", "insurance", "fidelity", "vanguard",
                                   "t. rowe", "tiaa", "prudential", "hartford", "allstate", "travelers",
                                   "hedge fund", "private equity", "asset management", "wealth management",
                                   "credit union", "mortgage"]),

    # Healthcare & Pharma
    ("Healthcare & Pharma",      ["hospital", "health system", "medical center", "pharma", "pfizer",
                                   "merck", "johnson & johnson", "abbvie", "eli lilly", "bristol",
                                   "amgen", "genentech", "physician", "doctor", "md ", "clinic",
                                   "dental", "dentist", "nurse", "nursing", "biotech", "bioscience",
                                   "medical device", "medtronic", "stryker", "health care",
                                   "inova", "sentara", "vcu health", "novant",
                                   "therapy", "therapist", "therapeutic", "mental health",
                                   "hearing", "audiology", "optical", "vision care",
                                   "chiropractic", "orthopedic", "oncology", "cardiology"]),

    # Real Estate & Construction
    ("Real Estate & Construction",["real estate", "realty", "properties", "construction", "builder",
                                   "developer", "homebuilder", "housing", "apartment", "reit",
                                   "property management", "land developer", "commercial real estate"]),

    # Technology
    ("Technology & IT",          ["google", "alphabet", "amazon", "microsoft", "apple", "meta",
                                   "facebook", "oracle", "ibm", "cisco", "intel", "nvidia",
                                   "software", "tech ", "technology", "computer", "data center",
                                   "cloud", "cyber", "it services", "information technology",
                                   "consulting firm", "systems integrator", "aws", "azure",
                                   "qualcomm", "dell", "hp ", "hewlett"]),

    # Energy
    ("Energy & Oil/Gas",         ["oil", "natural gas", "petroleum", "exxon", "chevron", "shell",
                                   "bp ", "conocophillips", "pioneer", "dominion energy",
                                   "appalachian power", "coal", "mining", "utility", "electric",
                                   "solar", "wind power", "renewable", "pipeline", "fracking",
                                   "lng ", "refinery"]),

    # Legal
    ("Legal",                    ["attorney", "lawyer", "law firm", "llp", "legal counsel",
                                   "barrister", "solicitor"]),

    # Telecom & Media
    ("Telecom & Media",          ["telecom", "comcast", "verizon", "at&t", "sprint", "t-mobile",
                                   "spectrum", "cox ", "lumen", "media", "broadcast", "publishing",
                                   "newspaper", "news group", "fox ", "cbs ", "nbc ", "abc ",
                                   "warner media", "discovery", "entertainment"]),

    # Agriculture
    ("Agriculture & Food",       ["farm", "agriculture", "agricultural", "food processing",
                                   "crop", "livestock", "dairy", "poultry", "pork", "beef",
                                   "tyson", "smithfield", "perdue", "pilgrim", "cargill",
                                   "john deere", "agco"]),

    # Transportation
    ("Transportation",           ["airline", "aviation", "united air", "american air", "southwest",
                                   "delta air", "trucking", "shipping", "logistics", "railroad",
                                   "norfolk southern", "csx ", "automotive", "auto dealer",
                                   "ford ", "general motors", "stellantis", "toyota", "fedex",
                                   "ups ", "amazon logistics"]),

    # Labor & Unions
    ("Labor & Unions",           ["union", "labor ", "afl-cio", "seiu", "teamsters", "ibew",
                                   "ufcw", "afscme", "cwa ", "united auto workers", "uaw "]),

    # Education
    ("Education",                ["university", "college", "school", "education", "teacher",
                                   "professor", "faculty", "academic"]),

    # Retail & Hospitality
    ("Retail & Hospitality",     ["retail", "restaurant", "hotel", "hospitality", "walmart",
                                   "target ", "amazon retail", "grocery", "food service",
                                   "marriott", "hilton", "hyatt", "casino", "resort"]),

    # Lobbyists
    ("Lobbying & Consulting",    ["lobbying", "lobbyist", "government affairs", "public affairs",
                                   "policy consulting", "political consulting"]),

    # Retired / individual donors
    ("Retired/Individual",       ["retired", "self-employed", "self employed", "homemaker",
                                   "not employed", "none", "n/a", "individual"]),

    # Catch-all
    ("Other/Unknown",            []),
]


_NO_EMPLOYER = frozenset({
    "", "null", "none", "n/a", "na", "self", "self-employed", "self employed",
    "not employed", "unemployed", "homemaker", "retired", "information requested",
    "information requested per best efforts", "requested", "entrepreneur",
    "freelance", "independent", "sole proprietor", "owner", "private",
})


def classify_employer(employer: str) -> str:
    """Map a free-text employer name to a sector label."""
    if not employer:
        return "Retired/Individual"
    low = employer.lower().strip()
    if low in _NO_EMPLOYER or low.startswith("information requested"):
        return "Retired/Individual"
    for sector, keywords in SECTOR_MAP:
        for kw in keywords:
            if kw in low:
                return sector
    # Generic finance patterns not caught above
    if any(t in low for t in (
        "capital", "fund", " partners", "management", "investment",
        "equity", "ventures", "venture", "holdings", "asset", "wealth",
        "trading", " llc", " lp", " gp",
    )):
        # Only upgrade to Finance if it looks like a firm, not a non-profit/govt
        if not any(t in low for t in ("school", "church", "government", "county", "city of")):
            return "Finance & Banking"
    return "Other/Unknown"


# ── FEC API helpers ───────────────────────────────────────────────────────────

def _get(path: str, params: dict) -> dict:
    params = {"api_key": FEC_API_KEY, **params}
    url = f"{FEC_BASE}/{path.lstrip('/')}"
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_principal_committee(fec_candidate_id: str, cycle: int) -> str | None:
    """Return the principal campaign committee ID for a candidate/cycle, or None."""
    try:
        data = _get(
            f"/candidate/{fec_candidate_id}/committees/",
            {"cycle": cycle, "designation": "P", "per_page": 5},
        )
        results = data.get("results", [])
        if results:
            return results[0].get("committee_id")
        # Fallback: any authorised committee
        data2 = _get(
            f"/candidate/{fec_candidate_id}/committees/",
            {"cycle": cycle, "designation": "A", "per_page": 5},
        )
        results2 = data2.get("results", [])
        if results2:
            return results2[0].get("committee_id")
    except Exception as exc:
        print(f"    [WARN] committee lookup failed: {exc}")
    return None


def fetch_by_employer(committee_id: str, cycle: int) -> list[dict]:
    """
    Return all employer-level contribution aggregates for a committee/cycle.
    Each item: {employer, total, count}
    """
    aggregates: list[dict] = []
    page = 1
    while True:
        try:
            data = _get(
                "/schedules/schedule_a/by_employer/",
                {
                    "committee_id": committee_id,
                    "two_year_transaction_period": cycle,
                    "per_page": 100,
                    "page": page,
                    "sort": "-total",
                },
            )
        except Exception as exc:
            print(f"    [WARN] by_employer page {page} failed: {exc}")
            break

        results = data.get("results", [])
        if not results:
            break
        aggregates.extend(results)

        pagination = data.get("pagination", {})
        total_pages = (pagination.get("count", 0) + 99) // 100
        if page >= total_pages or page >= 50:   # safety cap at 5 000 employers
            break
        page += 1
        time.sleep(REQUEST_PAUSE)

    return aggregates


def fetch_top_contributions(committee_id: str, cycle: int, limit: int = 200) -> list[dict]:
    """Return the top `limit` itemised Schedule A contributions (by amount)."""
    contributions: list[dict] = []
    per_page = min(limit, 100)
    page = 1
    while len(contributions) < limit:
        try:
            data = _get(
                "/schedules/schedule_a/",
                {
                    "committee_id": committee_id,
                    "two_year_transaction_period": cycle,
                    "per_page": per_page,
                    "page": page,
                    "sort": "-contribution_receipt_amount",
                    "is_individual": True,
                },
            )
        except Exception as exc:
            print(f"    [WARN] schedule_a page {page} failed: {exc}")
            break

        results = data.get("results", [])
        if not results:
            break
        contributions.extend(results)
        pagination = data.get("pagination", {})
        if len(contributions) >= pagination.get("count", 0):
            break
        page += 1
        time.sleep(REQUEST_PAUSE)

    return contributions[:limit]


# ── Database helpers ──────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fec_industry_totals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id       TEXT NOT NULL,
            member_name       TEXT,
            cycle             INTEGER NOT NULL,
            industry          TEXT NOT NULL,
            total_amount      REAL,
            contributor_count INTEGER,
            top_donors        TEXT,
            fetched_at        TEXT,
            UNIQUE(bioguide_id, cycle, industry)
        );

        CREATE TABLE IF NOT EXISTS fec_individual_contributions (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            fec_sub_id           TEXT,
            candidate_id         TEXT,
            candidate_name       TEXT,
            contributor_name     TEXT,
            contributor_employer TEXT,
            contributor_occupation TEXT,
            employer_sector      TEXT,
            amount               REAL,
            contribution_date    TEXT,
            city                 TEXT,
            state                TEXT,
            cycle                INTEGER,
            raw_json             TEXT,
            fetched_at           TEXT,
            bioguide_id          TEXT,
            UNIQUE(fec_sub_id)
        );
    """)
    conn.commit()


def upsert_industry_totals(
    conn: sqlite3.Connection,
    bioguide_id: str,
    member_name: str,
    cycle: int,
    sector_totals: dict[str, dict],
    fetched_at: str,
    dry_run: bool = False,
) -> int:
    """Upsert sector rows into fec_industry_totals. Returns rows written."""
    rows = 0
    for sector, data in sector_totals.items():
        top_donors_json = json.dumps(data["top_employers"][:10])
        if dry_run:
            print(f"      [DRY] {sector}: ${data['total']:,.0f} ({data['count']} donors)")
            rows += 1
            continue
        conn.execute(
            """
            INSERT INTO fec_industry_totals
                (bioguide_id, member_name, cycle, industry,
                 total_amount, contributor_count, top_donors, fetched_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(bioguide_id, cycle, industry) DO UPDATE SET
                total_amount      = excluded.total_amount,
                contributor_count = excluded.contributor_count,
                top_donors        = excluded.top_donors,
                fetched_at        = excluded.fetched_at
            """,
            (
                bioguide_id, member_name, cycle, sector,
                data["total"], data["count"], top_donors_json, fetched_at,
            ),
        )
        rows += 1
    if not dry_run:
        conn.commit()
    return rows


def upsert_contributions(
    conn: sqlite3.Connection,
    bioguide_id: str,
    candidate_name: str,
    fec_candidate_id: str,
    cycle: int,
    contributions: list[dict],
    fetched_at: str,
    dry_run: bool = False,
) -> int:
    """Upsert top itemised contributions. Returns rows written."""
    rows = 0
    for c in contributions:
        sub_id = c.get("sub_id") or c.get("transaction_id")
        if not sub_id:
            continue
        employer = c.get("contributor_employer", "") or ""
        occupation = c.get("contributor_occupation", "") or ""
        sector = classify_employer(employer) if employer else classify_employer(occupation)
        if dry_run:
            rows += 1
            continue
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO fec_individual_contributions
                    (fec_sub_id, candidate_id, candidate_name,
                     contributor_name, contributor_employer, contributor_occupation,
                     employer_sector, amount, contribution_date,
                     city, state, cycle, raw_json, fetched_at, bioguide_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sub_id,
                    fec_candidate_id,
                    candidate_name,
                    c.get("contributor_name", ""),
                    employer,
                    occupation,
                    sector,
                    c.get("contribution_receipt_amount", 0),
                    c.get("contribution_receipt_date", ""),
                    c.get("contributor_city", ""),
                    c.get("contributor_state", ""),
                    cycle,
                    json.dumps(c, default=str),
                    fetched_at,
                    bioguide_id,
                ),
            )
            rows += 1
        except Exception:
            pass
    if not dry_run:
        conn.commit()
    return rows


# ── Core ingestion logic ──────────────────────────────────────────────────────

def ingest_member(
    conn: sqlite3.Connection,
    bioguide_id: str,
    fec_candidate_id: str,
    member_name: str,
    cycle: int,
    dry_run: bool = False,
) -> dict:
    """Ingest one member. Returns summary dict."""
    print(f"\n  {member_name} ({bioguide_id} / {fec_candidate_id})")

    fetched_at = datetime.now(timezone.utc).isoformat()

    # 1. Find principal committee
    print(f"    Looking up committee for cycle {cycle}...")
    time.sleep(REQUEST_PAUSE)
    committee_id = get_principal_committee(fec_candidate_id, cycle)
    if not committee_id:
        print(f"    [SKIP] No principal committee found for cycle {cycle}")
        return {"member": member_name, "status": "no_committee", "industry_rows": 0, "contrib_rows": 0}

    print(f"    Committee: {committee_id}")

    # 2. Fetch contributions by employer
    print(f"    Fetching Schedule A by employer...")
    time.sleep(REQUEST_PAUSE)
    employer_rows = fetch_by_employer(committee_id, cycle)
    print(f"    Got {len(employer_rows)} employer aggregates")

    # Aggregate by sector
    sector_totals: dict[str, dict] = {}
    for row in employer_rows:
        employer = row.get("employer", "") or ""
        total    = float(row.get("total", 0) or 0)
        count    = int(row.get("count", 0) or 0)
        sector   = classify_employer(employer)

        if sector not in sector_totals:
            sector_totals[sector] = {"total": 0.0, "count": 0, "top_employers": []}
        sector_totals[sector]["total"]  += total
        sector_totals[sector]["count"]  += count
        sector_totals[sector]["top_employers"].append({"name": employer or "(unknown)", "amount": total})

    # Sort top_employers within each sector by amount descending
    for sd in sector_totals.values():
        sd["top_employers"].sort(key=lambda x: x["amount"], reverse=True)

    # Print summary
    for sector, sd in sorted(sector_totals.items(), key=lambda x: -x[1]["total"]):
        print(f"      {sector:<32} ${sd['total']:>12,.0f}  ({sd['count']} donors)")

    industry_rows = upsert_industry_totals(
        conn, bioguide_id, member_name, cycle, sector_totals, fetched_at, dry_run
    )

    # 3. Fetch top itemised contributions
    print(f"    Fetching top 200 itemised contributions...")
    time.sleep(REQUEST_PAUSE)
    contributions = fetch_top_contributions(committee_id, cycle, limit=200)
    print(f"    Got {len(contributions)} contributions")

    contrib_rows = upsert_contributions(
        conn, bioguide_id, member_name, fec_candidate_id,
        cycle, contributions, fetched_at, dry_run
    )

    return {
        "member":        member_name,
        "status":        "ok",
        "committee":     committee_id,
        "employer_aggs": len(employer_rows),
        "industry_rows": industry_rows,
        "contrib_rows":  contrib_rows,
    }


def load_members(conn: sqlite3.Connection, bioguide_filter: str | None = None) -> list[dict]:
    """Load VA delegation from bioguide_fec_bridge + congress_members."""
    sql = """
        SELECT b.bioguide_id, b.fec_candidate_id, m.name, m.chamber, m.party
        FROM bioguide_fec_bridge b
        LEFT JOIN congress_members m ON m.bioguide_id = b.bioguide_id
        WHERE m.name IS NOT NULL
    """
    params: list = []
    if bioguide_filter:
        sql += " AND b.bioguide_id = ?"
        params.append(bioguide_filter)
    sql += " ORDER BY m.chamber DESC, m.name"   # House before Senate
    rows = conn.execute(sql, params).fetchall()

    seen: set[str] = set()
    members = []
    for r in rows:
        key = (r["bioguide_id"], r["fec_candidate_id"])
        if key in seen:
            continue
        seen.add(key)
        members.append({
            "bioguide_id":      r["bioguide_id"],
            "fec_candidate_id": r["fec_candidate_id"],
            "name":             r["name"],
            "chamber":          r["chamber"],
            "party":            r["party"],
        })
    return members


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest FEC 2026-cycle data for VA delegation")
    parser.add_argument("--cycle",     type=int,  default=2026, help="Election cycle (default 2026)")
    parser.add_argument("--bioguide",  type=str,  default=None, help="Single member bioguide_id")
    parser.add_argument("--dry-run",   action="store_true",     help="Fetch and classify but do not write")
    parser.add_argument("--db",        type=str,  default=None, help="Override DB path")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    print(f"DB: {db_path}")
    print(f"FEC API key: {'DEMO_KEY (limited)' if FEC_API_KEY == 'DEMO_KEY' else FEC_API_KEY[:8] + '...'}")
    print(f"Cycle: {args.cycle}  |  dry-run: {args.dry_run}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    members = load_members(conn, bioguide_filter=args.bioguide)
    print(f"\nMembers to process: {len(members)}")
    for m in members:
        print(f"  {m['name']} ({m['bioguide_id']}) [{m['chamber']}]")

    results = []
    for m in members:
        try:
            result = ingest_member(
                conn,
                bioguide_id=m["bioguide_id"],
                fec_candidate_id=m["fec_candidate_id"],
                member_name=m["name"],
                cycle=args.cycle,
                dry_run=args.dry_run,
            )
            results.append(result)
        except Exception as exc:
            print(f"  [ERROR] {m['name']}: {exc}")
            results.append({"member": m["name"], "status": f"error: {exc}", "industry_rows": 0, "contrib_rows": 0})

    conn.close()

    # Summary
    print(f"\n{'='*70}")
    print(f"FEC {args.cycle} ingestion complete {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'='*70}")
    total_industry = sum(r.get("industry_rows", 0) for r in results)
    total_contribs = sum(r.get("contrib_rows",  0) for r in results)
    for r in results:
        status = r.get("status", "?")
        icon   = "OK" if status == "ok" else ("SKIP" if "no_committee" in status else "ERR")
        print(f"  [{icon}] {r['member']:<40} industry={r.get('industry_rows',0):>3}  contribs={r.get('contrib_rows',0):>4}")
    print(f"\n  Total industry rows upserted : {total_industry}")
    print(f"  Total contributions upserted : {total_contribs}")


if __name__ == "__main__":
    main()
