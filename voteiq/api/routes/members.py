"""Federal member and representative routes."""
from __future__ import annotations

import json
import os
import sqlite3
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
