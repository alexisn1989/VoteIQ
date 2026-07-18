"""
One-off cross-city analysis: what TYPE of items is each Hampton Roads
locality's council passing, and is there a regional donor-vote signal or
are all 7 cities running fully independent local agendas?

Ad-hoc report only -- writes nothing to the database. Reuses:
- classify_topic() from build_vb_dvadj.py (regex over vote titles,
  city-agnostic)
- classify_sector() from build_vb_finance.py (employer/occupation
  keyword match, city-agnostic)

Normalizes the one real schema difference found across cities: VB's
vote column uses 'Yes/Aye'/'No/Nay'/'Absent'/'Away'/'Abstain'; the other
6 cities (Norfolk, Chesapeake, Portsmouth, Newport News, Hampton,
Suffolk) use lowercase 'yes'/'no'/'absent'/'abstain'.

Adjacency only -- no causal inference. Donor-sector concentration next
to a topic yes-rate is a correlation surfaced for the user's own
judgment, never a claim that money caused a vote.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "polls.db"

CITIES = {
    "norfolk": {"table": "norfolk_council_member_votes", "locality": "Norfolk"},
    "vb": {"table": "vb_council_member_votes", "locality": "Virginia Beach"},
    "chesapeake": {"table": "chesapeake_council_member_votes", "locality": "Chesapeake"},
    "portsmouth": {"table": "portsmouth_council_member_votes", "locality": "Portsmouth"},
    "newport_news": {"table": "newport_news_council_member_votes", "locality": "Newport News"},
    "hampton": {"table": "hampton_council_member_votes", "locality": "Hampton"},
    "suffolk": {"table": "suffolk_council_member_votes", "locality": "Suffolk"},
}

_YES_VALUES = {"yes", "yes/aye"}
_NO_VALUES = {"no", "no/nay"}

# ── classify_topic(), verbatim from build_vb_dvadj.py ─────────────────────────
TOPIC_PATTERNS: list[tuple[str, str]] = [
    ("rezoning",       r"rezon|conditional use permit|subdivision|variance|encroachment|"
                       r"street closure|comprehensive plan|change of zoning|alley closure|"
                       r"nonconforming|special exception"),
    ("development",    r"develop|property acquisition|purchase of property|sale of property|"
                       r"easement|right.of.way|acquisition of|ground lease|cda "),
    ("budget",         r"appropriat|budget|transfer of funds|bond issue|tax levy|cip |"
                       r"capital improvement|fiscal year|tax rate|revenue sharing"),
    ("defense",        r"navy|naval|military|joint expeditionary|oceana|dam neck|"
                       r"shore command|littoral|armed forces"),
    ("infrastructure", r"infrastructure|road|roadway|utility|water system|sewer|storm "
                       r"|stormwater|bridge|transit|light rail|bus rapid|highway|traffic"),
    ("public-safety",  r"police|fire station|fire department|rescue|emergency|public safety|"
                       r"crime|ems |ambulance|communications center"),
    ("economic-dev",   r"economic develop|tourism|resort|convention center|sports complex|"
                       r"event center|amphitheater|hotel|entertainment district|tif "),
    ("governance",     r"charter amendment|ordinance to adopt|ordinance to amend|policy|"
                       r"appointment of|task force|personnel|labor agreement|collective "
                       r"|contract with|service agreement"),
    ("environment",    r"environment|climate|wetland|shoreline|beach nourishment|"
                       r"tree canopy|open space|preserve|bay|creek|living shoreline"),
    ("schools",        r"school|education|library|recreation center|park |parks and"),
]


def classify_topic(title: str) -> str | None:
    t = (title or "").lower()
    for topic, pat in TOPIC_PATTERNS:
        if re.search(pat, t):
            return topic
    return None


# ── classify_sector(), condensed from build_vb_finance.py (city-agnostic
# portion only -- the VB-specific _EXACT_EMPLOYER overrides are dropped since
# this is a cross-city report) ────────────────────────────────────────────────
SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Real Estate",   ["realt", "develop", "construct", "proper", "build", "homebuil",
                       "architect", "contractor", "land ", "rental", "housing",
                       "real estate", "apartment", "residential", "property management"]),
    ("Hospitality",   ["hotel", "motel", "resort", "restaur", "food", "beverage",
                       "hospitality", "tourism", "marina", "golf "]),
    ("Finance",       ["bank", "financ", "invest", "capital", "credit union", "mortgage",
                       "insur", "wealth", "fund", "securities", "lending", "asset", " trust"]),
    ("Legal",         ["law ", "attorn", "legal", "counsel", " llp", " pllc", "esq", " pc ", "lawyer"]),
    ("Healthcare",    ["health", "hospital", "medic", "doctor", "physician", "nurs",
                       "pharma", "clinic", "dental", "care ", "sentara", "bon secours"]),
    ("Defense",       ["defense", "military", "navy", "army", "coast guard", "veteran",
                       "naval", "ship repair", "huntington ingalls"]),
    ("Education",     ["school", "educat", "universit", "college", "teacher", "professor",
                       "academ", "old dominion", "tidewater community"]),
    ("Labor",         ["union ", " union", "teamster", "fire fighter", "firefighter", "afl-cio"]),
    ("Government",    ["city of", "county of", "state of ", "federal ", "govern", "municipal",
                       "dept of", "department of", "sheriff"]),
    ("Energy",        ["energy", "utility", "electric", "gas ", "power ", "dominion", "petroleum"]),
    ("Tech",          ["tech", "software", "data ", "cyber", "digital", "computer"]),
    ("PAC/Advocacy",  ["pac ", "political action", "campaign committee", "democrat",
                       "republican", "party committee"]),
    ("Retired",       ["retired", "retirement", "not employed"]),
]


def classify_sector(employer: str, occupation: str) -> str:
    text = (employer or "").lower().strip()
    if not text or text in ("n/a", "na", "none", "self", "individual", "self-employed",
                             "self employed", "homemaker", "executive"):
        text = (occupation or "").lower().strip()
    for sector, keywords in SECTOR_KEYWORDS:
        if any(kw in text for kw in keywords):
            return sector
    return "Other"


def _last_name(full: str) -> str:
    parts = (full or "").strip().split()
    return parts[-1].lower() if parts else ""


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    print("=" * 70)
    print("PART 1: Topic distribution per city (what type of items each city votes on)")
    print("=" * 70)

    city_topic_dist: dict[str, dict[str, float]] = {}
    city_date_range: dict[str, tuple] = {}

    for city, cfg in CITIES.items():
        rows = conn.execute(
            f"SELECT title, meeting_date FROM {cfg['table']}"
        ).fetchall()
        topic_counts: dict[str, int] = defaultdict(int)
        untagged = 0
        dates = [r[1] for r in rows if r[1]]
        for title, _ in rows:
            topic = classify_topic(title)
            if topic:
                topic_counts[topic] += 1
            else:
                untagged += 1
        total = sum(topic_counts.values())
        dist = {t: round(100.0 * c / total, 1) for t, c in topic_counts.items()} if total else {}
        city_topic_dist[city] = dist
        city_date_range[city] = (min(dates), max(dates)) if dates else (None, None)

        print(f"\n{city.upper()} ({cfg['locality']}) -- "
              f"{len(rows)} member-vote rows, dates {city_date_range[city]}")
        for topic, pct in sorted(dist.items(), key=lambda x: -x[1]):
            print(f"  {pct:5.1f}%  {topic}")
        if untagged:
            print(f"  ({untagged} rows didn't match any topic pattern)")

    print("\n" + "=" * 70)
    print("PART 2: Cross-city comparison -- shared regional agenda or independent?")
    print("=" * 70)
    all_topics = sorted({t for dist in city_topic_dist.values() for t in dist})
    header = f"{'topic':15s} " + " ".join(f"{c:>13s}" for c in CITIES)
    print(header)
    for topic in all_topics:
        row = f"{topic:15s} " + " ".join(
            f"{city_topic_dist[c].get(topic, 0):>12.1f}%" for c in CITIES
        )
        print(row)

    print("\n" + "=" * 70)
    print("PART 3: Donor-sector signal per city (adjacency only, no causal inference)")
    print("=" * 70)

    for city, cfg in CITIES.items():
        locality = cfg["locality"]
        contribs = conn.execute(
            "SELECT candidate_name, employer, occupation, amount "
            "FROM sbe_local_contributions WHERE locality=? AND office_sought LIKE '%Council%'",
            (locality,),
        ).fetchall()
        if not contribs:
            print(f"\n{city.upper()}: no sbe_local_contributions rows for this locality -- skipping")
            continue

        # sector totals per candidate
        cand_sector_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for cand, employer, occupation, amount in contribs:
            sector = classify_sector(employer, occupation)
            cand_sector_totals[cand][sector] += amount or 0.0

        # member yes-rate by topic, from the vote table
        vote_rows = conn.execute(
            f"SELECT member_name, title, vote FROM {cfg['table']}"
        ).fetchall()
        member_topic: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"yes": 0, "total": 0}))
        council_topic: dict[str, dict] = defaultdict(lambda: {"yes": 0, "total": 0})
        for member, title, vote in vote_rows:
            v = (vote or "").lower()
            if v not in _YES_VALUES and v not in _NO_VALUES:
                continue
            topic = classify_topic(title)
            if not topic:
                continue
            member_topic[member][topic]["total"] += 1
            council_topic[topic]["total"] += 1
            if v in _YES_VALUES:
                member_topic[member][topic]["yes"] += 1
                council_topic[topic]["yes"] += 1

        council_yes_pct = {
            t: round(100.0 * c["yes"] / c["total"], 1)
            for t, c in council_topic.items() if c["total"] >= 10
        }

        # match candidates to vote-table members by last name
        vote_last_names = {_last_name(m): m for m in member_topic}

        print(f"\n{city.upper()} ({locality}): {len(cand_sector_totals)} local council "
              f"candidates in sbe_local_contributions, {len(member_topic)} members with vote data")

        signals_found = 0
        for cand, sectors in cand_sector_totals.items():
            last = _last_name(cand)
            vote_member = vote_last_names.get(last)
            if not vote_member:
                continue
            total_amt = sum(sectors.values())
            if total_amt <= 0:
                continue
            top_sector, top_amt = max(sectors.items(), key=lambda x: x[1])
            sector_pct = round(100.0 * top_amt / total_amt, 1)

            # crude sector->topic guess reusing VB's map where it overlaps
            related = {
                "Real Estate": "rezoning", "Hospitality": "economic-dev",
                "Finance": "budget", "Legal": "governance", "Healthcare": "public-safety",
                "Defense": "defense", "Education": "schools", "Labor": "governance",
                "Government": "governance", "Energy": "infrastructure", "Tech": "infrastructure",
            }.get(top_sector)
            if not related:
                continue
            m_rates = member_topic[vote_member].get(related)
            c_rate = council_yes_pct.get(related)
            if not m_rates or c_rate is None or m_rates["total"] < 5:
                continue
            m_yes = round(100.0 * m_rates["yes"] / m_rates["total"], 1)
            delta = round(m_yes - c_rate, 1)
            print(f"  {vote_member} ({top_sector} {sector_pct:.0f}% of local donations, "
                  f"${top_amt:,.0f}): votes YES on {related} {m_yes:.0f}% vs "
                  f"council avg {c_rate:.0f}% (delta {delta:+.0f}pp, {m_rates['total']} votes) "
                  f"[adjacency only]")
            signals_found += 1
        if not signals_found:
            print("  no member matched to a candidate with enough overlapping vote data")

    conn.close()


if __name__ == "__main__":
    main()
