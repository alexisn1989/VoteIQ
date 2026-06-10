#!/usr/bin/env python3
"""
build_federal_vote_alignment.py

For each VA federal member, computes how often they vote YES on bills
in their top FEC donor sector vs. all other sectors.

A positive alignment_delta means the member votes YES more often on
bills that match their biggest campaign-finance sector.

Sources:
  congress_votes          — roll-call votes (bioguide_id, bill, member_vote)
  congress_bills          — bill titles + Congress.gov policy_area
  fec_industry_totals     — FEC contributions aggregated by industry/sector
  congress_members        — VA delegation roster

Writes / updates rows in: donor_vote_alignment
  legislator_id format: bioguide:<bioguide_id>  (e.g. bioguide:W000804)

Usage:
    python build_federal_vote_alignment.py
    python build_federal_vote_alignment.py --dry-run
    python build_federal_vote_alignment.py --bioguide W000804
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB  = Path(os.environ.get("DATA_DIR", str(BASE_DIR))) / "polls.db"
if not POLLS_DB.parent.is_dir():
    POLLS_DB = BASE_DIR / "polls.db"


# ── Canonical sector names ────────────────────────────────────────────────────
# Both FEC industries and Congress.gov policy areas map into these 12 labels.

CANONICAL = [
    "Defense",
    "Healthcare",
    "Finance",
    "Technology",
    "Labor",
    "Transportation",
    "Energy",
    "Environment",
    "Education",
    "Agriculture",
    "Housing",
    "Criminal Justice",
]


# ── FEC industry → canonical sector ──────────────────────────────────────────
# Covers all 58+ distinct industry values seen in fec_industry_totals.

FEC_TO_CANONICAL: dict[str, str | None] = {
    # Defense
    "Defense & Aerospace":    "Defense",
    "Defense":                "Defense",
    "Aerospace & Weapons":    "Defense",
    "Defense IT & Services":  "Defense",
    # Finance
    "Finance & Banking":      "Finance",
    "Banking":                "Finance",
    "Financial Services":     "Finance",
    "Investment & Securities":"Finance",
    "Insurance":              "Finance",
    # Healthcare
    "Healthcare & Pharma":    "Healthcare",
    "Health Professionals":   "Healthcare",
    "Hospitals":              "Healthcare",
    "Health Insurance":       "Healthcare",
    "Pharma":                 "Healthcare",
    # Technology
    "Technology":             "Technology",
    "Technology & IT":        "Technology",
    "Software & IT":          "Technology",
    "Telecom":                "Technology",
    "Telecom & Media":        "Technology",
    # Labor
    "Labor & Unions":         "Labor",
    "Building & Industrial Unions": "Labor",
    "Education Unions":       "Labor",
    "Healthcare Worker Unions":"Labor",
    "Public Employee Unions": "Labor",
    "Service & Retail Workers":"Labor",
    # Transportation
    "Transportation":         "Transportation",
    "Automotive":             "Transportation",
    # Energy
    "Energy & Oil/Gas":       "Energy",
    "Fossil Fuels":           "Energy",
    "Renewables":             "Energy",
    "Utilities":              "Energy",
    # Environment — no FEC industry maps cleanly here; leave for bill-side only
    # Education
    "Education":              "Education",
    # Agriculture
    "Agriculture":            "Agriculture",
    "Agribusiness":           "Agriculture",
    "Agriculture & Food":     "Agriculture",
    "Livestock & Poultry":    "Agriculture",
    # Housing / Real Estate
    "Commercial Real Estate": "Housing",
    "Homebuilders":           "Housing",
    "Real Estate":            "Housing",
    "Real Estate & Construction": "Housing",
    "Realtors":               "Housing",
    # Criminal Justice / Legal
    "Criminal Justice":       "Criminal Justice",
    "Legal":                  "Criminal Justice",
    "Corporate Law":          "Criminal Justice",
    "Trial Lawyers":          "Criminal Justice",
    "Guns/NRA":               "Criminal Justice",
    # Unmappable — skip as primary donor sector
    "Alcohol/Tobacco":        None,
    "Gambling/Casinos":       None,
    "Tobacco":                None,
    "Grassroots":             None,
    "Ideological":            None,
    "Ideological/PAC":        None,
    "Lobbying & Consulting":  None,
    "Manufacturing":          None,
    "Other":                  None,
    "Other/Unknown":          None,
    "Retired/Individual":     None,
    "Small Business":         None,
    "Retail":                 None,
    "Retail & Hospitality":   None,
    "Hospitality":            None,
}


# ── Congress.gov policy_area → canonical sector ───────────────────────────────

POLICY_AREA_TO_CANONICAL: dict[str, str | None] = {
    "Armed Forces and National Security": "Defense",
    "International Affairs":              "Defense",
    "Health":                             "Healthcare",
    "Finance and Financial Sector":       "Finance",
    "Taxation":                           "Finance",
    "Economics and Public Finance":       "Finance",
    "Foreign Trade and International Finance": "Finance",
    "Commerce":                           "Finance",
    "Science, Technology, Communications":"Technology",
    "Labor and Employment":               "Labor",
    "Transportation and Public Works":    "Transportation",
    "Energy":                             "Energy",
    "Environmental Protection":           "Environment",
    "Public Lands and Natural Resources": "Environment",
    "Water Resources Development":        "Environment",
    "Animals":                            "Environment",
    "Education":                          "Education",
    "Agriculture and Food":               "Agriculture",
    "Housing and Community Development":  "Housing",
    "Crime and Law Enforcement":          "Criminal Justice",
    "Law":                                "Criminal Justice",
    "Families":                           "Healthcare",
    "Social Welfare":                     "Healthcare",
    "Immigration":                        None,
    "Native Americans":                   None,
    "Civil Rights and Liberties, Minority Issues": None,
    "Government Operations and Politics": None,
    "Congress":                           None,
    "Emergency Management":               None,
    "Sports and Recreation":              None,
}


# ── Bill-title keyword classifier (fallback when policy_area is absent) ───────

_BILL_KEYWORDS: dict[str, list[str]] = {
    "Defense": [
        "defense", "military", "armed forces", "national security", "pentagon",
        "navy", "army", "air force", "marine", "coast guard", "veteran",
        "nuclear weapon", "missile", "nato", "intelligence", "cia", "nsa",
        "space force", "combat", "warfare", "munition", "shipbuilding",
    ],
    "Healthcare": [
        "health", "hospital", "medical", "physician", "nurse", "dental",
        "pharma", "pharmaceutical", "drug", "medicare", "medicaid", "fda",
        "substance abuse", "mental health", "behavioral health", "vaccine",
        "telehealth", "cancer", "diabetes", "opioid", "patient",
    ],
    "Finance": [
        "bank", "financial", "finance", "tax", "revenue", "budget",
        "appropriation", "credit", "loan", "mortgage", "securities", "treasury",
        "debt", "deficit", "tariff", "trade", "customs", "irs", "investment",
        "insurance", "cryptocurrency", "fintech",
    ],
    "Technology": [
        "technology", "cyber", "data", "privacy", "internet", "broadband",
        "artificial intelligence", "ai ", "digital", "software", "semiconductor",
        "5g", "telecom", "communications", "fcc", "spectrum", "cloud",
    ],
    "Labor": [
        "labor", "worker", "employment", "wage", "union", "workplace",
        "unemployment", "minimum wage", "osha", "pension", "workforce",
        "job training", "apprenticeship", "right to work",
    ],
    "Transportation": [
        "transportation", "highway", "road", "bridge", "rail", "railroad",
        "transit", "vehicle", "aviation", "airport", "faa", "dot ",
        "electric vehicle", "autonomous vehicle", "port ", "maritime",
    ],
    "Energy": [
        "energy", "oil", "gas", "pipeline", "coal", "nuclear",
        "solar", "wind", "renewable", "power grid", "utility",
        "electricity", "natural gas", "offshore drill", "fossil fuel",
        "clean energy",
    ],
    "Environment": [
        "environment", "climate", "clean air", "clean water", "epa",
        "conservation", "pollution", "wetland", "wildlife", "endangered",
        "national park", "forest", "chesapeake", "carbon", "emission",
    ],
    "Education": [
        "education", "school", "student", "teacher", "college", "university",
        "higher education", "curriculum", "pell grant", "student loan",
        "early childhood",
    ],
    "Agriculture": [
        "agriculture", "farm", "crop", "livestock", "rural", "usda",
        "food safety", "pesticide", "commodity", "irrigation", "dairy",
        "poultry", "fishing", "aquaculture",
    ],
    "Housing": [
        "housing", "rent", "eviction", "tenant", "landlord", "zoning",
        "affordable housing", "hud ", "mortgage", "homelessness", "fha",
        "construction", "homeowner",
    ],
    "Criminal Justice": [
        "criminal", "crime", "police", "law enforcement", "prison", "jail",
        "sentencing", "firearm", "gun control", "background check",
        "drug trafficking", "fbi", "doj", "prosecution", "parole",
    ],
}

_SORTED_BILL_KWS = [
    (sec, sorted(kws, key=len, reverse=True))
    for sec, kws in _BILL_KEYWORDS.items()
]

# Procedural votes that don't map to any topic — exclude from alignment math
_PROCEDURAL_QUESTIONS = frozenset([
    "On the Motion to Adjourn",
    "On the Motion to Proceed",
    "On the Cloture Motion",
    "On Cloture on the Motion to Proceed",
    "On the Motion to Reconsider",
    "On the Nomination",
    "On Ordering the Previous Question",
    "On the Motion to Table",
    "On Motion to Table",
    "On the Motion to Discharge",
])


def classify_bill_title(title: str) -> str | None:
    """Return canonical sector for a bill title, or None."""
    t = (title or "").lower()
    for sector, kws in _SORTED_BILL_KWS:
        if any(kw in t for kw in kws):
            return sector
    return None


def normalize_bill_id(bill: str) -> str:
    """'H R 3492' → 'HR3492',  'S. 1383' → 'S1383',  'H.J.Res. 60' → 'HJRES60', etc."""
    return bill.upper().replace(" ", "").replace(".", "")


# ── DB helpers ────────────────────────────────────────────────────────────────

_SCHEMA_EXTRA = """
CREATE INDEX IF NOT EXISTS idx_dva_name   ON donor_vote_alignment(name);
CREATE INDEX IF NOT EXISTS idx_dva_sector ON donor_vote_alignment(top_donor_sector);
CREATE INDEX IF NOT EXISTS idx_dva_delta  ON donor_vote_alignment(alignment_delta);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS donor_vote_alignment (
            legislator_id    TEXT PRIMARY KEY,
            name             TEXT,
            party            TEXT,
            chamber          TEXT,
            top_donor_sector TEXT,
            top_sector_amt   REAL,
            sector_yes_rate  REAL,
            other_yes_rate   REAL,
            alignment_delta  REAL,
            sector_vote_count  INTEGER,
            other_vote_count   INTEGER,
            by_sector_json   TEXT,
            updated_at       TEXT
        )
    """)
    conn.executescript(_SCHEMA_EXTRA)
    conn.commit()


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_members(conn: sqlite3.Connection, only_bioguide: str | None) -> list[dict]:
    """Return VA federal delegation members."""
    sql = "SELECT bioguide_id, name, party, chamber FROM congress_members"
    params: list = []
    if only_bioguide:
        sql += " WHERE bioguide_id = ?"
        params.append(only_bioguide)
    rows = conn.execute(sql, params).fetchall()
    return [
        {"bioguide_id": r[0], "name": r[1], "party": r[2], "chamber": r[3]}
        for r in rows
    ]


def load_bill_sector_map(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Build {normalized_bill_id → canonical_sector} from congress_bills.
    Uses policy_area first, falls back to keyword-classifying the title.
    """
    rows = conn.execute(
        "SELECT congress, bill_type, bill_number, title, policy_area FROM congress_bills"
    ).fetchall()
    mapping: dict[str, str] = {}
    for congress, bill_type, bill_number, title, policy_area in rows:
        norm = normalize_bill_id(f"{bill_type}{bill_number}")
        sector = None
        if policy_area:
            sector = POLICY_AREA_TO_CANONICAL.get(policy_area)
        if sector is None and title:
            sector = classify_bill_title(title)
        if sector:
            mapping[norm] = sector
    return mapping


def load_fec_sectors(
    conn: sqlite3.Connection, bioguide_id: str
) -> tuple[str | None, float, list[dict]]:
    """
    Return (top_canonical_sector, top_sector_total, sector_breakdown_list)
    from fec_industry_totals for the most recent cycle.
    """
    rows = conn.execute(
        """
        SELECT industry, total_amount, MAX(cycle) AS cycle
        FROM fec_industry_totals
        WHERE bioguide_id = ?
        GROUP BY industry
        ORDER BY total_amount DESC
        """,
        (bioguide_id,),
    ).fetchall()

    breakdown: list[dict] = []
    for industry, total, cycle in rows:
        canonical = FEC_TO_CANONICAL.get(industry)
        if canonical:
            breakdown.append({"sector": canonical, "fec_industry": industry, "total": total})

    if not breakdown:
        return None, 0.0, []

    # Sum by canonical sector (multiple FEC industries can map to same canonical)
    sector_totals: dict[str, float] = defaultdict(float)
    for entry in breakdown:
        sector_totals[entry["sector"]] += entry["total"]

    sorted_sectors = sorted(sector_totals.items(), key=lambda x: x[1], reverse=True)
    top_sector, top_total = sorted_sectors[0]
    full_breakdown = [{"sector": s, "total": t} for s, t in sorted_sectors]
    return top_sector, top_total, full_breakdown


def load_votes(conn: sqlite3.Connection, bioguide_id: str) -> list[dict]:
    """Load substantive roll-call votes for a member.
    Excludes nominations (PN *) and amendments (S.Amdt.) — neither can be
    classified to a policy sector from bill title alone.
    """
    rows = conn.execute(
        """
        SELECT bill, question, member_vote
        FROM congress_votes
        WHERE bioguide_id = ?
          AND member_vote IN ('Yea', 'Nay', 'Yes', 'No', 'Aye', 'Naye')
          AND bill NOT LIKE 'PN %'
          AND bill NOT LIKE 'PN%-%'
          AND bill NOT LIKE 'S.Amdt.%'
          AND bill NOT IN ('ADJOURN', 'QUORUM', '')
        """,
        (bioguide_id,),
    ).fetchall()
    return [
        {"bill": r[0], "question": r[1], "member_vote": r[2]}
        for r in rows
    ]


# ── Alignment computation ────────────────────────────────────────────────────

def compute_alignment(
    votes: list[dict],
    bill_sector_map: dict[str, str],
    top_donor_sector: str,
) -> dict | None:
    """
    Returns alignment stats dict, or None if insufficient data.
    """
    # Per-sector tallies: {sector -> [yes, total]}
    tallies: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    classified_votes = 0

    for vote in votes:
        # Skip purely procedural votes
        if vote["question"] in _PROCEDURAL_QUESTIONS:
            continue

        norm = normalize_bill_id(vote["bill"])
        sector = bill_sector_map.get(norm)
        if sector is None:
            continue

        classified_votes += 1
        is_yes = vote["member_vote"].upper() in ("YEA", "YES", "AYE")
        tallies[sector][1] += 1
        if is_yes:
            tallies[sector][0] += 1

    if classified_votes < 5:
        return None  # not enough classifiable votes

    # Build by_sector_json
    by_sector = []
    for sector in CANONICAL:
        if sector not in tallies:
            continue
        yes, total = tallies[sector]
        yes_rate = round(yes / total * 100, 1) if total else 0.0
        by_sector.append({
            "sector": sector,
            "yes": yes,
            "total": total,
            "yes_rate": yes_rate,
        })

    # Sector yes rate
    donor_tally = tallies.get(top_donor_sector, [0, 0])
    sector_yes = donor_tally[0]
    sector_total = donor_tally[1]
    sector_yes_rate = round(sector_yes / sector_total * 100, 1) if sector_total else 0.0

    # Other yes rate (all votes NOT in the top donor sector)
    other_yes = sum(t[0] for s, t in tallies.items() if s != top_donor_sector)
    other_total = sum(t[1] for s, t in tallies.items() if s != top_donor_sector)
    other_yes_rate = round(other_yes / other_total * 100, 1) if other_total else 0.0

    alignment_delta = round(sector_yes_rate - other_yes_rate, 1)

    return {
        "sector_yes_rate":   sector_yes_rate,
        "other_yes_rate":    other_yes_rate,
        "alignment_delta":   alignment_delta,
        "sector_vote_count": sector_total,
        "other_vote_count":  other_total,
        "by_sector_json":    json.dumps(by_sector),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, only_bioguide: str | None = None) -> None:
    print(f"build_federal_vote_alignment  db={POLLS_DB}  dry_run={dry_run}")

    if not POLLS_DB.exists():
        print("ERROR: polls.db not found.")
        return

    conn = sqlite3.connect(str(POLLS_DB))
    conn.row_factory = sqlite3.Row

    _ensure_table(conn)

    members = load_members(conn, only_bioguide)
    if not members:
        print("No members found in congress_members.")
        return
    print(f"Members to process: {len(members)}")

    bill_sector_map = load_bill_sector_map(conn)
    print(f"Bill sector map: {len(bill_sector_map)} bills classified")

    inserted = updated = skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for member in members:
        bio = member["bioguide_id"]
        name = member["name"]
        party = member["party"]
        chamber = member["chamber"]
        leg_id = f"bioguide:{bio}"

        top_sector, top_amt, fec_breakdown = load_fec_sectors(conn, bio)
        if top_sector is None:
            print(f"  {name:<30s} — no FEC industry data, skipping")
            skipped += 1
            continue

        votes = load_votes(conn, bio)
        alignment = compute_alignment(votes, bill_sector_map, top_sector)
        if alignment is None:
            print(f"  {name:<30s} — insufficient classified votes ({len(votes)} total), skipping")
            skipped += 1
            continue

        print(
            f"  {name:<30s} top_sector={top_sector:<16s} "
            f"delta={alignment['alignment_delta']:+.1f}%  "
            f"({alignment['sector_vote_count']} sector votes / {alignment['other_vote_count']} other)"
        )

        if not dry_run:
            existing = conn.execute(
                "SELECT legislator_id FROM donor_vote_alignment WHERE legislator_id=?",
                (leg_id,),
            ).fetchone()

            conn.execute(
                """
                INSERT INTO donor_vote_alignment
                    (legislator_id, name, party, chamber,
                     top_donor_sector, top_sector_amt,
                     sector_yes_rate, other_yes_rate, alignment_delta,
                     sector_vote_count, other_vote_count,
                     by_sector_json, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(legislator_id) DO UPDATE SET
                    name=excluded.name,
                    party=excluded.party,
                    chamber=excluded.chamber,
                    top_donor_sector=excluded.top_donor_sector,
                    top_sector_amt=excluded.top_sector_amt,
                    sector_yes_rate=excluded.sector_yes_rate,
                    other_yes_rate=excluded.other_yes_rate,
                    alignment_delta=excluded.alignment_delta,
                    sector_vote_count=excluded.sector_vote_count,
                    other_vote_count=excluded.other_vote_count,
                    by_sector_json=excluded.by_sector_json,
                    updated_at=excluded.updated_at
                """,
                (
                    leg_id, name, party, chamber,
                    top_sector, top_amt,
                    alignment["sector_yes_rate"],
                    alignment["other_yes_rate"],
                    alignment["alignment_delta"],
                    alignment["sector_vote_count"],
                    alignment["other_vote_count"],
                    alignment["by_sector_json"],
                    now,
                ),
            )
            conn.commit()
            if existing:
                updated += 1
            else:
                inserted += 1

    conn.close()
    print(
        f"\nDone.  inserted={inserted}  updated={updated}  skipped={skipped}"
    )
    if dry_run:
        print("(dry-run — no rows written)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build federal vote-donor alignment rows")
    ap.add_argument("--dry-run", action="store_true", help="Compute but don't write")
    ap.add_argument("--bioguide", metavar="ID", help="Process one member only")
    args = ap.parse_args()
    run(dry_run=args.dry_run, only_bioguide=args.bioguide)
