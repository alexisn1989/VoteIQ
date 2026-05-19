"""Federal member and representative routes."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["members"])

_BASE_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", _BASE_DIR), "polls.db")


@router.get("/federal-profiles", response_class=HTMLResponse)
def federal_profiles_page(request: Request):
    with open(os.path.join(_BASE_DIR, "templates", "federal_profiles.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/representatives", response_class=HTMLResponse)
def representatives_page():
    with open(os.path.join(_BASE_DIR, "templates", "representatives.html"), encoding="utf-8") as f:
        return f.read()


@router.get("/api/congress/members")
def congress_members():
    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute("""
            SELECT bioguide_id, name, party, chamber, district, website
            FROM congress_members
            ORDER BY chamber DESC, CAST(district AS INTEGER)
        """).fetchall()
        conn.close()
        return {"members": [
            {"bioguide_id": r[0], "name": r[1], "party": r[2],
             "chamber": r[3], "district": r[4], "website": r[5]}
            for r in rows
        ]}
    except Exception as exc:
        return {"members": [], "error": str(exc)}


@router.get("/api/congress/member/{bioguide_id}")
def congress_member_detail(bioguide_id: str):
    try:
        conn = sqlite3.connect(_POLLS_DB)
        row = conn.execute(
            "SELECT bioguide_id, name, party, chamber, state, district, website "
            "FROM congress_members WHERE bioguide_id = ?",
            (bioguide_id,)
        ).fetchone()
        conn.close()
        if not row:
            return {"member": None, "error": "not found"}
        return {"member": {
            "bioguide_id": row[0], "name": row[1], "party": row[2],
            "chamber": row[3], "state": row[4], "district": row[5], "website": row[6],
        }}
    except Exception as exc:
        return {"member": None, "error": str(exc)}


@router.get("/api/congress/bills/{bioguide_id}")
def congress_member_bills(bioguide_id: str, role: str = "sponsored", limit: int = 20):
    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute("""
            SELECT b.bill_type, b.bill_number, b.title, b.introduced_date,
                   b.policy_area, b.latest_action, b.latest_action_date, b.role
            FROM congress_bills b
            WHERE b.sponsor_id = ? AND (? = 'all' OR b.role = ?)
            ORDER BY b.introduced_date DESC
            LIMIT ?
        """, (bioguide_id, role, role, limit)).fetchall()
        conn.close()
        return {"bills": [
            {"type": r[0], "number": r[1], "title": r[2], "introduced": r[3],
             "policy_area": r[4], "latest_action": r[5], "latest_action_date": r[6], "role": r[7]}
            for r in rows
        ]}
    except Exception as exc:
        return {"bills": [], "error": str(exc)}


@router.get("/api/congress/votes/{bioguide_id}")
def congress_member_votes(bioguide_id: str, limit: int = 50):
    """Return recent roll-call votes for a VA member."""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute("""
            SELECT vote_number, chamber, vote_date, bill, question, member_vote, result
            FROM congress_votes
            WHERE bioguide_id = ?
            ORDER BY vote_date DESC, vote_number DESC
            LIMIT ?
        """, (bioguide_id, limit)).fetchall()
        conn.close()
        return {"votes": [
            {"vote_number": r[0], "chamber": r[1], "date": r[2], "bill": r[3],
             "question": r[4], "vote": r[5], "result": r[6]}
            for r in rows
        ]}
    except Exception as exc:
        return {"votes": [], "error": str(exc)}


@router.get("/api/congress/votes/{bioguide_id}/summary")
def congress_member_vote_summary(bioguide_id: str):
    """Return Yea/Nay/Not Voting counts and party-line stats for a member."""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute("""
            SELECT member_vote, COUNT(*) as cnt
            FROM congress_votes
            WHERE bioguide_id = ?
            GROUP BY member_vote
        """, (bioguide_id,)).fetchall()
        conn.close()
        summary = {r[0]: r[1] for r in rows}
        total = sum(summary.values())
        return {
            "total": total,
            "yea": summary.get("Yea", 0) + summary.get("Aye", 0),
            "nay": summary.get("Nay", 0) + summary.get("No", 0),
            "not_voting": summary.get("Not Voting", 0),
            "present": summary.get("Present", 0),
        }
    except Exception as exc:
        return {"total": 0, "error": str(exc)}


@router.get("/api/congress/bills")
def congress_bills_search(policy_area: str = "", limit: int = 50):
    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute("""
            SELECT b.bill_type, b.bill_number, b.title, b.introduced_date,
                   b.policy_area, b.latest_action, b.latest_action_date,
                   b.role, m.name, m.party, m.district
            FROM congress_bills b
            JOIN congress_members m ON m.bioguide_id = b.sponsor_id
            WHERE b.role = 'sponsored'
              AND (? = '' OR LOWER(b.policy_area) LIKE '%' || LOWER(?) || '%')
            ORDER BY b.introduced_date DESC
            LIMIT ?
        """, (policy_area, policy_area, limit)).fetchall()
        conn.close()
        return {"bills": [
            {"type": r[0], "number": r[1], "title": r[2], "introduced": r[3],
             "policy_area": r[4], "latest_action": r[5], "latest_action_date": r[6],
             "role": r[7], "sponsor": r[8], "party": r[9], "district": r[10]}
            for r in rows
        ]}
    except Exception as exc:
        return {"bills": [], "error": str(exc)}


@router.get("/api/congress/profile/{bioguide_id}")
def congress_member_profile(bioguide_id: str):
    """Full profile: member info + committees + FEC donors + vote stats + party alignment."""
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row

        member = conn.execute(
            "SELECT bioguide_id, name, party, chamber, state, district, website "
            "FROM congress_members WHERE bioguide_id = ?", (bioguide_id,)
        ).fetchone()
        if not member:
            conn.close()
            return {"error": "member not found"}

        committees = conn.execute(
            """SELECT committee_name, is_subcommittee, parent_code, parent_name, role
               FROM congress_committees WHERE bioguide_id = ?
               ORDER BY is_subcommittee, committee_name""", (bioguide_id,)
        ).fetchall()

        vote_rows = conn.execute(
            """SELECT member_vote, COUNT(*) FROM congress_votes
               WHERE bioguide_id = ? GROUP BY member_vote""", (bioguide_id,)
        ).fetchall()
        vote_map = {r[0]: r[1] for r in vote_rows}
        yea = vote_map.get("Yea", 0) + vote_map.get("Aye", 0)
        nay = vote_map.get("Nay", 0) + vote_map.get("No", 0)
        total_votes = sum(vote_map.values())

        party = member["party"]
        same_party_ids = conn.execute(
            "SELECT bioguide_id FROM congress_members WHERE party = ? AND bioguide_id != ?",
            (party, bioguide_id)
        ).fetchall()
        same_party_ids = [r[0] for r in same_party_ids]

        aligned = 0
        checked = 0
        if same_party_ids:
            placeholders = ",".join("?" * len(same_party_ids))
            pairs = conn.execute(f"""
                SELECT v1.bill, v1.chamber, v1.member_vote,
                       GROUP_CONCAT(v2.member_vote) as party_votes
                FROM congress_votes v1
                JOIN congress_votes v2
                  ON v1.bill = v2.bill AND v1.chamber = v2.chamber
                  AND v2.bioguide_id IN ({placeholders})
                WHERE v1.bioguide_id = ?
                  AND v1.member_vote IN ('Yea','Nay','Aye','No')
                GROUP BY v1.bill, v1.chamber, v1.member_vote
            """, (*same_party_ids, bioguide_id)).fetchall()

            for bill, chamber, my_vote, party_votes_str in pairs:
                if not party_votes_str:
                    continue
                party_votes = [v for v in party_votes_str.split(",") if v in ("Yea", "Nay", "Aye", "No")]
                if not party_votes:
                    continue
                yea_like = {"Yea", "Aye"}
                nay_like = {"Nay", "No"}
                party_yeas = sum(1 for v in party_votes if v in yea_like)
                party_nays = sum(1 for v in party_votes if v in nay_like)
                if party_yeas == party_nays:
                    continue
                party_majority = "yea" if party_yeas > party_nays else "nay"
                my_side = "yea" if my_vote in yea_like else "nay"
                checked += 1
                if my_side == party_majority:
                    aligned += 1

        alignment_pct = round(aligned / checked * 100, 1) if checked else None

        recent_votes = conn.execute(
            """SELECT vote_date, bill, question, member_vote, result
               FROM congress_votes WHERE bioguide_id = ?
               ORDER BY vote_date DESC LIMIT 30""", (bioguide_id,)
        ).fetchall()

        bills = conn.execute(
            """SELECT bill_type, bill_number, title, introduced_date,
                      policy_area, latest_action, latest_action_date, role
               FROM congress_bills WHERE sponsor_id = ?
               ORDER BY introduced_date DESC LIMIT 20""", (bioguide_id,)
        ).fetchall()

        fec_rows = conn.execute(
            """SELECT industry, total_amount, top_donors
               FROM fec_industry_totals
               WHERE bioguide_id = ? AND cycle = (
                   SELECT MAX(cycle) FROM fec_industry_totals WHERE bioguide_id = ?
               )
               ORDER BY total_amount DESC""", (bioguide_id, bioguide_id)
        ).fetchall()
        conn.close()

        photo_letter = bioguide_id[0].upper()
        photo_url = f"https://bioguide.congress.gov/bioguide/photo/{photo_letter}/{bioguide_id}.jpg"

        return {
            "member": {
                "bioguide_id": member["bioguide_id"],
                "name": member["name"],
                "party": member["party"],
                "chamber": member["chamber"],
                "district": member["district"],
                "website": member["website"],
                "photo_url": photo_url,
            },
            "committees": [
                {"name": r["committee_name"], "is_sub": bool(r["is_subcommittee"]),
                 "parent": r["parent_name"], "role": r["role"] or ""}
                for r in committees
            ],
            "vote_stats": {
                "total": total_votes, "yea": yea, "nay": nay,
                "yea_pct": round(yea / total_votes * 100, 1) if total_votes else 0,
                "party_alignment_pct": alignment_pct,
                "alignment_votes_checked": checked,
            },
            "recent_votes": [
                {"date": r[0], "bill": r[1], "question": r[2], "vote": r[3], "result": r[4]}
                for r in recent_votes
            ],
            "bills": [
                {"type": r[0], "number": r[1], "title": r[2], "introduced": r[3],
                 "policy_area": r[4], "latest_action": r[5], "latest_action_date": r[6], "role": r[7]}
                for r in bills
            ],
            "fec": [
                {"industry": r[0], "total": r[1], "top_donors": json.loads(r[2] or "[]")}
                for r in fec_rows
            ],
        }
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/va-officials")
def va_officials(office: str = "", limit: int = 200):
    try:
        conn = sqlite3.connect(_POLLS_DB)
        rows = conn.execute("""
            SELECT person_name, office, district, party, role,
                   incumbent, finance_url, source_url, data_confidence
            FROM va_finance_people
            WHERE (? = '' OR office = ?)
            ORDER BY
                CASE office
                    WHEN 'Governor' THEN 1
                    WHEN 'Lieutenant Governor' THEN 2
                    WHEN 'Attorney General' THEN 3
                    WHEN 'State Senate' THEN 4
                    WHEN 'House of Delegates' THEN 5
                    ELSE 6
                END,
                CAST(district AS INTEGER)
            LIMIT ?
        """, (office, office, limit)).fetchall()
        conn.close()
        return {"officials": [
            {"name": r[0], "office": r[1], "district": r[2], "party": r[3],
             "role": r[4], "incumbent": bool(r[5]), "finance_url": r[6],
             "source_url": r[7], "confidence": r[8]}
            for r in rows
        ]}
    except Exception as exc:
        return {"officials": [], "error": str(exc)}


@router.get("/api/congress/pac-summary/{bioguide_id}")
def pac_summary(bioguide_id: str):
    """Return FEC industry donation totals with per-cycle breakdown and trend %."""
    if not os.path.exists(_POLLS_DB):
        return {"bioguide_id": bioguide_id, "industries": [], "note": "polls.db missing"}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fec_industry_totals'"
        ).fetchone()
        if not tbl:
            conn.close()
            return {"bioguide_id": bioguide_id, "industries": [], "note": "no data"}
        all_rows = conn.execute(
            "SELECT industry, cycle, total_amount, contributor_count, top_donors, member_name "
            "FROM fec_industry_totals WHERE bioguide_id=? ORDER BY industry, cycle",
            (bioguide_id,)
        ).fetchall()
        conn.close()
    except Exception as exc:
        return {"bioguide_id": bioguide_id, "industries": [], "note": str(exc)}

    if not all_rows:
        return {"bioguide_id": bioguide_id, "industries": [], "note": "no data — run ingest_fec_pacs.py"}

    member_name = all_rows[0]["member_name"]
    by_industry: dict = defaultdict(lambda: {"by_cycle": {}, "top_donors": [], "count": 0})
    for row in all_rows:
        ind, cyc = row["industry"], str(row["cycle"])
        by_industry[ind]["by_cycle"][cyc] = row["total_amount"]
        by_industry[ind]["count"] += row["contributor_count"]
        if not by_industry[ind]["top_donors"]:
            by_industry[ind]["top_donors"] = json.loads(row["top_donors"] or "[]")

    industries = []
    for ind, data in by_industry.items():
        by_cycle = data["by_cycle"]
        sorted_cycles = sorted(by_cycle.keys())
        latest, earliest = by_cycle[sorted_cycles[-1]], by_cycle[sorted_cycles[0]]
        if len(sorted_cycles) > 1 and earliest > 0:
            trend_pct = round((latest - earliest) / earliest * 100)
            trend_label = f"+{trend_pct}%" if trend_pct >= 0 else f"{trend_pct}%"
            is_new = False
        elif len(sorted_cycles) == 1 and sorted_cycles[0] != "2020":
            trend_pct, trend_label, is_new = None, "NEW", True
        else:
            trend_pct, trend_label, is_new = None, "—", False
        industries.append({
            "industry": ind, "total": latest, "count": data["count"],
            "top_donors": data["top_donors"], "by_cycle": by_cycle,
            "cycles": sorted_cycles, "trend_pct": trend_pct,
            "trend_label": trend_label, "is_new": is_new,
        })

    industries.sort(key=lambda r: r["total"], reverse=True)
    all_cycles = sorted({str(row["cycle"]) for row in all_rows})
    return {
        "bioguide_id": bioguide_id, "member_name": member_name,
        "cycles": all_cycles, "latest_cycle": all_cycles[-1] if all_cycles else "",
        "industries": industries,
    }


@router.get("/api/congress/donors/{candidate_id}")
def candidate_donors(
    candidate_id: str,
    cycle: int = 2024,
    sector: str = "",
    limit: int = 100,
    offset: int = 0,
    sort: str = "amount",
):
    """Paginated donor list for a FEC candidate ID."""
    if not os.path.exists(_POLLS_DB):
        return {"error": "polls.db missing"}
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fec_individual_contributions'"
        ).fetchone()
        if not tbl:
            conn.close()
            return {"candidate_id": candidate_id, "donors": [], "note": "no data — run fec_employer_pipeline.py"}
        sort_col = "amount" if sort == "amount" else "contribution_date"
        where = "candidate_id = ? AND cycle = ?"
        params: list = [candidate_id, cycle]
        if sector:
            where += " AND employer_sector = ?"
            params.append(sector)
        total = conn.execute(
            f"SELECT COUNT(*) FROM fec_individual_contributions WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT contributor_name, contributor_employer, employer_sector,
                       contributor_occupation, amount, contribution_date, city, state
                FROM fec_individual_contributions
                WHERE {where} ORDER BY {sort_col} DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        sector_rows = conn.execute(
            """SELECT employer_sector, SUM(amount) AS total, COUNT(*) AS donors
               FROM fec_individual_contributions
               WHERE candidate_id = ? AND cycle = ?
               GROUP BY employer_sector ORDER BY total DESC""",
            [candidate_id, cycle],
        ).fetchall()
        conn.close()
        return {
            "candidate_id": candidate_id, "cycle": cycle,
            "total_donors": total, "limit": limit, "offset": offset,
            "sector_filter": sector or None,
            "sector_totals": [
                {"sector": r["employer_sector"], "total": r["total"], "donors": r["donors"]}
                for r in sector_rows
            ],
            "donors": [
                {"name": r["contributor_name"], "employer": r["contributor_employer"],
                 "sector": r["employer_sector"], "occupation": r["contributor_occupation"],
                 "amount": r["amount"], "date": r["contribution_date"],
                 "city": r["city"], "state": r["state"]}
                for r in rows
            ],
        }
    except Exception as exc:
        return {"error": str(exc)}
