"""
build_testimony_proxy.py

Builds a proxy for committee hearing testimony by cross-referencing:
  1. Registered lobbyists (principal name → sector)
  2. Bills active in the same session (bill title/subject → sector)
  3. Campaign donations from principal to bill sponsors (position inference)

When a lobbyist's principal operates in the same sector as a bill, and was
registered during that session, they were almost certainly in the room.

Logic:
  sector_match + registration_active_in_session = "likely testified"
  also_donated_to_sponsor                       = "likely testified FOR"
  no_donation + competing_sector                = "likely testified AGAINST"

Output: polls.db → committee_testimony_proxy

Usage:
    python build_testimony_proxy.py --session 2026
    python build_testimony_proxy.py --session 2025 --sector Energy/Utilities
    python build_testimony_proxy.py --session 2026 --top 20   # top 20 bills by lobbyist count
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB  = Path(os.environ.get("POLLS_DB", BASE_DIR / "polls.db"))

# ── Sector classifier (bill title/subject → sector) ──────────────────────────

BILL_SECTOR_KWS: dict[str, list[str]] = {
    "Energy/Utilities": [
        "energy", "electric", "utility", "utilities", "solar", "wind",
        "natural gas", "pipeline", "grid", "renewable", "fossil fuel",
        "coal", "oil", "petroleum", "nuclear", "dominion", "appalachian power",
        "offshore wind", "carbon", "emission",
    ],
    "Healthcare": [
        "health", "hospital", "medicaid", "medicare", "medical", "physician",
        "nurse", "dental", "mental health", "behavioral health", "pharmacy",
        "pharmaceutical", "drug", "opioid", "addiction", "substance",
        "certificate of public need", "copn", "insurance coverage",
    ],
    "Finance": [
        "bank", "banking", "financial", "insurance", "mortgage", "credit",
        "loan", "lending", "investment", "securities", "payday",
        "consumer finance", "fintech", "cryptocurrency", "tax credit",
    ],
    "Real Estate": [
        "real estate", "zoning", "land use", "housing", "tenant", "landlord",
        "rental", "eviction", "property tax", "assessment", "development",
        "construction", "building code", "short-term rental", "affordable housing",
    ],
    "Agriculture": [
        "farm", "farming", "agriculture", "agricultural", "livestock",
        "poultry", "crop", "pesticide", "fertilizer", "timber", "forestry",
        "aquaculture", "fishing", "rural",
    ],
    "Transportation": [
        "transportation", "highway", "road", "bridge", "transit",
        "vehicle", "motor vehicle", "driver", "license", "tolls",
        "autonomous vehicle", "electric vehicle", "rideshare", "uber",
        "lyft", "aviation", "airport", "rail", "amtrak",
    ],
    "Education/K-12": [
        "elementary school", "secondary school", "public school", "school board",
        "school division", "school district", "k-12", "kindergarten",
        "preschool", "early childhood", "early care",
        "standards of learning", "sol framework", "sol test",
        "school bus", "school security", "school crossing", "school nutrition",
        "school employee", "school funding", "school construction",
        "division superintendent", "charter school", "voucher", "homeschool",
        "teacher", "teacher licensure", "teacher certification", "educator",
        "curriculum", "literacy", "career and technical", "vocational education",
    ],
    "Education/Higher-Ed": [
        "college", "university", "higher education", "community college",
        "higher educational institution", "postsecondary", "tuition",
        "financial aid", "board of visitors", "scholarship fund",
        "dual enrollment", "concurrent enrollment", "student loan",
        "four-year institution", "graduate program", "academic program",
    ],
    "Criminal Justice": [
        "criminal", "crime", "police", "law enforcement", "prison",
        "incarceration", "parole", "probation", "sentencing", "juvenile",
        "firearm", "gun", "weapon", "marijuana", "cannabis", "expungement",
    ],
    "Alcohol/Gambling": [
        "alcohol", "beer", "wine", "spirits", "brewery", "winery",
        "distillery", "abc", "gambling", "casino", "gaming", "lottery",
        "sports betting", "wagering",
    ],
    "Tech/Data": [
        "technology", "data center", "cybersecurity", "privacy", "data",
        "broadband", "internet", "digital", "artificial intelligence",
        "social media", "biometric",
    ],
    "Labor": [
        "worker", "labor", "wage", "minimum wage", "overtime", "union",
        "collective bargaining", "employee", "employer", "workplace",
        "workers compensation", "unemployment",
    ],
    "Environment": [
        "environment", "environmental", "water quality", "air quality",
        "pollution", "climate", "stormwater", "wetland", "chesapeake bay",
        "conservation", "wildlife",
    ],
}

PRINCIPAL_SECTOR_KWS: dict[str, list[str]] = {
    "Energy/Utilities": [
        "energy", "electric", "utility", "solar", "gas", "oil", "coal",
        "pipeline", "dominion energy", "dominion resources", "appalachian power",
        "appalachian natural", "nuclear", "power company",
    ],
    "Healthcare": [
        "health", "hospital", "medical", "physician", "dental", "pharma",
        "clinic", "nursing", "behavioral", "healthcare", "insurance",
    ],
    "Finance": [
        "bank", "financial", "insurance", "mortgage", "credit", "investment",
        "capital", "securities", "wealth", "finance",
    ],
    "Real Estate": [
        "real estate", "realty", "property", "construction", "developer",
        "apartment", "housing", "homebuilder", "commercial real estate",
    ],
    "Agriculture": [
        "farm", "agri", "livestock", "crop", "timber", "forestry",
        "poultry", "dairy",
    ],
    "Transportation": [
        "transport", "transit", "highway", "trucking", "aviation",
        "airline", "railroad", "automobile", "motor vehicle", "auto",
    ],
    "Education/K-12": [
        "school board", "school district", "school division", "public school",
        "high school league", "vhsl", "preschool", "early care", "early childhood",
        "pta ", "ptsa", "school librarian", "school counselor", "school nutrition",
        "communities in schools", "teachers association", "teachers union",
        "education association",
        "career and technical", "special education", "school",
    ],
    "Education/Higher-Ed": [
        "university", "college", "higher education", "community college",
        "postsecondary", "foundation for community college",
        "council of independent colleges", "strada", "board of visitors",
        "roanoke higher education",
    ],
    "Alcohol/Gambling": [
        "beer", "wine", "spirit", "brewery", "winery", "distillery",
        "casino", "gaming", "lottery", "beverage",
    ],
    "Tech/Data": [
        "technology", "software", "data", "cyber", "digital", "computer",
        "cloud", "internet", "broadband", "telecom",
    ],
    "Labor": [
        "labor", "union", "worker", "employee", "workforce", "afl",
        "teamster", "seiu", "international brotherhood",
    ],
}

SESSION_YEAR_MAP = {
    "2026": "2026-2027",
    "2025": "2025-2026",
    "2024": "2024-2025",
    "2023": "2023-2024",
    "2022": "2022-2023",
    "2021": "2021-2022",
}


def classify_bill(title: str, subject: str, summary: str) -> str | None:
    text = f"{title} {subject} {summary}".lower()
    scores = defaultdict(int)
    for sector, kws in BILL_SECTOR_KWS.items():
        for kw in kws:
            if kw in text:
                scores[sector] += 1
    return max(scores, key=scores.get) if scores else None


def classify_principal(name: str) -> str | None:
    text = name.lower()
    for sector, kws in PRINCIPAL_SECTOR_KWS.items():
        if any(kw in text for kw in kws):
            return sector
    return None


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS committee_testimony_proxy (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session             TEXT NOT NULL,
    bill_number         TEXT NOT NULL,
    bill_title          TEXT,
    bill_sector         TEXT,
    principal_name      TEXT NOT NULL,
    principal_sector    TEXT,
    lobbyist_count      INTEGER DEFAULT 0,
    likely_position     TEXT,   -- 'support' | 'oppose' | 'neutral' | 'unknown'
    position_basis      TEXT,   -- 'donated_to_sponsor' | 'sector_match' | 'competing_sector'
    total_donated_to_sponsors REAL DEFAULT 0,
    sponsor_names       TEXT,   -- JSON array
    confidence          TEXT,   -- 'high' | 'medium' | 'low'
    built_at            TEXT,
    UNIQUE(session, bill_number, principal_name)
);
CREATE INDEX IF NOT EXISTS idx_ctp_session  ON committee_testimony_proxy(session);
CREATE INDEX IF NOT EXISTS idx_ctp_bill     ON committee_testimony_proxy(bill_number);
CREATE INDEX IF NOT EXISTS idx_ctp_sector   ON committee_testimony_proxy(bill_sector);
CREATE INDEX IF NOT EXISTS idx_ctp_position ON committee_testimony_proxy(likely_position);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── Build ─────────────────────────────────────────────────────────────────────

def build_proxy(
    conn: sqlite3.Connection,
    session: str,
    sector_filter: str | None = None,
    top_n: int = 0,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    year_range = SESSION_YEAR_MAP.get(session, f"{session}-{int(session)+1}")

    # Clear stale records before rebuild so removed/reclassified rows don't persist
    deleted = conn.execute(
        "DELETE FROM committee_testimony_proxy WHERE session=?", (session,)
    ).rowcount
    if deleted:
        print(f"  Cleared {deleted:,} old records for session {session}")

    # 1. Load all bills for this session
    # bill_id format: 'HB1000_2026' — extract bill_number from it
    bills = conn.execute("""
        SELECT bill_id,
               REPLACE(REPLACE(bill_id, '_'||session, ''), '_', '') AS bill_number,
               title, short_title, subject, summary, status
        FROM legiscan_va_bills
        WHERE session = ?
    """, (session,)).fetchall()

    print(f"  Bills in {session}: {len(bills)}")

    # 2. Classify bills by sector
    classified_bills = []
    for b in bills:
        sector = classify_bill(
            b["title"] or "",
            b["subject"] or "",
            b["summary"] or "",
        )
        if sector:
            if sector_filter and sector != sector_filter:
                continue
            classified_bills.append({
                "bill_number": b["bill_number"],
                "title":       (b["title"] or b["short_title"] or "")[:120],
                "sector":      sector,
                "bill_id":     b["bill_id"],
            })

    print(f"  Bills with sector classification: {len(classified_bills)}")
    if top_n:
        classified_bills = classified_bills[:top_n]

    # 3. Load registered lobbyists for this session year range
    lobbyists = conn.execute("""
        SELECT principal_name, lobbyist_name, year_range
        FROM lobbyist_registrations
        WHERE year_range = ? AND status = 'Approved'
    """, (year_range,)).fetchall()

    # Group by principal
    principal_lobbyists: dict[str, list[str]] = defaultdict(list)
    for r in lobbyists:
        principal_lobbyists[r["principal_name"]].append(r["lobbyist_name"])

    print(f"  Active principals in {year_range}: {len(principal_lobbyists)}")

    # 4. Classify principals by sector
    principal_sectors = {
        name: classify_principal(name)
        for name in principal_lobbyists
    }

    # 5. Load bill sponsors (use va_legislator_sponsored_bills which has bill_number)
    bill_sponsors: dict[str, list[str]] = defaultdict(list)
    sponsor_rows = conn.execute("""
        SELECT bill_number, legislator_name FROM va_legislator_sponsored_bills WHERE session = ?
        UNION
        SELECT bill_number, legislator_name FROM va_legislator_cosponsor_bills WHERE session = ?
    """, (session, session)).fetchall()
    for r in sponsor_rows:
        bill_sponsors[r["bill_number"]].append(r["legislator_name"])

    # 6. Load campaign donations: principal → legislator
    # Use donor_entity_map + va_cf_schedule_a to find who principals donated to
    donations: dict[tuple, float] = {}  # (principal_key, legislator_last) -> amount
    try:
        donation_rows = conn.execute("""
            SELECT lower(employer) as emp, lower(candidate_name) as cand,
                   SUM(amount) as total
            FROM va_cf_schedule_a
            WHERE election_cycle = ?
            GROUP BY lower(employer), lower(candidate_name)
        """, (session,)).fetchall()
        for r in donation_rows:
            donations[(r["emp"], r["cand"])] = r["total"]
    except Exception:
        pass

    # 7. Build proxy rows
    count = 0
    for bill in classified_bills:
        bill_num    = bill["bill_number"]
        bill_sector = bill["sector"]
        sponsors    = bill_sponsors.get(bill_num, [])
        sponsor_lasts = {s.split()[-1].lower() for s in sponsors}

        for principal, lobbyist_list in principal_lobbyists.items():
            # Filter out single-registrant institutions (not meaningful lobbying)
            if len(lobbyist_list) < 2:
                continue

            p_sector = principal_sectors.get(principal)
            if not p_sector:
                continue

            # Only include if sectors overlap
            if p_sector != bill_sector:
                continue

            # Score specificity: does the bill title directly name this principal?
            p_words = set(w for w in principal.lower().split() if len(w) > 4)
            title_lower = bill["title"].lower()
            name_match_score = sum(1 for w in p_words if w in title_lower)

            # Check if principal donated to any sponsor — require tighter name match
            p_lower = principal.lower()
            # Use first 12 chars of first significant word as key
            p_key = next((w for w in p_lower.split() if len(w) > 4), p_lower)[:12]
            donated = 0.0
            for last in sponsor_lasts:
                for key_emp, key_cand in donations:
                    if (last in key_cand and
                        len(p_key) >= 6 and p_key in key_emp):
                        donated += donations.get((key_emp, key_cand), 0)

            # Infer position
            if donated > 1000:
                position   = "support"
                basis      = "donated_to_sponsor"
                confidence = "high" if name_match_score > 0 else "medium"
            elif name_match_score > 0:
                position   = "support"
                basis      = "name_in_bill_title"
                confidence = "high"
            else:
                position   = "unknown"
                basis      = "sector_match"
                confidence = "low" if len(lobbyist_list) < 4 else "medium"

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO committee_testimony_proxy
                        (session, bill_number, bill_title, bill_sector,
                         principal_name, principal_sector, lobbyist_count,
                         likely_position, position_basis,
                         total_donated_to_sponsors, sponsor_names,
                         confidence, built_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    session, bill_num, bill["title"], bill_sector,
                    principal, p_sector, len(lobbyist_list),
                    position, basis,
                    round(donated, 2), json.dumps(sponsors[:5]),
                    confidence, now,
                ))
                count += 1
            except sqlite3.Error:
                pass

    conn.commit()
    return count


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(conn: sqlite3.Connection, session: str, top_n: int = 15) -> None:
    # Most active lobbied bills
    print(f"\nTop lobbied bills in {session} (by principal count):")
    print(f"  {'Bill':<10} {'Sector':<22} {'Principals':>10} {'Supporters':>10}  Title")
    print("  " + "-" * 90)
    rows = conn.execute("""
        SELECT bill_number, bill_title, bill_sector,
               COUNT(DISTINCT principal_name) as n_principals,
               SUM(CASE WHEN likely_position='support' THEN 1 ELSE 0 END) as n_support,
               SUM(total_donated_to_sponsors) as total_donated
        FROM committee_testimony_proxy
        WHERE session = ?
        GROUP BY bill_number
        ORDER BY n_principals DESC
        LIMIT ?
    """, (session, top_n)).fetchall()

    for r in rows:
        print(f"  {r['bill_number']:<10} {r['bill_sector']:<22} "
              f"{r['n_principals']:>10} {r['n_support']:>10}  "
              f"{(r['bill_title'] or '')[:45]}")

    # Most active principals
    print(f"\nMost active lobbying principals in {session}:")
    print(f"  {'Principal':<40} {'Sector':<22} {'Bills':>6} {'Lobbyists':>10}")
    print("  " + "-" * 85)
    rows2 = conn.execute("""
        SELECT principal_name, principal_sector,
               COUNT(DISTINCT bill_number) as n_bills,
               MAX(lobbyist_count) as n_lobbyists
        FROM committee_testimony_proxy
        WHERE session = ?
        GROUP BY principal_name
        ORDER BY n_bills DESC
        LIMIT ?
    """, (session, top_n)).fetchall()

    for r in rows2:
        print(f"  {r['principal_name'][:40]:<40} {r['principal_sector']:<22} "
              f"{r['n_bills']:>6} {r['n_lobbyists']:>10}")

    # Stats
    total = conn.execute(
        "SELECT COUNT(*) FROM committee_testimony_proxy WHERE session=?", (session,)
    ).fetchone()[0]
    supported = conn.execute(
        "SELECT COUNT(*) FROM committee_testimony_proxy WHERE session=? AND likely_position='support'",
        (session,)
    ).fetchone()[0]
    print(f"\n  Total proxy testimony records: {total:,}")
    print(f"  With donation-backed 'support' signal: {supported:,}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(session: str, sector: str | None, top_n: int) -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)

    print(f"Building testimony proxy for session {session}...")
    n = build_proxy(conn, session, sector_filter=sector, top_n=top_n)
    print(f"  Written: {n:,} proxy testimony records")

    print_summary(conn, session)
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--session", default="2026",  help="Legislative session year")
    p.add_argument("--sector",  default=None,    help="Filter to one sector")
    p.add_argument("--top",     default=0, type=int, help="Only process top N bills")
    args = p.parse_args()
    main(args.session, args.sector, args.top)
