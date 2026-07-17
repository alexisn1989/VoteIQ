"""
build_norfolk_finance.py
Classify Norfolk City Council SBE contributions by employer sector and write
norfolk_finance_totals to polls.db.

Usage:
    python build_norfolk_finance.py
    python build_norfolk_finance.py --reset
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"

# Canonical SBE filters for each Norfolk council member.
# Using first+last fragments avoids the "Thomas" false-positive problem.
# locality='Norfolk' is applied in the query.
MEMBER_SBE_FILTERS: dict[str, dict] = {
    "Alexander": {"first": "Kenneth",  "last": "Alexander",  "office_substr": "Mayor"},
    "Doyle":     {"first": "Courtney", "last": "Doyle",      "office_substr": "Council"},
    "Johnson":   {"first": "Mamie",    "last": "Johnson",    "office_substr": "Council"},
    "Smigiel":   {"first": "Smigiel",  "last": "Smigiel",   "office_substr": "Council"},
    "Thomas":    {"first": "Martin",   "last": "Thomas",     "office_substr": "Council"},
    "Paige":     {"first": "John",     "last": "Paige",      "office_substr": "Council"},
    "McGee":     {"first": "Jeremy",   "last": "McGee",      "office_substr": "Council"},
    "Clanton":   {"first": "Carlos",   "last": "Clanton",    "office_substr": "Council"},
    "Royster":   {"first": "Danica",   "last": "Royster",    "office_substr": "Council"},
}

# Employer → sector keyword mapping (checked in order; first match wins)
SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Real Estate",   ["realt", "develop", "construct", "proper", "build", "homebuil",
                       "architect", "contractor", "land ", "land,", "rental", "housing",
                       "tidewater builder", "franklin johnston", "bonaventure",
                       "pathway realty", "gold key"]),
    ("Hospitality",   ["hotel", "resort", "restaur", "food", "beverage", "hospitality",
                       "tourism", "shamin", "norfolk hotel", "marriott", "hilton"]),
    ("Finance",       ["bank", "financ", "invest", "capital", "credit union", "mortgage",
                       "insur", "wealth", "fund", "securities", "lending", "asset"]),
    ("Legal",         ["law ", "attorney", "legal", "counsel", " llp", " pllc", "esq",
                       " p.c.", "firm"]),
    ("Healthcare",    ["health", "hospital", "medic", "doctor", "physician", "nurs",
                       "pharma", "clinic", "dental", "care ", "sentara", "bon secours"]),
    ("Defense",       ["defense", "military", "navy", "army", "coast guard", "veteran",
                       "naval", "bae systems", "northrop", "general dynamics", "raytheon",
                       "leidos", "booz allen", "saic", "huntington ingalls"]),
    ("Education",     ["school", "educat", "universit", "college", "teacher",
                       "professor", "academ", "old dominion", "odu", "nsu",
                       "norfolk state"]),
    ("Government",    ["city of", "county of", "state of ", "federal ", "govern",
                       "municipal", "dept of", "department of", "public sector"]),
    ("Energy",        ["energy", "utility", "electric", "gas ", "power ", "dominion",
                       "appalachian", "columbia gas"]),
    ("Tech",          ["tech", "software", "data ", "cyber", "digital", "computer",
                       "information tech", " it ", "systems integr"]),
    ("Retired",       ["retired", "retirement"]),
]


def classify_sector(employer: str, occupation: str) -> str:
    text = (employer or "").lower().strip()
    if not text or text in ("n/a", "na", "none", "self", "individual"):
        text = (occupation or "").lower().strip()
    for sector, keywords in SECTOR_KEYWORDS:
        if any(kw in text for kw in keywords):
            return sector
    # occupation fallback
    occ = (occupation or "").lower()
    if any(kw in occ for kw in ["attorney", "lawyer", "legal"]):
        return "Legal"
    if any(kw in occ for kw in ["realtor", "broker", "developer", "contractor"]):
        return "Real Estate"
    if any(kw in occ for kw in ["physician", "doctor", "nurse", "pharmacist"]):
        return "Healthcare"
    if any(kw in occ for kw in ["banker", "accountant", "cpa", "financial advisor"]):
        return "Finance"
    if any(kw in occ for kw in ["retired"]):
        return "Retired"
    return "Other"


def fetch_member_contributions(conn: sqlite3.Connection, member: str) -> list[dict]:
    f = MEMBER_SBE_FILTERS[member]
    rows = conn.execute("""
        SELECT contributor_first, contributor_last, employer, occupation,
               amount, transaction_date, election_cycle
        FROM sbe_local_contributions
        WHERE locality = 'Norfolk'
          AND LOWER(candidate_name) LIKE LOWER(?)
          AND LOWER(candidate_name) LIKE LOWER(?)
          AND LOWER(office_sought) LIKE LOWER(?)
          AND amount > 0
    """, (
        f"%{f['first']}%",
        f"%{f['last']}%",
        f"%{f['office_substr']}%",
    )).fetchall()
    return [
        {
            "contributor": f"{r[0] or ''} {r[1] or ''}".strip(),
            "employer": r[2] or "",
            "occupation": r[3] or "",
            "amount": float(r[4]),
            "date": r[5],
            "cycle": r[6],
            "sector": classify_sector(r[2], r[3]),
        }
        for r in rows
    ]


def compute_sector_totals(contribs: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for c in contribs:
        s = c["sector"]
        if s not in totals:
            totals[s] = {"sector": s, "total_amount": 0.0, "donor_count": 0}
        totals[s]["total_amount"] += c["amount"]
        totals[s]["donor_count"] += 1
    grand = sum(t["total_amount"] for t in totals.values()) or 1
    result = sorted(totals.values(), key=lambda x: -x["total_amount"])
    for t in result:
        t["pct_of_total"] = round(100.0 * t["total_amount"] / grand, 1)
    return result


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_finance_totals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            member_name  TEXT NOT NULL,
            sector       TEXT NOT NULL,
            total_amount REAL,
            donor_count  INTEGER,
            pct_of_total REAL,
            updated_at   TEXT DEFAULT (datetime('now')),
            UNIQUE(member_name, sector)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norfolk_finance_summary (
            member_name  TEXT PRIMARY KEY,
            total_raised REAL,
            top_sector   TEXT,
            top_sector_amt  REAL,
            top_sector_pct  REAL,
            sector_json  TEXT,   -- [{sector, total_amount, pct_of_total, donor_count}]
            updated_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="Clear and recompute all finance totals")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_table(conn)

    if args.reset:
        conn.execute("DELETE FROM norfolk_finance_totals")
        conn.execute("DELETE FROM norfolk_finance_summary")
        conn.commit()
        print("Cleared existing finance totals.")

    now = datetime.now(timezone.utc).isoformat()
    for member, _ in MEMBER_SBE_FILTERS.items():
        contribs = fetch_member_contributions(conn, member)
        if not contribs:
            print(f"  {member}: no contributions found (check name filter)")
            continue
        totals = compute_sector_totals(contribs)
        total_raised = sum(c["amount"] for c in contribs)
        top = totals[0] if totals else {}

        for t in totals:
            conn.execute("""
                INSERT OR REPLACE INTO norfolk_finance_totals
                    (member_name, sector, total_amount, donor_count, pct_of_total, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (member, t["sector"], t["total_amount"], t["donor_count"],
                  t["pct_of_total"], now))

        conn.execute("""
            INSERT OR REPLACE INTO norfolk_finance_summary
                (member_name, total_raised, top_sector, top_sector_amt, top_sector_pct,
                 sector_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            member, total_raised,
            top.get("sector", "Other"),
            top.get("total_amount", 0),
            top.get("pct_of_total", 0),
            json.dumps(totals),
            now,
        ))
        conn.commit()

        print(f"  {member}: {len(contribs)} contributions, ${total_raised:,.0f} total")
        for t in totals[:5]:
            print(f"    {t['sector']:<18} ${t['total_amount']:>9,.0f}  ({t['pct_of_total']:>5.1f}%,  {t['donor_count']} donors)")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
