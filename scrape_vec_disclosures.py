"""
scrape_vec_disclosures.py

Ingests Virginia Statement of Economic Interests (VEC Form 801) data.

DATA SOURCE OPTIONS (in order of preference):
  1. Manual CSV export from VEC search: http://ethicssearch.dls.virginia.gov
     → Search → Conflict of Interest → Export (if available)
  2. PDF scraping from VEC (requires pypdf)
  3. Direct HTTP fetch of VEC data API (documented below — CAPTCHA gated)

USAGE:
  python scrape_vec_disclosures.py --csv vec_disclosures_2024.csv
  python scrape_vec_disclosures.py --fetch   # attempts HTTP fetch (may fail)
  python scrape_vec_disclosures.py --demo    # loads synthetic demo data

VEC FORM 801 SECTIONS TRACKED:
  Part 1 — Offices/Employment (employer name, title, compensation)
  Part 5 — Business interests >$10K (company name, % ownership, nature)
  Part 4 — Real estate (location, type, acquired how)
  Part 8 — Payments from lobbyists

These are the primary conflict-of-interest signals when crossed with
campaign finance donor sectors and voting records.

DB OUTPUT: polls.db → legislator_financial_disclosures
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"

# ── Sector classifier for business interests ──────────────────────────────────
# Same mapping as donor sectors so we can cross-reference directly

BUSINESS_SECTOR_KWS: dict[str, list[str]] = {
    "Energy/Utilities": [
        "energy", "electric", "utility", "solar", "gas", "oil", "coal",
        "pipeline", "grid", "dominion", "appalachian", "nuclear", "petroleum",
    ],
    "Healthcare": [
        "health", "hospital", "medical", "physician", "dental", "pharma",
        "clinic", "nursing", "behavioral",
    ],
    "Finance": [
        "bank", "financ", "insurance", "mortgage", "credit", "investment",
        "capital", "securities", "wealth",
    ],
    "Real Estate": [
        "real estate", "realty", "property", "construction", "homebuilder",
        "apartment", "developer", "land",
    ],
    "Agriculture": [
        "farm", "agri", "livestock", "crop", "timber", "forestry",
    ],
    "Transportation": [
        "transport", "transit", "highway", "vehicle", "trucking", "aviation",
        "airline", "railroad",
    ],
    "Tech/Data": [
        "technology", "software", "data", "cyber", "digital", "computer",
        "cloud", "internet",
    ],
    "Legal": [
        "law firm", "attorney", "legal", "counsel",
    ],
    "Retail/Hospitality": [
        "retail", "restaurant", "hotel", "casino", "hospitality", "franchise",
    ],
}


def classify_sector(text: str) -> str | None:
    t = (text or "").lower()
    for sector, kws in BUSINESS_SECTOR_KWS.items():
        if any(kw in t for kw in kws):
            return sector
    return None


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS legislator_financial_disclosures (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    legislator_name  TEXT NOT NULL,
    filing_year      TEXT,
    part             TEXT,   -- 'employment' | 'business_interest' | 'real_estate' | 'lobbyist_payment'
    entity_name      TEXT,   -- employer / company / address
    role_or_type     TEXT,   -- job title / ownership % / property type
    sector           TEXT,   -- classified sector
    amount_range     TEXT,   -- e.g. '$10K-$50K' where disclosed
    notes            TEXT,
    source           TEXT DEFAULT 'vec_form801',
    scraped_at       TEXT,
    UNIQUE(legislator_name, filing_year, part, entity_name)
);
CREATE INDEX IF NOT EXISTS idx_lfd_name   ON legislator_financial_disclosures(legislator_name);
CREATE INDEX IF NOT EXISTS idx_lfd_sector ON legislator_financial_disclosures(sector);
CREATE INDEX IF NOT EXISTS idx_lfd_year   ON legislator_financial_disclosures(filing_year);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── CSV loader ────────────────────────────────────────────────────────────────

# Expected CSV columns (flexible — tries multiple name patterns):
# last_name, first_name, filing_year, part, entity_name, role, amount

COLUMN_ALIASES = {
    "legislator_name": ["name", "full_name", "legislator", "official"],
    "filing_year":     ["year", "filing_year", "filingyear", "period"],
    "part":            ["type", "part", "section", "category"],
    "entity_name":     ["entity", "company", "employer", "business", "entity_name"],
    "role_or_type":    ["role", "title", "ownership", "type", "position"],
    "amount_range":    ["amount", "value", "compensation", "amount_range"],
    "notes":           ["notes", "description", "details"],
}


def _find_col(headers: list[str], aliases: list[str]) -> str | None:
    h_lower = [h.lower().strip() for h in headers]
    for alias in aliases:
        if alias in h_lower:
            return headers[h_lower.index(alias)]
    return None


def _ingest_csv(path: Path, conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        # Map column aliases
        col = {field: _find_col(headers, aliases)
               for field, aliases in COLUMN_ALIASES.items()}

        for row in reader:
            leg_name  = row.get(col["legislator_name"] or "", "").strip()
            entity    = row.get(col["entity_name"] or "", "").strip()
            if not leg_name or not entity:
                continue

            part      = row.get(col["part"] or "", "").strip().lower()
            year      = row.get(col["filing_year"] or "", "").strip()
            role      = row.get(col["role_or_type"] or "", "").strip()
            amount    = row.get(col["amount_range"] or "", "").strip()
            notes     = row.get(col["notes"] or "", "").strip()

            sector    = classify_sector(entity + " " + role)

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO legislator_financial_disclosures
                        (legislator_name, filing_year, part, entity_name,
                         role_or_type, sector, amount_range, notes, scraped_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (leg_name, year, part, entity, role, sector, amount, notes, now))
                count += 1
            except sqlite3.Error:
                pass

    conn.commit()
    return count


# ── Demo data ─────────────────────────────────────────────────────────────────
# Synthetic illustrative data matching the VEC Form 801 structure

DEMO_DISCLOSURES = [
    # (name, year, part, entity, role, amount)
    ("Emily Jordan",   "2024", "employment",       "Appalachian Power Co.",         "Attorney",               "$50K-$100K"),
    ("Emily Jordan",   "2024", "business_interest","Jordan Energy Consulting LLC",  "Managing Member 100%",   "$10K-$50K"),
    ("Emily Jordan",   "2024", "real_estate",      "Russell County, VA",            "Residential",            ""),
    ("Mark Peake",     "2024", "employment",       "Lynchburg City Schools",        "Educator",               "$50K-$100K"),
    ("Mark Peake",     "2024", "business_interest","Peake Properties LLC",          "Owner 100%",             "$10K-$50K"),
    ("Scott Surovell", "2024", "employment",       "Law Office of Scott Surovell",  "Attorney",               "$100K+"),
    ("Scott Surovell", "2024", "business_interest","Surovell Isaacs Law",           "Partner 50%",            "$100K+"),
    ("Paul Krizek",    "2024", "employment",       "Krizek Consulting Group",       "Principal",              "$50K-$100K"),
    ("Barry Knight",   "2024", "business_interest","Knights Landing Marina",        "Owner",                  "$50K-$100K"),
    ("Barry Knight",   "2024", "real_estate",      "Virginia Beach, VA",            "Commercial",             ""),
    ("Travis Hackworth","2024","employment",        "Buchanan County",               "Commissioner",           "$50K-$100K"),
    ("Travis Hackworth","2024","business_interest", "Southwest Virginia Gas LLC",   "Owner 60%",              "$10K-$50K"),
    ("Richard Stuart", "2024", "employment",       "Stuart PC Law Office",          "Attorney",               "$100K+"),
    ("Richard Stuart", "2024", "business_interest","Stuart Properties LLC",         "Manager 100%",           "$50K-$100K"),
    ("Angelia Williams Graves","2024","employment", "Dominion Energy Virginia",     "Communications Mgr",     "$100K+"),
]


def _load_demo(conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for name, year, part, entity, role, amount in DEMO_DISCLOSURES:
        sector = classify_sector(entity + " " + role)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO legislator_financial_disclosures
                    (legislator_name, filing_year, part, entity_name,
                     role_or_type, sector, amount_range, scraped_at, source)
                VALUES (?,?,?,?,?,?,?,?,'demo_data')
            """, (name, year, part, entity, role, sector, amount, now))
            count += 1
        except sqlite3.Error:
            pass
    conn.commit()
    return count


# ── Conflict detection ────────────────────────────────────────────────────────

def detect_conflicts(conn: sqlite3.Connection) -> list[dict]:
    """
    Find legislators where:
    1. They have a business interest in sector X (financial disclosure)
    2. Their top campaign donors are in sector X
    3. (Optional) They vote more than average on sector X bills

    Returns list of conflict records ranked by signal strength.
    """
    rows = conn.execute("""
        SELECT
            d.legislator_name,
            d.sector                        AS business_sector,
            d.entity_name                   AS business_entity,
            d.role_or_type                  AS business_role,
            d.filing_year,
            c.top_sector                    AS donor_sector,
            ROUND(c.top_sector_pct,1)       AS donor_sector_pct,
            ROUND(c.total_raised,0)         AS total_raised,
            a.alignment_delta               AS vote_alignment_delta,
            a.sector_yes_rate               AS vote_yes_rate_in_sector
        FROM legislator_financial_disclosures d
        LEFT JOIN campaign_finance_summary c
               ON lower(c.name) LIKE lower('%' || substr(d.legislator_name, instr(d.legislator_name,' ')+1) || '%')
              AND c.source = 'va_sbe'
        LEFT JOIN donor_vote_alignment a
               ON a.legislator_id = c.legislator_id
        WHERE d.sector IS NOT NULL
          AND d.source != 'demo_data'   -- exclude synthetic data by default
        ORDER BY d.legislator_name, d.sector
    """).fetchall()

    conflicts = []
    for r in rows:
        if not r[0]:
            continue
        overlap = r[1] and r[3] and (
            r[1].lower() == (r[3] or "").lower() or
            r[1].split("/")[0].lower() in (r[3] or "").lower()
        )
        signals = sum([
            1 if overlap else 0,                          # donor sector matches business sector
            1 if r[8] and abs(r[8]) >= 5 else 0,         # meaningful vote alignment
            1 if r[3] and r[4] and float(r[4] or 0) >= 20 else 0,  # concentrated donor base
        ])
        conflicts.append({
            "legislator":       r[0],
            "business_sector":  r[1],
            "business_entity":  r[2],
            "business_role":    r[3],
            "donor_sector":     r[5],
            "donor_sector_pct": r[6],
            "total_raised":     r[7],
            "vote_delta":       r[8],
            "sector_overlap":   overlap,
            "signal_count":     signals,
        })

    conflicts.sort(key=lambda x: -x["signal_count"])
    return conflicts


# ── Main ──────────────────────────────────────────────────────────────────────

def main(csv_path: str | None, demo: bool, show_conflicts: bool) -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)

    if csv_path:
        path = Path(csv_path)
        if not path.exists():
            print(f"File not found: {csv_path}")
            return
        n = _ingest_csv(path, conn)
        print(f"Loaded {n} disclosures from {csv_path}")

    elif demo:
        n = _load_demo(conn)
        print(f"Loaded {n} SYNTHETIC DEMO disclosures (source='demo_data').")
        print("These are illustrative examples only — not real VEC data.")
        print("Replace with real data via: python scrape_vec_disclosures.py --csv your_export.csv")

    total = conn.execute("SELECT COUNT(*) FROM legislator_financial_disclosures").fetchone()[0]
    by_sector = conn.execute("""
        SELECT sector, COUNT(*) as n FROM legislator_financial_disclosures
        WHERE sector IS NOT NULL GROUP BY sector ORDER BY n DESC
    """).fetchall()

    print(f"\nTotal disclosure records: {total}")
    if by_sector:
        print("By sector:")
        for r in by_sector:
            print(f"  {r[0]:<25} {r[1]} records")

    if show_conflicts and total > 0:
        print("\nConflict signal analysis:")
        conflicts = detect_conflicts(conn)
        if conflicts:
            for c in conflicts[:10]:
                print(
                    f"  {c['legislator']:<25} | business={c['business_sector']} | "
                    f"donors={c['donor_sector']} | overlap={c['sector_overlap']} | "
                    f"signals={c['signal_count']}"
                )
        else:
            print("  No conflicts found (need real VEC data matched to legislator names)")

    conn.close()

    print("\nNOTE: VEC financial disclosures require manual export.")
    print("Steps to get real data:")
    print("  1. Go to http://ethicssearch.dls.virginia.gov")
    print("  2. Search 'Conflict of Interest' > select 'General Assembly'")
    print("  3. Export results as CSV (if available) or copy table data")
    print("  4. Run: python scrape_vec_disclosures.py --csv your_export.csv")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv",       default=None, help="Path to CSV export from VEC")
    p.add_argument("--demo",      action="store_true", help="Load synthetic demo data")
    p.add_argument("--conflicts", action="store_true", help="Show conflict detection analysis")
    args = p.parse_args()
    main(args.csv, args.demo, args.conflicts)
