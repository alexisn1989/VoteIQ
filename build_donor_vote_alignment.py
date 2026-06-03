"""
build_donor_vote_alignment.py

For each VA legislator, computes how often they vote YES on bills
in their top donor sector vs. all other sectors.

A positive alignment_delta means the legislator votes YES more often
on bills that match their biggest campaign-finance sector.

Writes results to polls.db table: donor_vote_alignment

Usage:
    python build_donor_vote_alignment.py
    python build_donor_vote_alignment.py --sessions 2025 2026
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB  = BASE_DIR / "polls.db"
OS_DB     = BASE_DIR / "openstates_va.db"

# ── Bill sector classifier ────────────────────────────────────────────────────

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Energy/Utilities": [
        "energy", "electric", "utility", "utilities", "solar", "pipeline",
        "gas", "petroleum", "power grid", "coal", "wind", "nuclear",
        "dominion", "appalachian", "offshore", "natural gas",
    ],
    "Healthcare": [
        "health", "hospital", "medical", "physician", "dental", "pharma",
        "nursing", "medicaid", "medicare", "behavioral", "mental health",
        "substance abuse", "addiction", "telehealth", "vaccine",
    ],
    "Tech/Data": [
        "data", "technology", "cyber", "privacy", "internet", "broadband",
        "artificial intelligence", "digital", "computer", "electronic",
        "software", "information technology",
    ],
    "Transportation": [
        "transit", "highway", "road", "vehicle", "motor vehicle", "rail",
        "airport", "transportation", "rideshare", "toll", "traffic",
        "autonomous vehicle", "electric vehicle",
    ],
    "Finance": [
        "bank", "financial", "insurance", "credit", "loan", "mortgage",
        "securities", "investment", "tax", "revenue", "budget", "lending",
    ],
    "Environment": [
        "environment", "conservation", "climate", "clean water",
        "chesapeake", "pollution", "wetland", "wildlife", "forestry",
        "stormwater", "recycling",
    ],
    "Education": [
        "education", "school board", "teacher", "student", "college",
        "university", "curriculum", "higher education", "literacy",
    ],
    "Criminal Justice": [
        "criminal", "crime", "police", "law enforcement", "prison",
        "incarceration", "parole", "probation", "sentencing", "firearm",
        "weapon", "gun",
    ],
    "Labor": [
        "labor", "worker", "employment", "wage", "union", "workplace",
        "unemployment", "minimum wage", "workers compensation",
    ],
    "Housing": [
        "housing", "rent", "eviction", "landlord", "tenant", "zoning",
        "affordable housing", "construction permit",
    ],
    "Agriculture": [
        "agriculture", "farm", "livestock", "crop", "food safety",
        "rural", "veterinary", "pesticide",
    ],
    "Alcohol/Gambling": [
        "alcohol", "beer", "wine", "spirits", "casino", "gaming",
        "lottery", "cannabis", "marijuana",
    ],
}

# Longer phrases must come first to avoid partial matches swamping them
_SORTED_SECTORS: list[tuple[str, list[str]]] = [
    (s, sorted(kws, key=len, reverse=True))
    for s, kws in SECTOR_KEYWORDS.items()
]


def classify_bill(title: str) -> str | None:
    """Return the first matching sector for a bill title, or None."""
    t = (title or "").lower()
    for sector, kws in _SORTED_SECTORS:
        if any(kw in t for kw in kws):
            return sector
    return None


# ── CFS sector → bill sector mapping ─────────────────────────────────────────
# campaign_finance_summary uses sector names from build_va_state_finance.py;
# bills are classified with SECTOR_KEYWORDS above.  This maps CFS → bill names.

CFS_TO_BILL_SECTOR: dict[str, str | None] = {
    "Utilities":              "Energy/Utilities",
    "Fossil Fuels":           "Energy/Utilities",
    "Wine & Spirits":         "Alcohol/Gambling",
    "Tobacco":                "Alcohol/Gambling",
    "Financial Services":     "Finance",
    "Commercial Real Estate": "Housing",
    "Agribusiness":           "Agriculture",
    "Trial Lawyers":          "Criminal Justice",
    "Software & IT":          "Tech/Data",
    "IT & Engineering":       "Tech/Data",
    "Education":              "Education",
    "Healthcare":             "Healthcare",
    "Transportation":         "Transportation",
    "Labor":                  "Labor",
    "Environment":            "Environment",
    # Broad / political buckets — skip as primary; fall back to next sector
    "Ideological":            None,
    "Individual/Other":       None,
    "Retail":                 None,
}

# The set of bill sectors actually produced by SECTOR_KEYWORDS
_BILL_SECTORS = set(SECTOR_KEYWORDS.keys())


def _resolve_donor_sector(top_sector: str, by_sector_json: str) -> str | None:
    """
    Map a CFS top_sector to a bill sector name.
    If the top sector maps to None (Ideological, etc.), walk by_sector_json
    to find the first sector that has a valid mapping.
    """
    mapped = CFS_TO_BILL_SECTOR.get(top_sector)
    if mapped is not None:
        return mapped
    # top_sector is unmappable — try next sectors from the donor breakdown
    try:
        sectors = json.loads(by_sector_json or "[]")
    except Exception:
        return None
    for entry in sectors:
        sec = entry.get("sector", "")
        candidate = CFS_TO_BILL_SECTOR.get(sec)
        if candidate is not None:
            return candidate
    return None


# ── Name normalisation ────────────────────────────────────────────────────────

_SUFFIXES = re.compile(
    r"\b(jr|sr|ii|iii|iv|esq|phd|md|do|dds)\b\.?$", re.I
)


def _norm_name(name: str) -> str:
    """last-name + first-initial key, lower-cased, no punctuation."""
    name = _SUFFIXES.sub("", name).strip()
    parts = re.split(r"[\s,]+", name)
    parts = [p for p in parts if p]
    if not parts:
        return ""
    # Detect "Last, First" format
    if "," in name:
        last = parts[0]
        first_init = parts[1][0] if len(parts) > 1 else ""
    else:
        last = parts[-1]
        first_init = parts[0][0] if parts else ""
    return f"{last.lower()}_{first_init.lower()}"


# ── DB helpers ────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS donor_vote_alignment (
    legislator_id    TEXT PRIMARY KEY,
    name             TEXT,
    party            TEXT,
    chamber          TEXT,
    top_donor_sector TEXT,
    top_sector_amt   REAL,
    sector_yes_rate  REAL,   -- % YES on bills in top-donor sector
    other_yes_rate   REAL,   -- % YES on all other sectored bills
    alignment_delta  REAL,   -- sector_yes_rate - other_yes_rate
    sector_vote_count  INTEGER,
    other_vote_count   INTEGER,
    by_sector_json   TEXT,   -- [{sector, yes, total, yes_rate}]
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_dva_name   ON donor_vote_alignment(name);
CREATE INDEX IF NOT EXISTS idx_dva_sector ON donor_vote_alignment(top_donor_sector);
CREATE INDEX IF NOT EXISTS idx_dva_delta  ON donor_vote_alignment(alignment_delta);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ── Core logic ────────────────────────────────────────────────────────────────

# Floor-passage motion patterns (Senate: "Passage  R", House: starts with "H VOTE:")
_FLOOR_MOTIONS = re.compile(r"^(Passage\s+R|H VOTE:)", re.I)


def _load_contested_votes(
    os_conn: sqlite3.Connection, sessions: list[str]
) -> dict[str, dict[str, dict]]:
    """
    Returns: {bill_id -> {motion -> {voter_name -> 'yes'|'no'}}}
    Only includes motions where both yes ≥ 5 and no ≥ 5 (genuinely contested).
    """
    placeholders = ",".join("?" for _ in sessions)
    rows = os_conn.execute(
        f"""
        SELECT bill_id, motion, voter_name, option
        FROM votes
        WHERE session IN ({placeholders})
          AND option IN ('yes','no')
        """,
        sessions,
    ).fetchall()

    # Accumulate raw
    raw: dict[str, dict[str, dict[str, str]]] = {}
    for bill_id, motion, voter_name, option in rows:
        if not _FLOOR_MOTIONS.match(motion):
            continue
        raw.setdefault(bill_id, {}).setdefault(motion, {})[voter_name] = option

    # Keep only contested motions
    contested: dict[str, dict[str, dict]] = {}
    for bill_id, motions in raw.items():
        for motion, votes in motions.items():
            yeas = sum(1 for v in votes.values() if v == "yes")
            nays = sum(1 for v in votes.values() if v == "no")
            if yeas >= 5 and nays >= 5:
                contested.setdefault(bill_id, {})[motion] = votes

    return contested


def _load_bill_sectors(
    os_conn: sqlite3.Connection, sessions: list[str]
) -> dict[str, str]:
    """Return {bill_id -> sector} for all bills we can classify."""
    placeholders = ",".join("?" for _ in sessions)
    rows = os_conn.execute(
        f"SELECT bill_id, title FROM bills WHERE session IN ({placeholders})",
        sessions,
    ).fetchall()
    return {
        bill_id: sec
        for bill_id, title in rows
        if (sec := classify_bill(title)) is not None
    }


def _load_legislator_donor_sectors(
    polls_conn: sqlite3.Connection,
) -> dict[str, tuple[str, str, str, str, float]]:
    """
    Returns norm_key -> (legislator_id, name, party, chamber, top_sector, top_sector_amt)
    Uses campaign_finance_summary (state SBE source).
    """
    rows = polls_conn.execute(
        """
        SELECT legislator_id, name, party, chamber, top_sector,
               total_raised, by_sector_json
        FROM campaign_finance_summary
        WHERE source = 'va_sbe' AND by_sector_json IS NOT NULL
        """
    ).fetchall()

    result: dict[str, tuple] = {}
    for leg_id, name, party, chamber, top_sector, total_raised, bsj in rows:
        if not name or not top_sector:
            continue
        # Map CFS sector name → bill sector name (fall back through by_sector_json)
        bill_sector = _resolve_donor_sector(top_sector, bsj or "[]")
        if bill_sector is None:
            continue  # no mappable sector — skip this legislator
        try:
            sectors = json.loads(bsj)
            top_amt = sectors[0]["total"] if sectors else 0.0
        except Exception:
            top_amt = 0.0
        key = _norm_name(name)
        result[key] = (leg_id, name, party, chamber, bill_sector, top_amt)
    return result


def _compute_alignment(
    voter_name: str,
    top_donor_sector: str,
    contested_votes: dict[str, dict[str, dict]],
    bill_sectors: dict[str, str],
) -> dict | None:
    """
    For a single legislator, compute their YES rates in each sector
    and the alignment_delta for their top donor sector.
    """
    # Accumulate per-sector yes/total counts
    # Use (bill_id, motion) as the unit so we don't double-count
    sector_tallies: dict[str, list[int]] = {}  # sector -> [yes_count, total_count]

    seen = set()
    for bill_id, motions in contested_votes.items():
        sector = bill_sectors.get(bill_id)
        if sector is None:
            continue
        for motion, votes in motions.items():
            key = (bill_id, motion)
            if key in seen:
                continue
            seen.add(key)
            if voter_name not in votes:
                continue
            tally = sector_tallies.setdefault(sector, [0, 0])
            tally[1] += 1
            if votes[voter_name] == "yes":
                tally[0] += 1

    if not sector_tallies:
        return None

    # Build per-sector summary
    by_sector = []
    for sec, (yes, total) in sorted(sector_tallies.items()):
        by_sector.append({
            "sector": sec,
            "yes": yes,
            "total": total,
            "yes_rate": round(yes / total * 100, 1) if total else None,
        })

    # Sector vs non-sector tallies
    sec_yes = sec_total = oth_yes = oth_total = 0
    for entry in by_sector:
        if entry["sector"] == top_donor_sector:
            sec_yes   += entry["yes"]
            sec_total += entry["total"]
        else:
            oth_yes   += entry["yes"]
            oth_total += entry["total"]

    if sec_total == 0:
        return None  # no votes in their top sector — can't compute

    sector_yes_rate = round(sec_yes / sec_total * 100, 1)
    other_yes_rate  = round(oth_yes  / oth_total  * 100, 1) if oth_total else None
    alignment_delta = (
        round(sector_yes_rate - other_yes_rate, 1)
        if other_yes_rate is not None
        else None
    )

    return {
        "sector_yes_rate":  sector_yes_rate,
        "other_yes_rate":   other_yes_rate,
        "alignment_delta":  alignment_delta,
        "sector_vote_count": sec_total,
        "other_vote_count":  oth_total,
        "by_sector":         by_sector,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main(sessions: list[str]) -> None:
    print(f"Sessions: {sessions}")

    os_conn = sqlite3.connect(OS_DB)
    polls_conn = sqlite3.connect(POLLS_DB, timeout=30)
    polls_conn.execute("PRAGMA journal_mode=WAL")
    _ensure_table(polls_conn)

    print("Loading contested floor votes...")
    contested = _load_contested_votes(os_conn, sessions)
    print(f"  {sum(len(m) for m in contested.values()):,} contested motions on "
          f"{len(contested):,} bills")

    print("Classifying bills by sector...")
    bill_sectors = _load_bill_sectors(os_conn, sessions)
    covered = sum(1 for b in contested if b in bill_sectors)
    print(f"  {len(bill_sectors):,} bills classified; "
          f"{covered:,} of {len(contested):,} contested bills have a sector")

    print("Loading legislator donor profiles...")
    donor_map = _load_legislator_donor_sectors(polls_conn)
    print(f"  {len(donor_map):,} legislators with donor sector data")

    now = datetime.now(timezone.utc).isoformat()
    inserted = skipped_no_votes = skipped_no_match = 0

    # Get unique voter names from contested votes
    all_voters: set[str] = set()
    for motions in contested.values():
        for votes in motions.values():
            all_voters.update(votes.keys())

    print(f"Computing alignment for {len(all_voters):,} unique voters...")

    for voter_name in sorted(all_voters):
        norm = _norm_name(voter_name)
        if norm not in donor_map:
            skipped_no_match += 1
            continue

        leg_id, name, party, chamber, top_sector, top_amt = donor_map[norm]

        result = _compute_alignment(voter_name, top_sector, contested, bill_sectors)
        if result is None:
            skipped_no_votes += 1
            continue

        polls_conn.execute(
            """
            INSERT INTO donor_vote_alignment
                (legislator_id, name, party, chamber,
                 top_donor_sector, top_sector_amt,
                 sector_yes_rate, other_yes_rate, alignment_delta,
                 sector_vote_count, other_vote_count,
                 by_sector_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(legislator_id) DO UPDATE SET
                name             = excluded.name,
                party            = excluded.party,
                chamber          = excluded.chamber,
                top_donor_sector = excluded.top_donor_sector,
                top_sector_amt   = excluded.top_sector_amt,
                sector_yes_rate  = excluded.sector_yes_rate,
                other_yes_rate   = excluded.other_yes_rate,
                alignment_delta  = excluded.alignment_delta,
                sector_vote_count= excluded.sector_vote_count,
                other_vote_count = excluded.other_vote_count,
                by_sector_json   = excluded.by_sector_json,
                updated_at       = excluded.updated_at
            """,
            (
                leg_id, name, party, chamber,
                top_sector, top_amt,
                result["sector_yes_rate"],
                result["other_yes_rate"],
                result["alignment_delta"],
                result["sector_vote_count"],
                result["other_vote_count"],
                json.dumps(result["by_sector"]),
                now,
            ),
        )
        inserted += 1

    polls_conn.commit()
    os_conn.close()

    total = polls_conn.execute(
        "SELECT COUNT(*) FROM donor_vote_alignment"
    ).fetchone()[0]
    polls_conn.close()

    print(f"\nDone.")
    print(f"  Inserted/updated: {inserted}")
    print(f"  Skipped (no donor match): {skipped_no_match}")
    print(f"  Skipped (no sector votes): {skipped_no_votes}")
    print(f"  Total rows in donor_vote_alignment: {total}")

    # Quick summary
    _print_summary()


def _print_summary() -> None:
    conn = sqlite3.connect(POLLS_DB)
    print("\nTop 10 most aligned (votes YES on donor-sector bills most above average):")
    rows = conn.execute("""
        SELECT name, party, chamber, top_donor_sector,
               sector_yes_rate, other_yes_rate, alignment_delta,
               sector_vote_count
        FROM donor_vote_alignment
        WHERE alignment_delta IS NOT NULL AND sector_vote_count >= 5
        ORDER BY alignment_delta DESC LIMIT 10
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:<28} {r[1][:1]} {r[2][:3]} | {r[3]:<22} "
              f"sector:{r[4]:5.1f}% other:{r[5]:5.1f}% delta:+{r[6]:.1f}pp "
              f"(n={r[7]})")

    print("\nTop 10 most opposed (votes YES on donor-sector bills LESS than average):")
    rows2 = conn.execute("""
        SELECT name, party, chamber, top_donor_sector,
               sector_yes_rate, other_yes_rate, alignment_delta,
               sector_vote_count
        FROM donor_vote_alignment
        WHERE alignment_delta IS NOT NULL AND sector_vote_count >= 5
        ORDER BY alignment_delta ASC LIMIT 10
    """).fetchall()
    for r in rows2:
        print(f"  {r[0]:<28} {r[1][:1]} {r[2][:3]} | {r[3]:<22} "
              f"sector:{r[4]:5.1f}% other:{r[5]:5.1f}% delta:{r[6]:.1f}pp "
              f"(n={r[7]})")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions", nargs="+", default=["2024", "2025", "2026"],
        help="OpenStates session years to include"
    )
    args = parser.parse_args()
    main(args.sessions)
