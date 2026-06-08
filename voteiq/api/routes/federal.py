"""VA Federal Delegation table + industry funding analysis — HTML + JSON API."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["federal"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")


def _conn():
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    c = sqlite3.connect(_POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


def _federal_list(conn) -> list[dict]:
    try:
        rows = conn.execute("""
            SELECT m.bioguide_id,
                   m.name,
                   m.chamber,
                   m.district,
                   m.party,
                   m.website,
                   f.total_raised,
                   f.top_sector,
                   f.top_sector_pct,
                   f.latest_cycle,
                   f.by_sector_json
            FROM congress_members m
            LEFT JOIN campaign_finance_summary f
                   ON f.legislator_id = m.bioguide_id AND f.source = 'fec'
            ORDER BY
                CASE m.chamber WHEN 'Senate' THEN 0 ELSE 1 END,
                CAST(CASE m.district WHEN 'S' THEN '0' ELSE m.district END AS INTEGER),
                m.name
        """).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        d = dict(r)
        d["by_sector"] = json.loads(d.pop("by_sector_json") or "[]")
        # Donor tiers: Grassroots (small donors) vs Industry/Corporate
        try:
            tiers = conn.execute("""
                SELECT
                    CASE WHEN industry = 'Grassroots' THEN 'Grassroots (Small Donors)'
                         WHEN industry = 'Other'      THEN 'Unclassified / Other'
                         ELSE 'Industry / Corporate'
                    END AS tier,
                    SUM(total_amount)      AS total,
                    SUM(contributor_count) AS cnt
                FROM fec_industry_totals
                WHERE bioguide_id = ?
                GROUP BY tier ORDER BY total DESC
            """, (d["bioguide_id"],)).fetchall()
            d["donor_tiers"] = [{"tier": t["tier"], "total": t["total"], "count": t["cnt"]} for t in tiers]
        except sqlite3.OperationalError:
            d["donor_tiers"] = []
        # Granular industry breakdown (excludes Grassroots / Other buckets)
        try:
            ind_rows = conn.execute("""
                SELECT industry, SUM(total_amount) AS total
                FROM fec_industry_totals
                WHERE bioguide_id = ?
                  AND industry NOT IN ('Grassroots', 'Other')
                  AND industry IS NOT NULL AND industry != ''
                GROUP BY industry
                ORDER BY total DESC
                LIMIT 10
            """, (d["bioguide_id"],)).fetchall()
            d["industry_breakdown"] = [
                {"industry": r["industry"], "total": r["total"]} for r in ind_rows
            ]
        except sqlite3.OperationalError:
            d["industry_breakdown"] = []
        out.append(d)
    return out


def _inject(data: list[dict]) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / "federal_legislators.html").open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window._FED_DATA={safe};</script></head>", 1)


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/federal-legislators")
def api_federal_legislators():
    """Return all VA federal members with finance stats."""
    conn = _conn()
    try:
        data = _federal_list(conn)
        return {"members": data, "total": len(data)}
    finally:
        conn.close()


# ── HTML page ─────────────────────────────────────────────────────────────────

@router.get("/federal-legislators", response_class=HTMLResponse)
def federal_legislators_page():
    """Sortable/filterable table of VA federal delegation."""
    conn = _conn()
    try:
        data = _federal_list(conn)
        return _inject(data)
    finally:
        conn.close()


# ── Federal Analysis ──────────────────────────────────────────────────────────

def _federal_analysis_data(conn) -> dict:
    """Build the full payload for /federal-analysis."""

    # 1. Members
    members_raw = {
        r["bioguide_id"]: dict(r)
        for r in conn.execute(
            "SELECT bioguide_id, name, party, chamber, district, website FROM congress_members"
        ).fetchall()
    }

    # 2. Industry totals aggregated across all cycles (exclude Grassroots/Other noise)
    try:
        ind_rows = conn.execute("""
            SELECT bioguide_id, member_name, industry,
                   SUM(total_amount) AS total,
                   SUM(contributor_count) AS cnt
            FROM fec_industry_totals
            WHERE industry NOT IN ('Grassroots', 'Other')
              AND industry IS NOT NULL AND industry != ''
            GROUP BY bioguide_id, industry
            ORDER BY bioguide_id, total DESC
        """).fetchall()
    except sqlite3.OperationalError:
        ind_rows = []

    by_member: dict[str, list] = defaultdict(list)
    for r in ind_rows:
        by_member[r["bioguide_id"]].append({
            "industry": r["industry"],
            "total": r["total"],
            "cnt": r["cnt"],
        })

    # 3. Vote summaries from congress_votes
    try:
        vote_rows = conn.execute("""
            SELECT bioguide_id,
                   SUM(CASE WHEN lower(member_vote) IN ('yea','aye','yes') THEN 1 ELSE 0 END) AS yea,
                   SUM(CASE WHEN lower(member_vote) IN ('nay','no')        THEN 1 ELSE 0 END) AS nay,
                   SUM(CASE WHEN lower(member_vote) = 'not voting'         THEN 1 ELSE 0 END) AS abstain,
                   COUNT(*) AS total
            FROM congress_votes
            GROUP BY bioguide_id
        """).fetchall()
    except sqlite3.OperationalError:
        vote_rows = []
    votes = {r["bioguide_id"]: dict(r) for r in vote_rows}

    # 4. Per-member objects
    members_out = []
    grand_total = 0
    all_industries: set[str] = set()

    for bgid, m in members_raw.items():
        inds = by_member.get(bgid, [])
        v = votes.get(bgid, {})
        total = sum(i["total"] for i in inds)
        grand_total += total
        yea = v.get("yea", 0)
        nay = v.get("nay", 0)
        vote_total = v.get("total", 0)
        yea_pct = round(yea / max(vote_total, 1) * 100, 1)
        for i in inds:
            all_industries.add(i["industry"])

        party = m.get("party") or ""
        party_short = "D" if "Democrat" in party else ("R" if "Republican" in party else "I")

        members_out.append({
            "bioguide_id": bgid,
            "name": m["name"],
            "party": party,
            "party_short": party_short,
            "chamber": m.get("chamber") or "",
            "district": m.get("district") or "",
            "website": m.get("website") or "",
            "total_raised": total,
            "top_industry": inds[0]["industry"] if inds else "",
            "top_industry_amount": inds[0]["total"] if inds else 0,
            "industries": inds[:10],
            "votes": {
                "yea": yea,
                "nay": nay,
                "abstain": v.get("abstain", 0),
                "total": vote_total,
                "yea_pct": yea_pct,
            },
        })

    members_out.sort(key=lambda x: x["total_raised"], reverse=True)

    # 5. Cross-party industry breakdown (top 20 industries by grand total)
    try:
        ind_party_rows = conn.execute("""
            SELECT i.industry,
                   SUM(CASE WHEN m.party LIKE '%Democrat%'   THEN i.total_amount ELSE 0 END) AS dem_total,
                   SUM(CASE WHEN m.party LIKE '%Republican%' THEN i.total_amount ELSE 0 END) AS rep_total,
                   SUM(i.total_amount) AS grand
            FROM fec_industry_totals i
            JOIN congress_members m ON m.bioguide_id = i.bioguide_id
            WHERE i.industry NOT IN ('Grassroots', 'Other')
              AND i.industry IS NOT NULL AND i.industry != ''
            GROUP BY i.industry
            ORDER BY grand DESC
            LIMIT 20
        """).fetchall()
        industry_totals = [dict(r) for r in ind_party_rows]
    except sqlite3.OperationalError:
        industry_totals = []

    top_r = max(members_out, key=lambda x: x["total_raised"]) if members_out else {}

    return {
        "members": members_out,
        "stats": {
            "member_count": len(members_out),
            "industry_count": len(all_industries),
            "grand_total": grand_total,
            "top_recipient_name": top_r.get("name", ""),
            "top_recipient_total": top_r.get("total_raised", 0),
        },
        "industry_totals": industry_totals,
    }


def _inject_analysis(data: dict) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / "federal_analysis.html").open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window._FA_DATA={safe};</script></head>", 1)


@router.get("/api/federal-analysis")
def api_federal_analysis():
    """Return VA federal delegation industry funding + vote data."""
    conn = _conn()
    try:
        return _federal_analysis_data(conn)
    finally:
        conn.close()


@router.get("/federal-analysis", response_class=HTMLResponse)
def federal_analysis_page():
    """Federal delegation industry funding analysis page."""
    conn = _conn()
    try:
        data = _federal_analysis_data(conn)
        return _inject_analysis(data)
    finally:
        conn.close()
