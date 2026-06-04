"""
build_sponsor_finance_analysis.py

For a given session year, correlates which legislators sponsored bills in each
sector with how much they received from sector-aligned campaign donors.

Unlike donor_vote_alignment (which needs per-member roll-call votes),
this uses bill sponsorship as the activity signal — sponsoring a bill is a
more active commitment than voting for it.

Writes to polls.db: sponsor_donor_correlation

Usage:
    python build_sponsor_finance_analysis.py --year 2021
    python build_sponsor_finance_analysis.py --year 2021 --year 2023
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"
LEG_DB   = BASE_DIR / "legislative_intelligence.db"

# ── Bill sector classifier (same keyword approach as build_donor_vote_alignment) ─

import hashlib

import re as _re

# Leading prefixes — strip the phrase, keep what follows
_LEADING_COMMITTEE = _re.compile(
    r"^(friends\s+of|committee\s+to\s+elect|re-?elect|citizens\s+for|"
    r"virginians\s+for|volunteers\s+for|elect)\s+",
    _re.IGNORECASE,
)
# Trailing suffixes — strip phrase and everything after
_TRAILING_COMMITTEE = _re.compile(
    r"\s*\b(campaign\s+fund|campaign\s+committee|campaign\s+account|"
    r"political\s+action\s+committee|political\s+committee|"
    r"for\s+delegate|for\s+senate|for\s+house|for\s+virginia|"
    r"for\s+governor|for\s+attorney|for\s+lt\b|for\s+state\s+senate|"
    r"for\s+house\s+of\s+delegates)\b.*$",
    _re.IGNORECASE,
)
_PERSON_SUFFIX = _re.compile(r"\b(jr|sr|ii|iii|iv)\b\.?$", _re.I)
_LEADING_INITIAL = _re.compile(r"^([A-Z]\.?\s+){1,2}(?=[A-Z])", _re.I)  # "L. " or "L Louise "


def _norm_name(name: str) -> str:
    """
    Robust last_first-initial key that handles:
      'Louise Lucas'                     → lucas_l
      'L Louise Lucas'                   → lucas_l
      'L Louise Lucas Campaign Fund'     → lucas_l
      'Friends of Terry Kilgore'         → kilgore_t
      'Committee to Elect George Barker' → barker_g
    """
    s = (name or "").strip()
    s = _LEADING_COMMITTEE.sub("", s).strip()   # strip leading "Friends of" etc.
    s = _TRAILING_COMMITTEE.sub("", s).strip()  # strip trailing "Campaign Fund" etc.
    s = _PERSON_SUFFIX.sub("", s).strip()
    # Strip leading single initial(s): "L Louise Lucas" → "Louise Lucas"
    s = _LEADING_INITIAL.sub("", s).strip()
    parts = _re.split(r"[\s,]+", s)
    parts = [p.rstrip(".") for p in parts if p and len(p.rstrip(".")) > 0]
    if not parts:
        return ""
    if "," in name:
        last = parts[0]; first_init = parts[1][0] if len(parts) > 1 else ""
    else:
        last = parts[-1]; first_init = parts[0][0] if parts else ""
    return f"{last.lower()}_{first_init.lower()}"


BILL_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Energy/Utilities": [
        "clean economy", "offshore wind", "electric utility", "electric utilities",
        "natural gas", "net metering", "solar energy", "grid transformation",
        "renewable energy", "energy storage", "coal mine", "carbon-free",
        "rate reduction", "rate increase", "rate freeze", "state corporation commission",
        "dominion", "appalachian power", "nuclear", "energy efficiency",
    ],
    "Healthcare": [
        "medicaid", "behavioral health", "mental health", "certificate of public need",
        "prescription drug", "telehealth", "substance abuse", "health benefit",
        "health insurance", "hospital", "pharmacy", "nursing",
    ],
    "Alcohol/Gambling": [
        "casino", "gaming", "sports betting", "cannabis", "marijuana",
        "mixed beverage", "alcohol beverage", "abc board", "lottery",
        "pharmaceutical processor",
    ],
    "Finance": [
        "payday loan", "consumer finance", "interest rate", "mortgage",
        "bank", "credit union", "insurance rate", "lending", "securities",
    ],
    "Agriculture": [
        "agricultural", "farm", "livestock", "pesticide", "right to farm",
        "food safety", "crop", "rural",
    ],
    "Transportation": [
        "toll", "rideshare", "transportation network", "autonomous vehicle",
        "electric vehicle", "motor vehicle", "highway", "transit",
    ],
    "Housing": [
        "eviction", "landlord tenant", "affordable housing", "accessory dwelling",
        "rent control", "short-term rental", "zoning", "housing",
    ],
    "Labor": [
        "minimum wage", "workers compensation", "unemployment", "workplace safety",
        "collective bargaining", "labor union", "employment",
    ],
    "Criminal Justice": [
        "expungement", "sentencing", "firearm", "law enforcement", "parole",
        "probation", "criminal record", "police",
    ],
    "Education": [
        "school board", "teacher", "higher education", "student", "curriculum",
        "literacy", "college", "university",
    ],
}

# Donor name keyword mapping to sectors (for va_cf_schedule_a lookup)
DONOR_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Energy/Utilities": [
        "dominion", "appalachian power", "electric", "energy", "solar", "nuclear",
        "grid", "pipeline", "petroleum", "gas company", "coal",
    ],
    "Healthcare": [
        "health", "hospital", "medical", "physician", "dental", "pharma",
        "nurse", "clinic", "behavioral", "hca ",
    ],
    "Alcohol/Gambling": [
        "casino", "gaming", "hard rock", "mgm ", "caesars", "penndot",
        "beer", "wine", "spirits", "cannabis", "marijuana",
    ],
    "Finance": [
        "bank", "financial", "insurance", "credit union", "mortgage",
        "capital group", "investment", "securities",
    ],
    "Agriculture": [
        "farm bureau", "agri", "livestock", "farm credit", "dairy",
    ],
    "Transportation": [
        "transurban", "uber", "lyft", "rideshare", "airline", "airport",
        "automobile", "dealer", "transit authority",
    ],
    "Housing": [
        "realtor", "real estate", "homebuilder", "apartment", "housing assoc",
    ],
    "Labor": [
        "union", "seiu", "afscme", "teamster", "carpenters", "pipefitter",
        "plumber", "electricians union",
    ],
}


def classify_bill(title: str) -> str | None:
    t = (title or "").lower()
    for sector, kws in BILL_SECTOR_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return sector
    return None


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sponsor_donor_correlation (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session          TEXT NOT NULL,
    legislator_name  TEXT NOT NULL,
    sector           TEXT NOT NULL,
    bills_sponsored  INTEGER DEFAULT 0,
    bill_ids         TEXT,           -- JSON array of bill_numbers
    total_from_sector_donors REAL DEFAULT 0,
    total_raised_2021        REAL DEFAULT 0,
    sector_pct       REAL,           -- sector_donors / total * 100
    top_sector_donor TEXT,
    top_donor_amt    REAL,
    alignment_label  TEXT,           -- 'strong' / 'moderate' / 'none' / 'contrary'
    updated_at       TEXT,
    UNIQUE(session, legislator_name, sector)
);
CREATE INDEX IF NOT EXISTS idx_sdc_session ON sponsor_donor_correlation(session);
CREATE INDEX IF NOT EXISTS idx_sdc_sector  ON sponsor_donor_correlation(sector);
CREATE INDEX IF NOT EXISTS idx_sdc_name    ON sponsor_donor_correlation(legislator_name);
"""


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_sponsored_bills(
    li_conn: sqlite3.Connection, session: str
) -> dict[str, dict[str, list[str]]]:
    """
    Returns {legislator_name -> {sector -> [bill_numbers]}}
    Only primary sponsors (sponsor_order = 1).
    """
    rows = li_conn.execute("""
        SELECT bs.legislator_name, b.bill_number, b.title
        FROM va_bill_sponsors bs
        JOIN va_bills b ON bs.bill_id = b.bill_id
        WHERE b.session = ? AND bs.sponsor_order = 1
          AND bs.legislator_name != ''
          AND b.title IS NOT NULL
    """, (session,)).fetchall()

    result: dict[str, dict[str, list[str]]] = {}
    for name, bill_num, title in rows:
        sector = classify_bill(title)
        if sector is None:
            continue
        result.setdefault(name, {}).setdefault(sector, []).append(bill_num)
    return result


def _load_sector_donations(
    p_conn: sqlite3.Connection, election_cycle: str
) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """
    Returns:
      exact_map  : {candidate_name -> {sector -> total, 'total' -> grand_total}}
      norm_map   : {norm_key -> candidate_name}  (for fuzzy fallback)
    """
    rows = p_conn.execute("""
        SELECT candidate_name, last_or_company, SUM(amount) as total
        FROM va_cf_schedule_a
        WHERE election_cycle = ?
          AND last_or_company IS NOT NULL AND last_or_company != ''
        GROUP BY candidate_name, last_or_company
        ORDER BY candidate_name, total DESC
    """, (election_cycle,)).fetchall()

    exact: dict[str, dict[str, float]] = {}
    for cand, donor, amt in rows:
        donor_lower = donor.lower()
        exact.setdefault(cand, {})
        exact[cand]["total"] = exact[cand].get("total", 0) + amt
        for sector, kws in DONOR_SECTOR_KEYWORDS.items():
            if any(kw in donor_lower for kw in kws):
                exact[cand][sector] = exact[cand].get(sector, 0) + amt
                break

    # Build normalized-key lookup: keep only the record with highest total per key
    norm: dict[str, str] = {}
    for cand, data in exact.items():
        key = _norm_name(cand)
        if key and (key not in norm or
                    data.get("total", 0) > exact[norm[key]].get("total", 0)):
            norm[key] = cand

    return exact, norm


def _load_top_sector_donor(
    p_conn: sqlite3.Connection, candidate: str, election_cycle: str, sector: str
) -> tuple[str, float]:
    """Return (top_donor_name, amount) for a given candidate/sector/cycle."""
    kws = DONOR_SECTOR_KEYWORDS.get(sector, [])
    if not kws:
        return "", 0.0

    conditions = " OR ".join(f"lower(last_or_company) LIKE ?" for _ in kws)
    params = [f"%{kw}%" for kw in kws] + [candidate, election_cycle]
    row = p_conn.execute(
        f"""
        SELECT last_or_company, SUM(amount) as total
        FROM va_cf_schedule_a
        WHERE ({conditions})
          AND candidate_name = ? AND election_cycle = ?
        GROUP BY last_or_company
        ORDER BY total DESC LIMIT 1
        """,
        params,
    ).fetchone()
    return (row[0], float(row[1])) if row else ("", 0.0)


# ── Main ──────────────────────────────────────────────────────────────────────

def _alignment_label(sector_pct: float | None, n_bills: int) -> str:
    if sector_pct is None or n_bills == 0:
        return "none"
    if sector_pct >= 30:
        return "strong"
    if sector_pct >= 15:
        return "moderate"
    if sector_pct >= 5:
        return "weak"
    return "none"


def main(years: list[str]) -> None:
    li_conn = sqlite3.connect(LEG_DB)
    p_conn  = sqlite3.connect(POLLS_DB, timeout=30)
    p_conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(p_conn)

    for session in years:
        print(f"\nProcessing session {session}...")

        # Same calendar year for campaign finance cycle
        cycle = session

        print(f"  Loading bill sponsorships...")
        sponsored = _load_sponsored_bills(li_conn, session)
        print(f"  {len(sponsored):,} legislators with sponsored sector bills")

        print(f"  Loading {cycle} campaign finance...")
        donations, norm_lookup = _load_sector_donations(p_conn, cycle)
        print(f"  {len(donations):,} candidates with donation records")

        now = datetime.now(timezone.utc).isoformat()
        rows_written = 0

        for leg_name, sector_bills in sponsored.items():
            # 1. Exact name match
            sbe_name = leg_name  # canonical SBE candidate name for DB queries
            leg_finance = donations.get(leg_name, {})
            if not leg_finance:
                # 2. Normalized key match (handles "L Louise Lucas" vs "Louise Lucas")
                key = _norm_name(leg_name)
                matched_cand = norm_lookup.get(key, "")
                if matched_cand:
                    leg_finance = donations.get(matched_cand, {})
                    sbe_name = matched_cand   # use SBE name for DB queries

            # Compute total_raised directly from DB using name-fragment match
            # (committee name formats vary by cycle; LIKE is more reliable than dict lookup)
            name_parts = leg_name.split()
            first_init = name_parts[0][0].lower() if name_parts else ""
            last_word  = name_parts[-1].lower()   if name_parts else ""
            total_raised = 0.0
            if last_word and len(last_word) > 3:
                tr_row = p_conn.execute(
                    "SELECT SUM(amount) FROM va_cf_schedule_a "
                    "WHERE lower(candidate_name) LIKE ? "
                    "  AND lower(candidate_name) LIKE ? "
                    "  AND election_cycle = ?",
                    (f"%{last_word}%", f"%{first_init}%", cycle),
                ).fetchone()
                total_raised = tr_row[0] or 0.0
            if total_raised == 0:
                total_raised = leg_finance.get("total", 0.0)

            for sector, bill_nums in sector_bills.items():
                from_sector = leg_finance.get(sector, 0.0)
                sector_pct  = round(from_sector / total_raised * 100, 1) \
                              if total_raised > 0 else None

                top_donor, top_amt = _load_top_sector_donor(
                    p_conn, sbe_name, cycle, sector
                ) if from_sector > 0 else ("", 0.0)

                label = _alignment_label(sector_pct, len(bill_nums))

                p_conn.execute("""
                    INSERT INTO sponsor_donor_correlation
                        (session, legislator_name, sector, bills_sponsored,
                         bill_ids, total_from_sector_donors, total_raised_2021,
                         sector_pct, top_sector_donor, top_donor_amt,
                         alignment_label, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(session, legislator_name, sector) DO UPDATE SET
                        bills_sponsored          = excluded.bills_sponsored,
                        bill_ids                 = excluded.bill_ids,
                        total_from_sector_donors = excluded.total_from_sector_donors,
                        total_raised_2021        = excluded.total_raised_2021,
                        sector_pct               = excluded.sector_pct,
                        top_sector_donor         = excluded.top_sector_donor,
                        top_donor_amt            = excluded.top_donor_amt,
                        alignment_label          = excluded.alignment_label,
                        updated_at               = excluded.updated_at
                """, (
                    session, leg_name, sector, len(bill_nums),
                    json.dumps(bill_nums),
                    from_sector, total_raised,
                    sector_pct, top_donor, top_amt,
                    label, now,
                ))
                rows_written += 1

        p_conn.commit()
        print(f"  {rows_written:,} sponsor-donor correlation rows written")

    _print_summary(p_conn, years)
    li_conn.close()
    p_conn.close()


def _print_summary(conn: sqlite3.Connection, years: list[str]) -> None:
    for session in years:
        print(f"\n=== {session} Sponsor-Donor Correlation Summary ===")

        print("\nStrongest sector alignment (sponsors with heavy donor overlap):")
        rows = conn.execute("""
            SELECT legislator_name, sector, bills_sponsored,
                   total_from_sector_donors, total_raised_2021, sector_pct,
                   top_sector_donor, top_donor_amt
            FROM sponsor_donor_correlation
            WHERE session = ? AND alignment_label IN ('strong','moderate')
              AND total_raised_2021 > 10000
            ORDER BY sector_pct DESC LIMIT 15
        """, (session,)).fetchall()

        for r in rows:
            pct_s = f"{r[4]:.1f}%" if r[4] else "n/a"
            print(
                f"  {r[0]:<30} {r[1]:<22} "
                f"{r[2]} bills | "
                f"${r[3]:,.0f} from sector / ${r[5]:,.0f} total ({pct_s}) | "
                f"top donor: {(r[6] or 'n/a')[:30]}"
            )

        print(f"\nSector summary for {session}:")
        sec_rows = conn.execute("""
            SELECT sector,
                   COUNT(DISTINCT legislator_name) as n_sponsors,
                   SUM(bills_sponsored) as n_bills,
                   SUM(CASE WHEN alignment_label IN ('strong','moderate') THEN 1 ELSE 0 END) as aligned,
                   ROUND(AVG(sector_pct),1) as avg_pct
            FROM sponsor_donor_correlation
            WHERE session = ?
            GROUP BY sector ORDER BY n_bills DESC
        """, (session,)).fetchall()

        print(f"  {'Sector':<22} {'Sponsors':>8} {'Bills':>6} {'Aligned':>8} {'Avg%':>6}")
        for r in sec_rows:
            print(f"  {r[0]:<22} {r[1]:>8} {r[2]:>6} {r[3]:>8} {(str(r[4])+'%') if r[4] else 'n/a':>6}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--year", action="append", dest="years",
                   default=None, help="Session year (repeatable). Default: 2021")
    args = p.parse_args()
    main(args.years or ["2021"])
