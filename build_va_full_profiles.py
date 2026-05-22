#!/usr/bin/env python3
"""
Build full Virginia public-official profile chunks from local VoteIQ data.

Profiles combine:
  - Virginia SBE itemized campaign finance records, de-duplicated by Schedule A row
  - Current/recent legislative vote and bill counts where available
  - Governor action counts where available

Output: va_full_profiles.jsonl

Usage:
    python build_va_full_profiles.py
    python build_va_full_profiles.py --since 2005 --out va_full_profiles.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from build_va_state_finance import classify_sector

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"
LEG_DB = BASE_DIR / "legislative_intelligence.db"

OFFICE_ORDER = {
    "Governor": 1,
    "Lieutenant Governor": 2,
    "Attorney General": 3,
    "State Senate": 4,
    "House of Delegates": 5,
    "Statewide": 6,
}

TITLE_RE = re.compile(
    r"\b("
    r"mr|mrs|ms|miss|dr|hon|gov|lt|sen|senator|delegate|del|rep|representative"
    r")\.?\b",
    re.IGNORECASE,
)
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def clean_name(name: str) -> str:
    name = TITLE_RE.sub(" ", name or "")
    name = re.sub(r"[^A-Za-z .'-]", " ", name)
    parts = [p.strip(".") for p in name.split() if p.strip(".")]
    parts = [p for p in parts if p.lower() not in SUFFIXES]
    return " ".join(parts).strip()


def name_key(name: str) -> str:
    parts = [p.lower() for p in clean_name(name).split() if len(p) > 1]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1]}"


def office_label(office: str, is_statewide: str = "", is_ga: str = "") -> str:
    o = (office or "").lower()
    if "lieutenant" in o or "lt." in o or "lt governor" in o:
        return "Lieutenant Governor"
    if "attorney" in o:
        return "Attorney General"
    if "governor" in o:
        return "Governor"
    if "senate" in o:
        return "State Senate"
    if "house" in o or "delegates" in o:
        return "House of Delegates"
    if str(is_statewide).lower() == "true":
        return "Statewide"
    if str(is_ga).lower() == "true":
        return "General Assembly"
    return (office or "Unknown").strip() or "Unknown"


def norm_district(district: str) -> str:
    d = (district or "").strip()
    if not d or d in {"0", "00", "000", "N/A", "VA", "Virginia"}:
        return ""
    m = re.search(r"(\d{1,3})(?:st|nd|rd|th)?", d, re.IGNORECASE)
    return m.group(1).lstrip("0") if m else d


def fmt_money(value: float) -> str:
    return f"${float(value or 0):,.0f}"


def should_include_report(row: sqlite3.Row) -> bool:
    office = office_label(row["OfficeSought"], row["IsStateWide"], row["IsGeneralAssembly"])
    return office in OFFICE_ORDER or office == "General Assembly"


def looks_like_pac(row: sqlite3.Row) -> bool:
    text = f"{row['last_or_company'] or ''} {row['occupation'] or ''} {row['employer'] or ''}".lower()
    return bool(
        int(row["is_individual"] or 0) == 0
        or any(t in text for t in (" pac", "political action", "committee", "caucus", "party", "victory fund"))
    )


def profile_sort_key(item: tuple[tuple[str, str, str], dict]) -> tuple:
    (_key, office, district), data = item
    total = data["total"]
    return (OFFICE_ORDER.get(office, 99), office, district or "999", -total, data["display"])


def load_finance_profiles(since: int) -> dict[tuple[str, str, str], dict]:
    conn = sqlite3.connect(POLLS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            a.id AS schedule_row_id,
            a.schedule_a_id,
            a.candidate_name,
            a.election_cycle,
            a.amount,
            a.occupation,
            a.employer,
            a.last_or_company,
            a.first_name,
            a.city,
            a.state_code,
            a.is_individual,
            r.CandidateName,
            r.OfficeSought,
            r.District,
            r.Party,
            r.IsStateWide,
            r.IsGeneralAssembly
        FROM va_cf_schedule_a a
        JOIN va_cf_reports r ON r.ReportUID = a.report_uid
        WHERE r.CandidateName IS NOT NULL
          AND TRIM(r.CandidateName) != ''
          AND a.amount IS NOT NULL
          AND CAST(a.election_cycle AS INTEGER) >= ?
          AND (
                r.IsGeneralAssembly = 'True'
             OR r.IsStateWide = 'True'
             OR lower(r.OfficeSought) LIKE '%governor%'
             OR lower(r.OfficeSought) LIKE '%attorney general%'
             OR lower(r.OfficeSought) LIKE '%house of delegates%'
             OR lower(r.OfficeSought) LIKE '%state senate%'
          )
        ORDER BY CAST(a.election_cycle AS INTEGER), r.CandidateName
        """,
        (since,),
    )

    profiles: dict[tuple[str, str, str], dict] = {}
    seen_ids: set[int] = set()
    for row in rows:
        sid = int(row["schedule_row_id"])
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        if not should_include_report(row):
            continue
        display = clean_name(row["CandidateName"] or row["candidate_name"])
        key_name = name_key(display)
        if not key_name:
            continue
        office = office_label(row["OfficeSought"], row["IsStateWide"], row["IsGeneralAssembly"])
        if office == "General Assembly":
            office = "State Legislature"
        district = norm_district(row["District"])
        key = (key_name, office, district)
        data = profiles.setdefault(
            key,
            {
                "display_names": Counter(),
                "display": display,
                "office": office,
                "district": district,
                "party": Counter(),
                "cycles": defaultdict(lambda: {"total": 0.0, "records": 0}),
                "sectors": defaultdict(lambda: {"total": 0.0, "records": 0}),
                "donors": defaultdict(lambda: {"total": 0.0, "records": 0}),
                "pac_donors": defaultdict(lambda: {"total": 0.0, "records": 0}),
                "geo": defaultdict(lambda: {"total": 0.0, "records": 0}),
                "total": 0.0,
                "records": 0,
            },
        )
        data["display_names"][display] += 1
        if row["Party"]:
            data["party"][row["Party"]] += 1
        amount = float(row["amount"] or 0)
        cycle = str(row["election_cycle"] or "")
        sector = classify_sector(row["occupation"] or "", row["employer"] or "", row["last_or_company"] or "")
        donor = (f"{row['first_name'] or ''} {row['last_or_company'] or ''}".strip() if row["first_name"] else (row["last_or_company"] or "Unknown donor").strip())
        city_state = f"{row['city'] or 'Unknown'}, {row['state_code'] or ''}".strip(", ")

        data["total"] += amount
        data["records"] += 1
        data["cycles"][cycle]["total"] += amount
        data["cycles"][cycle]["records"] += 1
        data["sectors"][sector]["total"] += amount
        data["sectors"][sector]["records"] += 1
        data["donors"][donor]["total"] += amount
        data["donors"][donor]["records"] += 1
        data["geo"][city_state]["total"] += amount
        data["geo"][city_state]["records"] += 1
        if looks_like_pac(row):
            data["pac_donors"][donor]["total"] += amount
            data["pac_donors"][donor]["records"] += 1

    conn.close()
    for data in profiles.values():
        if data["display_names"]:
            data["display"] = data["display_names"].most_common(1)[0][0]
    return profiles


def _empty_profile(display: str, office: str, district: str) -> dict:
    return {
        "display_names": Counter(),
        "display": display,
        "office": office,
        "district": district,
        "party": Counter(),
        "cycles": defaultdict(lambda: {"total": 0.0, "records": 0}),
        "sectors": defaultdict(lambda: {"total": 0.0, "records": 0}),
        "donors": defaultdict(lambda: {"total": 0.0, "records": 0}),
        "pac_donors": defaultdict(lambda: {"total": 0.0, "records": 0}),
        "geo": defaultdict(lambda: {"total": 0.0, "records": 0}),
        "total": 0.0,
        "records": 0,
    }


def add_current_legislator_raw_profiles(profiles: dict[tuple[str, str, str], dict], since: int) -> None:
    """Guarantee every current GA member has a finance profile using raw SBE candidate-name rows."""
    if not LEG_DB.exists():
        return
    leg_conn = sqlite3.connect(LEG_DB)
    leg_conn.row_factory = sqlite3.Row
    current: dict[str, dict] = {}
    for row in leg_conn.execute("SELECT name, party, chamber, district FROM legislators"):
        k = name_key(row["name"])
        if not k:
            continue
        chamber = row["chamber"] or ""
        chamber_lower = chamber.lower()
        if "sen" in chamber_lower:
            office = "State Senate"
        elif "del" in chamber_lower or "house" in chamber_lower:
            office = "House of Delegates"
        else:
            continue
        current[k] = {
            "display": clean_name(row["name"]),
            "party": row["party"] or "",
            "office": office,
            "district": norm_district(str(row["district"] or "")),
        }
    leg_conn.close()
    if not current:
        return

    conn = sqlite3.connect(POLLS_DB)
    conn.row_factory = sqlite3.Row
    seen_ids: set[int] = set()
    rows = conn.execute(
        """
        SELECT id AS schedule_row_id, candidate_name, election_cycle, amount,
               occupation, employer, last_or_company, first_name, city, state_code,
               is_individual
        FROM va_cf_schedule_a
        WHERE candidate_name IS NOT NULL
          AND TRIM(candidate_name) != ''
          AND amount IS NOT NULL
          AND CAST(election_cycle AS INTEGER) >= ?
        """,
        (since,),
    )
    for row in rows:
        sid = int(row["schedule_row_id"])
        if sid in seen_ids:
            continue
        ck = name_key(row["candidate_name"])
        official = current.get(ck)
        if not official:
            continue
        seen_ids.add(sid)
        key = (ck, official["office"], official["district"])
        data = profiles.setdefault(key, _empty_profile(official["display"], official["office"], official["district"]))
        data["display_names"][official["display"]] += 1
        if official["party"]:
            data["party"][official["party"]] += 1
        amount = float(row["amount"] or 0)
        cycle = str(row["election_cycle"] or "")
        sector = classify_sector(row["occupation"] or "", row["employer"] or "", row["last_or_company"] or "")
        donor = (f"{row['first_name'] or ''} {row['last_or_company'] or ''}".strip() if row["first_name"] else (row["last_or_company"] or "Unknown donor").strip())
        city_state = f"{row['city'] or 'Unknown'}, {row['state_code'] or ''}".strip(", ")
        data["total"] += amount
        data["records"] += 1
        data["cycles"][cycle]["total"] += amount
        data["cycles"][cycle]["records"] += 1
        data["sectors"][sector]["total"] += amount
        data["sectors"][sector]["records"] += 1
        data["donors"][donor]["total"] += amount
        data["donors"][donor]["records"] += 1
        data["geo"][city_state]["total"] += amount
        data["geo"][city_state]["records"] += 1
        if looks_like_pac(row):
            data["pac_donors"][donor]["total"] += amount
            data["pac_donors"][donor]["records"] += 1
    conn.close()


def current_legislator_stats() -> dict[str, dict]:
    if not LEG_DB.exists():
        return {}
    conn = sqlite3.connect(LEG_DB)
    conn.row_factory = sqlite3.Row
    stats: dict[str, dict] = {}
    for row in conn.execute("SELECT lis_id, name, party, chamber, district FROM legislators"):
        key = name_key(row["name"])
        if not key:
            continue
        chamber_lower = (row["chamber"] or "").lower()
        if not any(term in chamber_lower for term in ("sen", "del", "house")):
            continue
        vote = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN vote='YES' THEN 1 ELSE 0 END) AS yes_votes,
                   SUM(CASE WHEN vote='NO' THEN 1 ELSE 0 END) AS no_votes,
                   MIN(vote_date) AS first_vote,
                   MAX(vote_date) AS last_vote
            FROM va_votes WHERE legislator_id=?
            """,
            (row["lis_id"],),
        ).fetchone()
        bills = conn.execute(
            "SELECT COUNT(*) FROM va_bills WHERE introduced_by=?",
            (row["lis_id"],),
        ).fetchone()[0]
        stats[key] = {
            "lis_id": row["lis_id"],
            "name": row["name"],
            "party": row["party"],
            "chamber": row["chamber"],
            "district": row["district"],
            "vote_total": int(vote["total"] or 0),
            "yes_votes": int(vote["yes_votes"] or 0),
            "no_votes": int(vote["no_votes"] or 0),
            "first_vote": vote["first_vote"],
            "last_vote": vote["last_vote"],
            "bills_introduced": int(bills or 0),
        }
    conn.close()
    return stats


def governor_action_stats() -> dict[str, dict]:
    if not POLLS_DB.exists():
        return {}
    conn = sqlite3.connect(POLLS_DB)
    conn.row_factory = sqlite3.Row
    stats: dict[str, dict] = {}
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='governor_actions'").fetchone():
        for row in conn.execute(
            """
            SELECT governor, action_label, action, COUNT(*) AS count
            FROM governor_actions
            GROUP BY governor, action_label, action
            """
        ):
            key = name_key(row["governor"] or "")
            if not key:
                continue
            label = row["action_label"] or row["action"] or "Unknown"
            stats.setdefault(key, defaultdict(int))[label] += int(row["count"] or 0)
    conn.close()
    return {k: dict(v) for k, v in stats.items()}


def top_items(mapping: dict, limit: int = 8) -> list[tuple[str, dict]]:
    return sorted(mapping.items(), key=lambda kv: kv[1]["total"], reverse=True)[:limit]


def build_text(data: dict, leg_stats: dict | None, gov_stats: dict | None, since: int) -> str:
    party = data["party"].most_common(1)[0][0] if data["party"] else ""
    district = f", District {data['district']}" if data["district"] else ""
    cycles = sorted([c for c in data["cycles"] if c], key=lambda y: int(y) if str(y).isdigit() else 0)
    cycle_span = f"{cycles[0]}-{cycles[-1]}" if cycles else f"{since}+"

    lines = [
        f"Full Virginia Profile: {data['display']}",
        f"Office/role: {data['office']}{district}",
        f"Party in SBE reports: {party or 'not listed'}",
        f"Data coverage: Virginia SBE itemized campaign finance records, cycles {cycle_span}; legislative vote/bill data where available from local OpenStates/LIS tables.",
        f"Career SBE fundraising total in local import: {fmt_money(data['total'])} from {data['records']:,} itemized contribution records.",
    ]

    if leg_stats:
        chamber = leg_stats.get("chamber") or ""
        lines.extend(
            [
                "",
                "[Current/recent General Assembly record]",
                f"Listed as: {leg_stats['name']} ({leg_stats.get('party') or ''}), {chamber} District {leg_stats.get('district') or ''}",
                f"Recorded votes: {leg_stats['vote_total']:,} ({leg_stats['yes_votes']:,} YES, {leg_stats['no_votes']:,} NO), {leg_stats.get('first_vote') or 'unknown'} to {leg_stats.get('last_vote') or 'unknown'}",
                f"Sponsored/introduced bills in local table: {leg_stats['bills_introduced']:,}",
            ]
        )

    if gov_stats:
        lines.append("")
        lines.append("[Governor action record]")
        for label, count in sorted(gov_stats.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- {label}: {count:,}")

    lines.append("")
    lines.append("[Campaign finance by cycle]")
    for cycle, row in top_items(data["cycles"], 12):
        lines.append(f"- {cycle}: {fmt_money(row['total'])} from {row['records']:,} records")

    lines.append("")
    lines.append("[Top donor sectors]")
    for sector, row in top_items(data["sectors"], 8):
        lines.append(f"- {sector}: {fmt_money(row['total'])} from {row['records']:,} records")

    lines.append("")
    lines.append("[Top contributors]")
    for donor, row in top_items(data["donors"], 8):
        lines.append(f"- {donor}: {fmt_money(row['total'])} from {row['records']:,} records")

    if data["pac_donors"]:
        lines.append("")
        lines.append("[Top PAC/committee-style contributors]")
        for donor, row in top_items(data["pac_donors"], 8):
            lines.append(f"- {donor}: {fmt_money(row['total'])} from {row['records']:,} records")

    lines.append("")
    lines.append("[Top donor geography]")
    for place, row in top_items(data["geo"], 8):
        lines.append(f"- {place}: {fmt_money(row['total'])} from {row['records']:,} records")

    lines.extend(
        [
            "",
            "[Methodology]",
            "Campaign finance totals are raw itemized Virginia SBE Schedule A records joined to SBE report metadata and de-duplicated by imported Schedule A row ID.",
            "Sector labels are keyword classifications from occupation/employer/contributor text, not official SBE categories.",
            "Correlation does not imply causation. Do not infer motive, pressure, reward, or influence from donations, votes, or bill outcomes.",
        ]
    )
    return "\n".join(lines)


def build_profiles(since: int) -> list[dict]:
    finance_profiles = load_finance_profiles(since)
    add_current_legislator_raw_profiles(finance_profiles, since)
    leg = current_legislator_stats()
    gov = governor_action_stats()
    chunks = []
    for (nkey, office, district), data in sorted(finance_profiles.items(), key=profile_sort_key):
        text = build_text(data, leg.get(nkey), gov.get(nkey), since)
        slug = re.sub(r"[^a-z0-9]+", "_", f"{data['display']}_{office}_{district}".lower()).strip("_")
        cycles = sorted([c for c in data["cycles"] if c], key=lambda y: int(y) if str(y).isdigit() else 0)
        chunk = {
            "chunk_id": f"va_full_profile_{slug}",
            "text": text,
            "bill_id": "",
            "session_id": cycles[-1] if cycles else "",
            "state": "VA",
            "year": cycles[-1] if cycles else "",
            "chunk_index": 0,
            "chunk_type": "va_full_profile",
            "metadata": {
                "source": "virginia_sbe_full_profile",
                "name": data["display"],
                "name_key": nkey,
                "office": office,
                "district": district,
                "party": data["party"].most_common(1)[0][0] if data["party"] else "",
                "cycle_start": cycles[0] if cycles else "",
                "cycle_end": cycles[-1] if cycles else "",
                "total_raised": f"{data['total']:.2f}",
                "records": str(data["records"]),
            },
        }
        chunks.append(chunk)
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Build full Virginia profiles from local data")
    parser.add_argument("--since", type=int, default=2005)
    parser.add_argument("--out", default="va_full_profiles.jsonl")
    args = parser.parse_args()

    chunks = build_profiles(args.since)
    out = BASE_DIR / args.out
    with out.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"Wrote {len(chunks):,} profiles to {out}")


if __name__ == "__main__":
    main()
