#!/usr/bin/env python3
"""
Pull FEC Schedule A itemized contributions, group donors by employer sector,
store results in local SQLite, and optionally ask Claude for a correlation report.

Examples:
    python fec_employer_pipeline.py --candidate "Jennifer Kiggans" --cycle 2024 --no-claude
    python fec_employer_pipeline.py --candidate "Mark Warner" --cycle 2024 --report
    python fec_employer_pipeline.py --all --cycle 2024 --no-claude
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
FEC_API_BASE = "https://api.open.fec.gov/v1"
USER_AGENT = "VoteIQ/1.0 (Virginia campaign finance research)"
CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
CLAUDE_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "ANTHROPIC_FALLBACK_MODELS",
        "claude-sonnet-4-6,claude-opus-4-5-20251101,claude-haiku-4-5-20251001",
    ).split(",")
    if model.strip()
]

def _load_virginia_candidates() -> dict[str, str]:
    """Load Virginia delegation members from congress_members + bioguide_fec_bridge."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT c.name, b.fec_candidate_id
            FROM congress_members c
            JOIN bioguide_fec_bridge b ON c.bioguide_id = b.bioguide_id
            WHERE c.state = 'Virginia' AND b.fec_candidate_id IS NOT NULL
            ORDER BY c.name
        """).fetchall()
        return {row["name"]: row["fec_candidate_id"] for row in rows}
    finally:
        conn.close()

VIRGINIA_CANDIDATES = _load_virginia_candidates()

EMPLOYER_SECTOR_MAP = {
    # Defense & Security
    "huntington ingalls": "Defense",
    "huntington ingalls industries": "Defense",
    "hii": "Defense",
    "booz allen": "Defense",
    "raytheon": "Defense",
    "lockheed martin": "Defense",
    "general dynamics": "Defense",
    "northrop grumman": "Defense",
    "leidos": "Defense",
    "saic": "Defense",
    "ita international": "Defense",
    "patriot group international": "Defense",
    "london bridge trading": "Defense",
    "clearpath action": "Defense",
    "jo-kell": "Defense",
    # Finance & Investment
    "bank of america": "Finance",
    "wells fargo": "Finance",
    "jpmorgan": "Finance",
    "goldman sachs": "Finance",
    "morgan stanley": "Finance",
    "navy federal": "Finance",
    "usaa": "Finance",
    "blackstone": "Finance",
    "kinsale": "Finance",
    "elliott investment": "Finance",
    "suburban capital": "Finance",
    "chain bridge": "Finance",
    # Real Estate
    "real estate": "Real Estate",
    "realty": "Real Estate",
    "housing inc": "Real Estate",
    "land title": "Real Estate",
    "cumberland development": "Real Estate",
    "hercules real estate": "Real Estate",
    "kw metro": "Real Estate",
    "schaubach": "Real Estate",
    "philips international": "Real Estate",
    "cottrell contracting": "Construction",
    "jes foundation": "Construction",
    "c & m industries": "Manufacturing",
    # Healthcare
    "sentara": "Healthcare",
    "bon secours": "Healthcare",
    "childrens hospital": "Healthcare",
    "children's hospital": "Healthcare",
    "vcu health": "Healthcare",
    # Energy
    "dominion energy": "Energy",
    "dominion": "Energy",
    "exxon": "Energy",
    "chevron": "Energy",
    "holtzman oil": "Energy",
    # Legal
    "attorney": "Legal",
    "law firm": "Legal",
    "esquire": "Legal",
    "kirkland": "Legal",
    "elaw": "Legal",
    # Technology
    "amazon": "Technology",
    "google": "Technology",
    "microsoft": "Technology",
    "oracle": "Technology",
    # Lobbying & Consulting
    "the lindsey group": "Lobbying",
    "chesapeake management": "Consulting",
    "mjc & associates": "Consulting",
    "newmarket corp": "Manufacturing",
    # Automotive & Retail
    "loyalty automotive": "Automotive",
    "charles barker": "Automotive",
    # Hospitality
    "gold key": "Hospitality",
    "kelly's": "Hospitality",
    # Government
    "commonwealth of virginia": "Government",
    # Advocacy / Think Tank
    "a.f.p.i.": "Advocacy",
    "afpi": "Advocacy",
    # Construction
    "construction": "Construction",
    # Education
    "university": "Education",
    "college": "Education",
    "school": "Education",
    # Status
    "retired": "Retired",
    "self-employed": "Self-Employed",
    "self employed": "Self-Employed",
    "homemaker": "Homemaker",
}

COMMITTEE_ASSIGNMENTS = {
    "H2VA02064": [
        "Armed Services",
        "Natural Resources",
        "Veterans' Affairs",
    ],
    "S6VA00093": [
        "Banking, Housing, and Urban Affairs",
        "Finance",
        "Rules and Administration",
        "Senate Select Committee on Intelligence",
        "Budget",
    ],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_print(text: str = "") -> None:
    encoding = sys.stdout.encoding or "utf-8"
    print(str(text).encode(encoding, errors="replace").decode(encoding, errors="replace"))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fec_individual_contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fec_sub_id TEXT UNIQUE,
            candidate_id TEXT NOT NULL,
            candidate_name TEXT NOT NULL,
            contributor_name TEXT,
            contributor_employer TEXT,
            contributor_occupation TEXT,
            employer_sector TEXT,
            amount REAL,
            contribution_date TEXT,
            city TEXT,
            state TEXT,
            cycle INTEGER,
            raw_json TEXT,
            fetched_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_fec_contrib_candidate_cycle
            ON fec_individual_contributions(candidate_id, cycle);
        CREATE INDEX IF NOT EXISTS idx_fec_contrib_sector
            ON fec_individual_contributions(employer_sector);

        CREATE TABLE IF NOT EXISTS candidate_sector_totals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            candidate_name TEXT NOT NULL,
            sector TEXT NOT NULL,
            total_amount REAL,
            donor_count INTEGER,
            cycle INTEGER,
            updated_at TEXT NOT NULL,
            UNIQUE(candidate_id, sector, cycle)
        );

        CREATE TABLE IF NOT EXISTS candidate_finance_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            candidate_name TEXT NOT NULL,
            cycle INTEGER,
            report TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(candidate_id, cycle)
        );
    """)
    conn.commit()


def normalize_employer(raw: str | None) -> str:
    if not raw:
        return "Unknown"
    return " ".join(str(raw).strip().lower().split()) or "Unknown"


def map_to_sector(employer: str | None) -> str:
    emp_lower = normalize_employer(employer)
    for keyword, sector in EMPLOYER_SECTOR_MAP.items():
        if keyword in emp_lower:
            return sector
    return "Other"


def fetch_authorized_committees(candidate_id: str, cycle: int, api_key: str) -> list[dict]:
    response = requests.get(
        f"{FEC_API_BASE}/candidate/{candidate_id}/committees/history/{cycle}/",
        params={"api_key": api_key, "per_page": 100},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    committees = response.json().get("results", [])
    authorized = [
        committee for committee in committees
        if committee.get("designation") in {"P", "A"}
        and committee.get("committee_id")
    ]
    if not authorized:
        response = requests.get(
            f"{FEC_API_BASE}/candidate/{candidate_id}/committees/",
            params={"api_key": api_key, "per_page": 100},
            headers={"User-Agent": USER_AGENT},
            timeout=60,
        )
        response.raise_for_status()
        authorized = [
            committee for committee in response.json().get("results", [])
            if committee.get("designation") in {"P", "A"}
            and committee.get("committee_id")
        ]
    return authorized


def fetch_schedule_a(
    committee_id: str,
    cycle: int,
    api_key: str,
    limit_pages: int | None = None,
    page_sleep: float = 4.0,
) -> list[dict]:
    all_results: list[dict] = []
    page = 1
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    while True:
        params = {
            "api_key": api_key,
            "committee_id": committee_id,
            "two_year_transaction_period": cycle,
            "per_page": 100,
            "page": page,
            "sort": "-contribution_receipt_amount",
            "is_individual": "true",
        }
        for attempt in range(8):
            try:
                response = session.get(
                    f"{FEC_API_BASE}/schedules/schedule_a/",
                    params=params,
                    timeout=90,
                )
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                wait = 2 ** attempt * 5
                print(f"  timeout on page {page} — waiting {wait}s and retrying")
                time.sleep(wait)
                continue
            if response.status_code == 429:
                wait = 2 ** attempt * 10
                print(f"  rate limited — waiting {wait}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        else:
            raise RuntimeError(f"FEC API failed after 8 retries on page {page}")
        data = response.json()
        results = data.get("results", [])
        if not results:
            break

        all_results.extend(results)
        pagination = data.get("pagination", {})
        print(f"  fetched page {page}: {len(all_results)} total")

        if page >= int(pagination.get("pages") or 1):
            break
        if limit_pages and page >= limit_pages:
            break
        page += 1
        time.sleep(page_sleep)

    return all_results


def insert_contributions(
    conn: sqlite3.Connection,
    candidate_name: str,
    candidate_id: str,
    contributions: Iterable[dict],
    cycle: int,
) -> int:
    written = 0
    fetched_at = now_iso()
    for contrib in contributions:
        employer = contrib.get("contributor_employer") or ""
        sector = map_to_sector(employer)
        fec_sub_id = str(contrib.get("sub_id") or "").strip() or None
        amount = float(contrib.get("contribution_receipt_amount") or 0)
        line_number = str(contrib.get("line_number") or "")
        if line_number and not line_number.startswith("11A"):
            continue
        if amount <= 0:
            continue

        conn.execute("""
            INSERT INTO fec_individual_contributions (
                fec_sub_id, candidate_id, candidate_name, contributor_name,
                contributor_employer, contributor_occupation, employer_sector,
                amount, contribution_date, city, state, cycle, raw_json, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fec_sub_id) DO UPDATE SET
                candidate_name=excluded.candidate_name,
                contributor_name=excluded.contributor_name,
                contributor_employer=excluded.contributor_employer,
                contributor_occupation=excluded.contributor_occupation,
                employer_sector=excluded.employer_sector,
                amount=excluded.amount,
                contribution_date=excluded.contribution_date,
                city=excluded.city,
                state=excluded.state,
                cycle=excluded.cycle,
                raw_json=excluded.raw_json,
                fetched_at=excluded.fetched_at
        """, (
            fec_sub_id,
            candidate_id,
            candidate_name,
            contrib.get("contributor_name") or "",
            employer,
            contrib.get("contributor_occupation") or "",
            sector,
            amount,
            contrib.get("contribution_receipt_date") or "",
            contrib.get("contributor_city") or "",
            contrib.get("contributor_state") or "",
            cycle,
            json.dumps(contrib),
            fetched_at,
        ))
        written += 1

    conn.commit()
    return written


def build_sector_totals(
    conn: sqlite3.Connection,
    candidate_id: str,
    candidate_name: str,
    cycle: int,
) -> int:
    rows = conn.execute("""
        SELECT employer_sector, SUM(amount) AS total, COUNT(*) AS donors
        FROM fec_individual_contributions
        WHERE candidate_id = ? AND cycle = ?
        GROUP BY employer_sector
        ORDER BY total DESC
    """, (candidate_id, cycle)).fetchall()

    for row in rows:
        conn.execute("""
            INSERT INTO candidate_sector_totals (
                candidate_id, candidate_name, sector, total_amount,
                donor_count, cycle, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, sector, cycle) DO UPDATE SET
                candidate_name=excluded.candidate_name,
                total_amount=excluded.total_amount,
                donor_count=excluded.donor_count,
                updated_at=excluded.updated_at
        """, (
            candidate_id,
            candidate_name,
            row["employer_sector"],
            row["total"],
            row["donors"],
            cycle,
            now_iso(),
        ))

    conn.commit()
    return len(rows)


def get_sector_totals(conn: sqlite3.Connection, candidate_id: str, cycle: int) -> list[tuple]:
    """Returns (sector, total, donor_count) — queried live so avg is always fresh."""
    rows = conn.execute("""
        SELECT employer_sector          AS sector,
               SUM(amount)             AS total,
               COUNT(*)                AS donor_count
        FROM fec_individual_contributions
        WHERE candidate_id = ? AND cycle = ?
        GROUP BY employer_sector
        ORDER BY total DESC
    """, (candidate_id, cycle)).fetchall()
    return [(r["sector"], float(r["total"] or 0), r["donor_count"]) for r in rows]


def get_top_employers(
    conn: sqlite3.Connection,
    candidate_id: str,
    cycle: int,
    limit: int = 20,
) -> list[tuple]:
    """Returns (employer, sector, total, donor_count)."""
    rows = conn.execute("""
        SELECT contributor_employer     AS emp,
               employer_sector         AS sec,
               SUM(amount)             AS total,
               COUNT(*)                AS donor_count
        FROM fec_individual_contributions
        WHERE candidate_id = ?
          AND cycle = ?
          AND COALESCE(contributor_employer, '') != ''
        GROUP BY contributor_employer, employer_sector
        ORDER BY total DESC
        LIMIT ?
    """, (candidate_id, cycle, limit)).fetchall()
    return [(r["emp"], r["sec"], float(r["total"] or 0), r["donor_count"]) for r in rows]


# Bioguide IDs for Virginia delegation (maps FEC candidate_id → bioguide_id)
FEC_TO_BIOGUIDE = {
    "H2VA02064": "K000399",  # Kiggans
    "H6VA01117": "S000185",  # Bobby Scott
    "H8VA01147": "W000804",  # Wittman
    "S6VA00093": "W000805",  # Warner
    "S2VA00142": "K000384",  # Kaine
    "H4VA04066": "M001239",  # McClellan
    "H4VA08224": "B001292",  # Beyer
    "H8VA11062": "C001118",  # Connolly
    "H0VA09055": "G000568",  # Griffith
    "H0VA05160": "G000599",  # Good
    "H8VA06104": "C001118",  # Cline — placeholder
}


def get_vote_summary(conn: sqlite3.Connection, candidate_id: str) -> dict:
    """Pull voting stats from congress_votes for the member."""
    bioguide_id = FEC_TO_BIOGUIDE.get(candidate_id)
    if not bioguide_id:
        return {}

    tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='congress_votes'"
    ).fetchone()
    if not tbl:
        return {}

    total = conn.execute(
        "SELECT COUNT(*) FROM congress_votes WHERE bioguide_id = ?", (bioguide_id,)
    ).fetchone()[0]
    if not total:
        return {}

    # Party-line vote rate (Yea on bills that passed, Nay on bills that failed — rough proxy)
    votes = conn.execute(
        "SELECT member_vote, result FROM congress_votes WHERE bioguide_id = ?",
        (bioguide_id,)
    ).fetchall()

    with_majority = sum(
        1 for v in votes
        if (v["member_vote"] == "Yea" and "Passed" in (v["result"] or ""))
        or (v["member_vote"] == "Nay" and "Failed" in (v["result"] or ""))
    )
    alignment_pct = round(with_majority / total * 100, 1) if total else 0

    # Most recent 10 notable votes
    recent = conn.execute("""
        SELECT vote_date, bill, question, member_vote, result
        FROM congress_votes
        WHERE bioguide_id = ?
        ORDER BY vote_date DESC
        LIMIT 10
    """, (bioguide_id,)).fetchall()

    # Key topic votes (defense, veterans, energy, healthcare)
    keywords = ["defense", "veteran", "energy", "armed", "natural resource",
                "healthcare", "climate", "tax", "budget", "appropriation"]
    topic_votes = conn.execute("""
        SELECT vote_date, bill, question, member_vote, result
        FROM congress_votes
        WHERE bioguide_id = ?
        AND (LOWER(question) LIKE '%defense%'
             OR LOWER(question) LIKE '%veteran%'
             OR LOWER(question) LIKE '%energy%'
             OR LOWER(question) LIKE '%armed%'
             OR LOWER(question) LIKE '%appropriat%'
             OR LOWER(question) LIKE '%budget%')
        ORDER BY vote_date DESC
        LIMIT 10
    """, (bioguide_id,)).fetchall()

    return {
        "bioguide_id": bioguide_id,
        "total_votes": total,
        "majority_alignment_pct": alignment_pct,
        "recent_votes": [dict(r) for r in recent],
        "topic_votes": [dict(r) for r in topic_votes],
    }


# Maps bill topic keywords → donor sectors for defection cross-reference
TOPIC_TO_SECTOR = {
    "defense": "Defense",
    "armed services": "Defense",
    "military": "Defense",
    "veteran": "Defense",
    "energy": "Energy",
    "oil": "Energy",
    "gas": "Energy",
    "health": "Healthcare",
    "medicare": "Healthcare",
    "medicaid": "Healthcare",
    "financial": "Finance",
    "banking": "Finance",
    "securities": "Finance",
    "real estate": "Real Estate",
    "housing": "Real Estate",
    "education": "Education",
    "technology": "Technology",
    "environment": "Energy",
    "tax": "Finance",
    "appropriat": "Finance",
}


def get_defections(conn: sqlite3.Connection, candidate_id: str, cycle: int) -> list[dict]:
    """
    Find votes where the member broke with the expected party majority position,
    then cross-reference each defection with the member's donor sectors.

    Defection logic:
      - Majority-party member (R in 119th House/Senate): voted Nay on Passed, or Yea on Failed
      - Minority-party member (D): voted Yea on Passed (crossed aisle), or Nay on Failed (with majority)
    """
    bioguide_id = FEC_TO_BIOGUIDE.get(candidate_id)
    if not bioguide_id:
        return []

    member = conn.execute(
        "SELECT party FROM congress_members WHERE bioguide_id = ?", (bioguide_id,)
    ).fetchone()
    if not member:
        return []

    party = member["party"]
    is_majority = party == "Republican"  # 119th Congress

    donor_sectors = {
        r["sector"] for r in conn.execute(
            "SELECT DISTINCT sector FROM candidate_sector_totals WHERE candidate_id = ? AND cycle = ?",
            (candidate_id, cycle),
        ).fetchall()
        if r["sector"] not in ("Other", "Unknown", "Retired", "Self-Employed", "Homemaker")
    }

    votes = conn.execute("""
        SELECT vote_date, bill, question, member_vote, result
        FROM congress_votes
        WHERE bioguide_id = ?
          AND result IS NOT NULL
          AND member_vote IN ('Yea', 'Nay')
        ORDER BY vote_date DESC
    """, (bioguide_id,)).fetchall()

    defections = []
    for v in votes:
        result = v["result"] or ""
        vote = v["member_vote"]
        passed = "Passed" in result or "Agreed" in result
        failed = "Failed" in result or "Rejected" in result
        if not (passed or failed):
            continue

        if is_majority:
            is_defection = (vote == "Nay" and passed) or (vote == "Yea" and failed)
        else:
            is_defection = (vote == "Yea" and passed) or (vote == "Nay" and failed)

        if not is_defection:
            continue

        question_lower = (v["question"] or "").lower()
        bill_lower = (v["bill"] or "").lower()
        text = question_lower + " " + bill_lower

        related_sector = None
        for keyword, sector in TOPIC_TO_SECTOR.items():
            if keyword in text:
                related_sector = sector
                break

        if related_sector and related_sector in donor_sectors:
            flag = f"Policy area overlaps with top donor sector ({related_sector})"
        else:
            flag = "No clear donor-sector overlap"

        party_majority = "Nay" if passed else "Yea"  # what the majority outcome implies
        defections.append({
            "date": v["vote_date"],
            "bill": v["bill"],
            "question": v["question"],
            "vote": vote,
            "party_majority": party_majority,
            "result": result,
            "related_sector": related_sector,
            "flag": flag,
            "classification": flag,  # alias for prompt compatibility
        })

    return defections


def classify_donor_concentration(sector_totals: list[tuple]) -> list[dict]:
    """
    Classify each sector as Grassroots / Mixed / Corporate by avg donation.
    FEC 2024 individual limit: $3,300.
      Grassroots: avg < $200
      Mixed:      avg $200–$999
      Corporate:  avg >= $1,000
    """
    classified = []
    for sector, total, count in sector_totals:
        if count == 0:
            continue
        avg = total / count
        if avg < 200:
            concentration, label = "Grassroots", "G"
        elif avg < 1000:
            concentration, label = "Mixed", "M"
        else:
            concentration, label = "Corporate", "C"
        classified.append({
            "sector": sector,
            "total": total,
            "donor_count": count,
            "avg_donation": avg,
            "concentration": concentration,
            "label": label,
        })
    return classified


def summarize_funding_profile(classified: list[dict]) -> dict:
    """Returns grassroots/corporate/mixed split by dollars and by donor count."""
    total_dollars = sum(c["total"] for c in classified)
    total_donors  = sum(c["donor_count"] for c in classified)

    def _dollars(conc: str) -> float:
        return sum(c["total"] for c in classified if c["concentration"] == conc)

    def _donors(conc: str) -> int:
        return sum(c["donor_count"] for c in classified if c["concentration"] == conc)

    def _pct(n: float, d: float) -> float:
        return round(n / d * 100, 1) if d else 0.0

    corp_d, grass_d, mixed_d = _dollars("Corporate"), _dollars("Grassroots"), _dollars("Mixed")
    corp_n, grass_n = _donors("Corporate"), _donors("Grassroots")

    return {
        "total_raised":           total_dollars,
        "total_donors":           total_donors,
        "corporate_pct_dollars":  _pct(corp_d, total_dollars),
        "grassroots_pct_dollars": _pct(grass_d, total_dollars),
        "mixed_pct_dollars":      _pct(mixed_d, total_dollars),
        "corporate_pct_donors":   _pct(corp_n, total_donors),
        "grassroots_pct_donors":  _pct(grass_n, total_donors),
    }


def build_triangle_prompt(
    legislator_name: str,
    candidate_id: str,
    classified_sectors: list[dict],
    profile: dict,
    top_employers: list[tuple],
    committees: list[str],
    defections: list[dict],
    cycle: int = 2024,
    vote_summary: dict | None = None,
) -> str:
    sector_lines = "\n".join(
        f"  [{c['label']}] {c['sector']}: "
        f"${c['total']:,.0f} total | {c['donor_count']} donors | "
        f"${c['avg_donation']:,.0f} avg | {c['concentration']}"
        for c in classified_sectors
    ) or "  - No sector data available"

    profile_summary = (
        f"  Total raised:     ${profile['total_raised']:,.0f}\n"
        f"  Total donors:     {profile['total_donors']:,}\n\n"
        f"  BY DOLLARS:\n"
        f"  [C] Corporate:    {profile['corporate_pct_dollars']:.1f}%\n"
        f"  [M] Mixed:        {profile['mixed_pct_dollars']:.1f}%\n"
        f"  [G] Grassroots:   {profile['grassroots_pct_dollars']:.1f}%\n\n"
        f"  BY DONOR COUNT:\n"
        f"  [C] Corporate:    {profile['corporate_pct_donors']:.1f}%\n"
        f"  [G] Grassroots:   {profile['grassroots_pct_donors']:.1f}%"
    )

    employer_lines = "\n".join(
        f"  - {emp} [{sec}]: ${tot:,.0f} from {cnt} donors (avg ${tot/cnt:,.0f})"
        for emp, sec, tot, cnt in top_employers
        if cnt > 0
    ) or "  - No employer data available"

    committee_lines = "\n".join(f"  - {c}" for c in committees)

    defection_lines = "\n".join(
        f"  - {d['bill']}: Voted {d['vote']} | Party voted {d['party_majority']} | {d['classification']}"
        for d in defections
    ) if defections else "  No notable defections found"

    if vote_summary and vote_summary.get("total_votes"):
        topic_lines = "\n".join(
            f"  - {v['vote_date']} | {v['bill']} | {v['question']} | Voted: {v['member_vote']} | Result: {v['result']}"
            for v in vote_summary.get("topic_votes", [])
        ) or "  - None found"
        vote_section = (
            f"\nVOTING RECORD:\n"
            f"  Total votes: {vote_summary['total_votes']} | "
            f"With majority outcome: {vote_summary['majority_alignment_pct']}%\n\n"
            f"KEY TOPIC VOTES:\n{topic_lines}\n"
        )
    else:
        vote_section = "\nVOTING RECORD: No vote data available for this member.\n"

    return f"""
You are a nonpartisan campaign finance analyst for VoteIQ,
a civic intelligence platform covering Virginia.

Analyze {legislator_name}'s complete funding profile for the {cycle} cycle.

FUNDING PROFILE SUMMARY:
{profile_summary}

SECTOR BREAKDOWN ([C] Corporate | [M] Mixed | [G] Grassroots):
{sector_lines}

TOP EMPLOYERS IN DONOR BASE:
{employer_lines}

COMMITTEE ASSIGNMENTS:
{committee_lines}

PARTY DEFECTIONS:
{defection_lines}
{vote_section}
CRITICAL DISTINCTION TO ALWAYS MAKE:
- Corporate funding = few donors, large checks, institutional access
- Grassroots funding = many donors, small checks, constituent support
- A candidate can have BOTH — always separate them clearly
- % of dollars vs % of donors often tells opposite stories

ANALYSIS INSTRUCTIONS:
1. Separate grassroots base from corporate money clearly
2. Identify who funds them vs who their base actually is
3. Flag any sector where corporate % of dollars >> grassroots % of donors
4. Identify committee alignment with corporate sectors specifically
5. Identify committee alignment with grassroots sectors separately
6. Classify each defection: donor-aligned / donor-contradicted / unrelated
7. Note if defections protect grassroots base or corporate donors
8. Rate overall donor-committee alignment: Low / Medium / High
9. Surface the single most newsworthy finding in one sentence

FORMAT YOUR RESPONSE AS:

## Funding Profile
[Grassroots base: X donors averaging $Y]
[Corporate money: X donors averaging $Y]
[Key tension: where grassroots base and corporate money diverge]

## Sector Concentration Analysis
[Wealthy few vs broad many for each sector]

## Corporate Donor Alignment
[Which corporate sectors align with committee assignments]

## Notable Defections
[Classify each defection with donor context]

## Alignment Rating
[Low/Medium/High — one sentence]

## Concentration Rating
[Concentrated/Mixed/Grassroots — one sentence]

## VoteIQ Finding
[Single most newsworthy nonpartisan sentence]

Correlation does not imply causation.
All data sourced from public FEC and VA SBE filings.
""".strip()


def run_claude_correlation(
    conn: sqlite3.Connection,
    candidate_name: str,
    candidate_id: str,
    cycle: int,
) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    sector_totals = get_sector_totals(conn, candidate_id, cycle)
    top_employers = get_top_employers(conn, candidate_id, cycle)
    committees = COMMITTEE_ASSIGNMENTS.get(candidate_id, ["Unknown"])
    vote_summary = get_vote_summary(conn, candidate_id)
    defections = get_defections(conn, candidate_id, cycle)
    classified = classify_donor_concentration(sector_totals)
    profile = summarize_funding_profile(classified)
    prompt = build_triangle_prompt(
        candidate_name,
        candidate_id,
        classified,
        profile,
        top_employers,
        committees,
        defections,
        cycle=cycle,
        vote_summary=vote_summary,
    )

    client = Anthropic(api_key=api_key)
    last_error = None
    message = None
    for model in [CLAUDE_MODEL, *CLAUDE_FALLBACK_MODELS]:
        try:
            message = client.messages.create(
                model=model,
                max_tokens=1000,
                system=(
                    "You are a nonpartisan civic intelligence analyst. "
                    "Be factual, precise, and always include the correlation caveat."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            if model != CLAUDE_MODEL:
                print(f"  Claude model fallback used: {model}")
            break
        except Exception as exc:
            last_error = exc
            if "not_found_error" not in str(exc) and "model" not in str(exc).lower():
                raise
    if message is None:
        raise RuntimeError(f"Claude report failed: {last_error}") from last_error
    report = message.content[0].text
    conn.execute("""
        INSERT INTO candidate_finance_reports (
            candidate_id, candidate_name, cycle, report, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id, cycle) DO UPDATE SET
            candidate_name=excluded.candidate_name,
            report=excluded.report,
            created_at=excluded.created_at
    """, (candidate_id, candidate_name, cycle, report, now_iso()))
    conn.commit()
    return report


def run_full_pipeline(
    conn: sqlite3.Connection,
    candidate_name: str,
    candidate_id: str,
    cycle: int,
    fec_api_key: str,
    limit_pages: int | None,
    report: bool,
) -> None:
    print(f"\nProcessing {candidate_name} ({candidate_id}), cycle {cycle}")
    committees = fetch_authorized_committees(candidate_id, cycle, fec_api_key)
    if not committees:
        print("  no authorized FEC committees found")
        return

    total_written = 0
    for committee in committees:
        committee_id = committee["committee_id"]
        committee_name = committee.get("name") or committee_id
        print(f"  fetching {committee_name} ({committee_id})")
        contribs = fetch_schedule_a(committee_id, cycle, fec_api_key, limit_pages)
        print(f"  found {len(contribs)} itemized contributions for {committee_name}")
        if contribs:
            written = insert_contributions(conn, candidate_name, candidate_id, contribs, cycle)
            total_written += written
            print(f"  inserted/updated {written} rows (running total: {total_written})")
    if not total_written:
        return
    print(f"  total inserted/updated: {total_written} contribution rows")

    sector_count = build_sector_totals(conn, candidate_id, candidate_name, cycle)
    print(f"  built {sector_count} sector totals")

    if report:
        print("  building Claude correlation report")
        text = run_claude_correlation(conn, candidate_name, candidate_id, cycle)
        safe_print("\n" + text)


def resolve_candidates(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.all:
        return list(VIRGINIA_CANDIDATES.items())
    if args.candidate_id:
        name = args.candidate or args.candidate_id
        return [(name, args.candidate_id)]
    if args.candidate:
        matches = [
            (name, cid)
            for name, cid in VIRGINIA_CANDIDATES.items()
            if args.candidate.lower() in name.lower()
        ]
        if not matches:
            raise SystemExit(f"Unknown candidate: {args.candidate}")
        return matches
    return [("Jennifer Kiggans", VIRGINIA_CANDIDATES["Jennifer Kiggans"])]


def main() -> None:
    parser = argparse.ArgumentParser(description="FEC employer-sector pipeline for Virginia candidates.")
    parser.add_argument("--candidate", help="Candidate name, matched against built-in Virginia list.")
    parser.add_argument("--candidate-id", help="Explicit FEC candidate ID.")
    parser.add_argument("--cycle", type=int, default=2024)
    parser.add_argument("--all", action="store_true", help="Run all built-in Virginia delegation candidates.")
    parser.add_argument("--limit-pages", type=int, help="Limit FEC pages for a quick smoke test.")
    parser.add_argument("--report", action="store_true", help="Generate and store a Claude correlation report.")
    parser.add_argument("--no-claude", action="store_true", help="Alias for leaving --report off.")
    parser.add_argument("--anthropic-model", help="Override Claude model for report generation.")
    parser.add_argument("--fec-api-key", default=os.getenv("FEC_API_KEY", "DEMO_KEY"))
    args = parser.parse_args()
    if args.anthropic_model:
        global CLAUDE_MODEL
        CLAUDE_MODEL = args.anthropic_model

    conn = get_db()
    create_tables(conn)
    try:
        for candidate_name, candidate_id in resolve_candidates(args):
            run_full_pipeline(
                conn=conn,
                candidate_name=candidate_name,
                candidate_id=candidate_id,
                cycle=args.cycle,
                fec_api_key=args.fec_api_key,
                limit_pages=args.limit_pages,
                report=bool(args.report and not args.no_claude),
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
