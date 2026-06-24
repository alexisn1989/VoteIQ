"""
build_vb_finance.py
Classify Virginia Beach City Council SBE contributions by employer sector
and write vb_finance_totals + vb_finance_summary to polls.db.

Usage:
    python build_vb_finance.py
    python build_vb_finance.py --reset
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "polls.db"

# Canonical SBE name filters per member.
# locality='Virginia Beach' applied in query.
MEMBER_SBE_FILTERS: dict[str, dict] = {
    "Berlucchi":     {"first": "Michael",  "last": "Berlucchi",     "office_substr": "Council"},
    "Dyer":          {"first": "Robert",   "last": "Dyer",          "office_substr": "Mayor"},
    "Henley":        {"first": "Barbara",  "last": "Henley",        "office_substr": "Council"},
    "Hutcheson":     {"first": "David",    "last": "Hutcheson",     "office_substr": "Council"},
    "Wilson":        {"first": "Rosemary", "last": "Wilson",        "office_substr": "Council"},
    "Remick":        {"first": "Robert",   "last": "Remick",        "office_substr": "Council"},
    "Ross-Hammond":  {"first": "Amelia",   "last": "Ross-Hammond",  "office_substr": "Council"},
    "Schulman":      {"first": "Joashua",  "last": "Schulman",      "office_substr": "Council"},
    "Rouse":         {"first": "Jennifer", "last": "Rouse",         "office_substr": "Council"},
    "Cummings":      {"first": "Stacy",    "last": "Cummings",      "office_substr": "Council"},
    "Jackson-Green": {"first": "Calvern",  "last": "Jackson-Green", "office_substr": "Council"},
}

# Employer → sector keyword mapping (checked in order; first match wins)
SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Real Estate",   ["realt", "develop", "devlop", "construct", "proper", "build", "homebuil",
                       "architect", "contractor", "land ", "land,", "rental", "housing",
                       "farm", "agriculture", "agric", "abatement", "roofing",
                       "real estate", "apartment", "commercial real estate",
                       "residential", "property management", "sifen", "ainslie",
                       "heatwole", "armada hoffler", "lawson compan", "title compan"]),
    ("Hospitality",   ["hotel", "motel", "resort", "restaur", "restobar", "food", "beverage",
                       "hospitality", "tourism", "marriott", "hilton", "oceanfront", "gold key",
                       "shamin", "city club", "events llc", "event venue", "marina",
                       "events unlimited", "pashm"]),
    ("Finance",       ["bank", "financ", "invest", "capital", "credit union", "mortgage",
                       "insur", "assurance", "wealth", "fund", "securities", "lending", "asset",
                       " trust"]),
    ("Legal",         ["law ", "attorn", "legal", "counsel", " llp", " pllc", "esq",
                       " p.c.", " pc", "firm", "lawyer"]),
    ("Healthcare",    ["health", "hospital", "medic", "doctor", "physician", "nurs",
                       "pharma", "clinic", "dental", "care ", "sentara", "bon secours",
                       "optom", "ophthalm", "opthalm", "eye ", "eye consult",
                       "chiropr", "psychotherapy", "psychiatr", "therapist", "surgical"]),
    ("Defense",       ["defense", "military", "navy", "army", "coast guard", "veteran",
                       "naval", "ship repair", "bae systems", "northrop", "general dynamics",
                       "raytheon", "leidos", "booz allen", "saic", "huntington ingalls", "l3"]),
    ("Education",     ["school", "educat", "universit", "college", "teacher",
                       "professor", "academ", "old dominion", "odu", "regent",
                       "tidewater community", "tcc", "ecpi", "aviation institute"]),
    ("Labor",         ["union ", " union", "teamster", "fire fighter", "firefighter",
                       "labor union", "afl-cio", "ibew ", "ufcw ", "seiu "]),
    ("Government",    ["city of", "county of", "state of ", "federal ", "govern",
                       "municipal", "dept of", "department of", "public sector",
                       "city council", "town council", "internal revenue", "sheriff"]),
    ("Energy",        ["energy", "utility", "electric", "gas ", "power ", "dominion",
                       "appalachian", "columbia gas", "papco", "petroleum", "fuel distrib"]),
    ("Tech",          ["tech", "software", "data ", "cyber", "digital", "computer",
                       "information tech", " it ", "systems integr"]),
    ("Automotive",    ["automobile", "automotive", "car dealer", "auto dealer",
                       "checkered flag", "priority toyota", "priority hyundai",
                       "priority auto", "beach ford", "auto group",
                       "hyundai", "subaru", "mazda dealer", "kia dealer"]),
    ("PAC/Advocacy",  ["pac ", "political action", "campaign committee", "advocacy org",
                       "lobby org", "political org", "political committee",
                       "politcal action", "candidate committee",
                       "democrat", "republican", "national committee", "party committee",
                       "political activ"]),
    ("Retired",       ["retired", "retirement", "not employed", "not employ"]),
]


# Exact employer names too short/ambiguous for safe substring matching
_EXACT_EMPLOYER: dict[str, str] = {
    "phr":              "Hospitality",  # Gold Key | PHR, Virginia Beach hospitality group
    "globalinx":        "Tech",         # Globalinx — IT firm, Wilson donor
    "lynnhaven marine": "Automotive",   # Lynnhaven Marine — boat dealer, Wilson donor
    "meb":              "Real Estate",  # MEB General Contractors, Virginia Beach
    "rk auto":          "Automotive",   # RK Auto Group, Virginia Beach
    "757 angels":       "Finance",      # 757 Angels — VB angel investor group
    "cox communications": "Tech",       # Cox Communications — cable/telecom
    "inter-op.net":     "Tech",         # INTER-OP.NET — IT/networking firm
    "gates management co": "Real Estate",  # Gates Management — property management
    "riggins company":  "Defense",      # Riggins Co — metal fabricator serving Navy/NASA
    "riggins co":       "Defense",      # short form
    "ems industrial":   "Defense",      # EMS Industrial — Virginia Ship Repair member
    "xkig":             "Energy",       # XKIG — national utility/power infrastructure
    "bruce smith enterp": "Real Estate",  # Bruce Smith Enterprise — VB commercial RE developer
    "neighborhood harvest": "Hospitality",  # The Neighborhood Harvest — VB food delivery
}


def classify_sector(employer: str, occupation: str) -> str:
    text = (employer or "").lower().strip()
    if text in _EXACT_EMPLOYER:
        return _EXACT_EMPLOYER[text]
    if not text or text in ("n/a", "na", "none", "self", "individual", "self-employed",
                             "sell-employed", "self employed", "homemaker"):
        text = (occupation or "").lower().strip()
    for sector, keywords in SECTOR_KEYWORDS:
        if any(kw in text for kw in keywords):
            return sector
    # occupation fallback — checked only when employer-based path yields no match
    occ = (occupation or "").lower().strip()
    if any(kw in occ for kw in ["attorney", "lawyer", "legal"]):
        return "Legal"
    if any(kw in occ for kw in ["realtor", "broker", "developer", "contractor", "farmer",
                                  "real estate", "apartment", "property manager",
                                  "property management", "residential company",
                                  "homebuil", "agric", "title ", "builder",
                                  "civil engineer", "construct"]):
        return "Real Estate"
    if any(kw in occ for kw in ["physician", "doctor", "nurse", "pharmacist",
                                  "psychiatrist", "psychotherap", "therapist", "biostat",
                                  "home care", "surgical"]):
        return "Healthcare"
    if any(kw in occ for kw in ["banker", "accountant", "cpa", "financial advisor",
                                  "investment advisor", "insur"]):
        return "Finance"
    if any(kw in occ for kw in ["hotel", "motel", "hospitality", "restaur",
                                  "event organiz", "bar service"]):
        return "Hospitality"
    if any(kw in occ for kw in ["car dealer", "auto dealer", "automobile", "automotive",
                                  "car sales", "car dealership", "boat dealer"]):
        return "Automotive"
    if any(kw in occ for kw in ["union", "teamster", "firefighter", "fire fighter"]):
        return "Labor"
    if any(kw in occ for kw in ["city council", "town council", "county supervisor",
                                  "county board", "state senator", "state delegate",
                                  "senator", "governor"]):
        return "Government"
    if any(kw in occ for kw in ["navy", "army", " air force", "coast guard", "military",
                                  "veteran", "soldier", "sailor", "airman", "ship repair"]):
        return "Defense"
    if (any(kw in occ for kw in ["political action", "political committee",
                                   "candidate committee", "political org", "advocacy",
                                   "national committee", "democrat", "republican",
                                   "political activ"])
            or occ in ("political", "pac")):
        return "PAC/Advocacy"
    if any(kw in occ for kw in ["information technology", "software engineer",
                                  "it director", "it manager"]):
        return "Tech"
    if "retired" in occ or "not employ" in occ or occ == "homemaker":
        return "Retired"
    return "Other"


def fetch_member_contributions(conn: sqlite3.Connection, member: str) -> list[dict]:
    f = MEMBER_SBE_FILTERS[member]
    rows = conn.execute("""
        SELECT contributor_first, contributor_last, employer, occupation,
               amount, transaction_date, election_cycle
        FROM sbe_local_contributions
        WHERE locality = 'Virginia Beach'
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
            "employer":    r[2] or "",
            "occupation":  r[3] or "",
            "amount":      float(r[4]),
            "date":        r[5],
            "cycle":       r[6],
            "sector":      classify_sector(r[2], r[3]),
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


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vb_finance_totals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            member_name  TEXT NOT NULL,
            sector       TEXT NOT NULL,
            total_amount REAL,
            donor_count  INTEGER,
            pct_of_total REAL,
            updated_at   TEXT DEFAULT (datetime('now')),
            UNIQUE(member_name, sector)
        );
        CREATE TABLE IF NOT EXISTS vb_finance_summary (
            member_name     TEXT PRIMARY KEY,
            total_raised    REAL,
            top_sector      TEXT,
            top_sector_amt  REAL,
            top_sector_pct  REAL,
            sector_json     TEXT,
            updated_at      TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="Clear and recompute all finance totals")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_tables(conn)

    if args.reset:
        conn.execute("DELETE FROM vb_finance_totals")
        conn.execute("DELETE FROM vb_finance_summary")
        conn.commit()
        print("Cleared existing finance totals.")

    now = datetime.now(timezone.utc).isoformat()
    for member in MEMBER_SBE_FILTERS:
        contribs = fetch_member_contributions(conn, member)
        if not contribs:
            print(f"  {member}: no contributions found")
            continue
        totals = compute_sector_totals(contribs)
        total_raised = sum(c["amount"] for c in contribs)
        top = totals[0] if totals else {}

        for t in totals:
            conn.execute("""
                INSERT OR REPLACE INTO vb_finance_totals
                    (member_name, sector, total_amount, donor_count, pct_of_total, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (member, t["sector"], t["total_amount"],
                  t["donor_count"], t["pct_of_total"], now))

        conn.execute("""
            INSERT OR REPLACE INTO vb_finance_summary
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

        print(f"  {member}: {len(contribs)} contributions  ${total_raised:,.0f} total")
        for t in totals[:5]:
            print(f"    {t['sector']:<18} ${t['total_amount']:>9,.0f}  "
                  f"({t['pct_of_total']:>5.1f}%,  {t['donor_count']} donors)")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
