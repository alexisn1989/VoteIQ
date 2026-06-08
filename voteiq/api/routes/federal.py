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
        data["alignment"] = _compute_alignment(conn)
        return _inject_analysis(data)
    finally:
        conn.close()


# ── Alignment Engine ──────────────────────────────────────────────────────────

# Maps FEC industry labels → keywords to search in bill titles
_INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "Defense":              ["defense", "military", "armed forces", "national security", "pentagon", "combat", "troop", "veteran", "soldier"],
    "Aerospace & Weapons":  ["aerospace", "aircraft", "missile", "space force", "satellite", "aviation", "weapon system"],
    "Defense IT & Services":["intelligence", "cyber", "surveillance", "classified", "dod", "department of defense", "defense contract"],
    "Fossil Fuels":         ["oil", "gas", "coal", "fossil", "petroleum", "pipeline", "drilling", "refinery", "lng", "offshore"],
    "Renewables":           ["solar", "wind", "renewable", "clean energy", "geothermal", "biofuel", "zero emission"],
    "Banking":              ["bank", "lending", "credit union", "mortgage", "fdic", "community bank", "financial institution"],
    "Financial Services":   ["financial", "securities", "investment fund", "portfolio", "asset management", "fintech"],
    "Investment & Securities": ["securities", "hedge fund", "private equity", "venture capital", "investment adviser"],
    "Health Insurance":     ["health insurance", "affordable care", "medicaid", "medicare", "coverage", "health plan", "insurer"],
    "Hospitals":            ["hospital", "health system", "medical center", "clinic", "patient care", "inpatient"],
    "Pharma":               ["drug", "pharmaceutical", "prescription", "fda", "biotech", "vaccine", "opioid", "biologic"],
    "Health Professionals": ["physician", "doctor", "nurse", "dentist", "therapist", "medical practice"],
    "Technology":           ["technology", "artificial intelligence", "ai ", "digital", "semiconductor", "computing", "data center"],
    "Software & IT":        ["software", "cybersecurity", "cloud", "algorithm", "information technology"],
    "Telecom":              ["telecom", "broadband", "spectrum", "wireless", "5g", "internet service provider"],
    "Agriculture":          ["farm", "agriculture", "crop", "livestock", "rural", "usda", "food production", "commodity"],
    "Agribusiness":         ["agribusiness", "seed", "fertilizer", "pesticide", "grain", "food processing"],
    "Transportation":       ["transportation", "highway", "rail", "aviation", "airport", "freight", "port", "transit"],
    "Real Estate":          ["real estate", "housing", "property", "zoning", "landlord", "rent", "homeowner"],
    "Commercial Real Estate": ["commercial real estate", "commercial property", "office building", "development project"],
    "Insurance":            ["insurance", "liability", "indemnity", "underwrite", "casualty"],
    "Legal":                ["legal", "court", "attorney", "justice", "lawsuit", "litigation", "judiciary"],
    "Trial Lawyers":        ["lawsuit", "litigation", "tort", "class action", "plaintiff", "damages", "contingency"],
    "Guns/NRA":             ["firearm", "gun", "second amendment", "ammunition", "rifle", "concealed carry"],
    "Education":            ["education", "school", "student loan", "university", "college", "teacher", "pell grant"],
    "Manufacturing":        ["manufacturing", "factory", "industrial", "production", "supply chain", "tariff"],
    "Fossil Fuels":         ["oil", "gas", "coal", "petroleum", "pipeline", "drilling", "lng"],
    "Gambling/Casinos":     ["gambling", "casino", "gaming", "lottery", "sports betting", "wagering"],
    "Utilities":            ["utility", "electric grid", "power plant", "rate", "ratepayer", "energy regulation"],
}


def _keyword_match_pct(titles: list[str], keywords: list[str]) -> tuple[int, int]:
    """Return (matches, total) of titles containing any keyword."""
    if not titles or not keywords:
        return 0, len(titles)
    matches = sum(
        1 for t in titles
        if any(k in (t or "").lower() for k in keywords)
    )
    return matches, len(titles)


def _compute_alignment(conn: sqlite3.Connection) -> list[dict]:
    """
    Compute 3-signal alignment scores for each VA federal member.

    Signal 1 — Sponsorship alignment:
        % of member-sponsored bills whose title matches their #1 donor industry keywords.
    Signal 2 — Co-sponsorship alignment:
        Same for co-sponsored bills.
    Signal 3 — Party-line score:
        % of roll-call votes where member voted with the majority of their same-party
        VA delegation colleagues.
    """
    # ── Members + top industries ──────────────────────────────────────────────
    try:
        members = conn.execute(
            "SELECT bioguide_id, name, party, chamber FROM congress_members"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    top_industry: dict[str, str] = {}
    try:
        for r in conn.execute("""
            SELECT bioguide_id, industry
            FROM (
                SELECT bioguide_id, industry,
                       SUM(total_amount) AS tot,
                       ROW_NUMBER() OVER (PARTITION BY bioguide_id ORDER BY SUM(total_amount) DESC) AS rn
                FROM fec_industry_totals
                WHERE industry NOT IN ('Grassroots','Other')
                GROUP BY bioguide_id, industry
            ) WHERE rn = 1
        """).fetchall():
            top_industry[r["bioguide_id"]] = r["industry"]
    except sqlite3.OperationalError:
        pass

    # ── Bill titles by member + role ─────────────────────────────────────────
    sponsored: dict[str, list[str]] = defaultdict(list)
    cosponsored: dict[str, list[str]] = defaultdict(list)
    try:
        for r in conn.execute(
            "SELECT sponsor_id, role, title FROM congress_bills WHERE title IS NOT NULL"
        ).fetchall():
            if r["role"] == "sponsored":
                sponsored[r["sponsor_id"]].append(r["title"])
            else:
                cosponsored[r["sponsor_id"]].append(r["title"])
    except sqlite3.OperationalError:
        pass

    # ── Party-line score ──────────────────────────────────────────────────────
    # For each roll call (vote_number + chamber + session), build party → votes map
    party_of: dict[str, str] = {}
    for m in members:
        p = m["party"] or ""
        party_of[m["bioguide_id"]] = "D" if "Democrat" in p else ("R" if "Republican" in p else "I")

    # Group votes by (chamber, session, vote_number)
    from collections import defaultdict as _dd
    roll_votes: dict[tuple, dict[str, str]] = _dd(dict)
    try:
        for r in conn.execute(
            "SELECT bioguide_id, chamber, session, vote_number, member_vote FROM congress_votes"
        ).fetchall():
            key = (r["chamber"], r["session"], r["vote_number"])
            roll_votes[key][r["bioguide_id"]] = (r["member_vote"] or "").lower()
    except sqlite3.OperationalError:
        pass

    # Per member: count rolls where they voted with party majority
    party_aligned: dict[str, int] = defaultdict(int)
    party_total: dict[str, int] = defaultdict(int)

    for roll_key, vote_map in roll_votes.items():
        # Group by party
        party_yeas: dict[str, int] = {"D": 0, "R": 0}
        party_nays: dict[str, int] = {"D": 0, "R": 0}
        for bgid, mv in vote_map.items():
            p = party_of.get(bgid, "I")
            if p not in ("D", "R"):
                continue
            if mv in ("yea", "aye", "yes"):
                party_yeas[p] += 1
            elif mv in ("nay", "no"):
                party_nays[p] += 1

        for bgid, mv in vote_map.items():
            p = party_of.get(bgid, "I")
            if p not in ("D", "R"):
                continue
            # Need at least one other party member voting to establish a majority
            others_yea = party_yeas[p] - (1 if mv in ("yea","aye","yes") else 0)
            others_nay = party_nays[p] - (1 if mv in ("nay","no") else 0)
            if others_yea == others_nay:
                continue  # no clear party majority among others
            party_pos = "yea" if others_yea > others_nay else "nay"
            member_pos = "yea" if mv in ("yea","aye","yes") else "nay"
            party_total[bgid] += 1
            if member_pos == party_pos:
                party_aligned[bgid] += 1

    # ── Assemble output ───────────────────────────────────────────────────────
    out = []
    for m in members:
        bgid = m["bioguide_id"]
        ind = top_industry.get(bgid, "")
        kw = _INDUSTRY_KEYWORDS.get(ind, [])

        sp_match, sp_total = _keyword_match_pct(sponsored.get(bgid, []), kw)
        co_match, co_total = _keyword_match_pct(cosponsored.get(bgid, []), kw)
        pl_total = party_total.get(bgid, 0)
        pl_aligned = party_aligned.get(bgid, 0)

        sp_pct = round(sp_match / max(sp_total, 1) * 100, 1)
        co_pct = round(co_match / max(co_total, 1) * 100, 1)
        pl_pct = round(pl_aligned / max(pl_total, 1) * 100, 1)

        out.append({
            "bioguide_id": bgid,
            "name": m["name"],
            "party_short": party_of.get(bgid, "I"),
            "chamber": m["chamber"] or "",
            "top_industry": ind,
            "sponsorship": {
                "matches": sp_match, "total": sp_total, "pct": sp_pct,
            },
            "cosponsor": {
                "matches": co_match, "total": co_total, "pct": co_pct,
            },
            "party_line": {
                "aligned": pl_aligned, "total": pl_total, "pct": pl_pct,
            },
        })

    # Drop members with no vote data at all (former members still in congress_members)
    out = [m for m in out if m["party_line"]["total"] > 0]
    out.sort(key=lambda x: x["party_line"]["pct"], reverse=True)
    return out


@router.get("/api/federal-alignment")
def api_federal_alignment():
    """3-signal alignment scores for VA federal delegation."""
    conn = _conn()
    try:
        return {"alignment": _compute_alignment(conn)}
    finally:
        conn.close()
