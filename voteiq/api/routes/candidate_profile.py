"""Federal House 2026 candidate profile — /candidate/federal/<cand_id>."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["candidate_profile"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_POLLS_DB = os.environ.get(
    "POLLS_DB_PATH",
    str(_BASE_DIR / "polls.db") if os.path.isfile(str(_BASE_DIR / "polls.db"))
    else "/var/data/polls.db",
)

_ICI_LABEL = {"I": "Incumbent", "C": "Challenger", "O": "Open Seat"}

# Maps raw sector labels (fec_va_house_contributions.sector) to policy issue areas.
# None = generic/non-industry, excluded from issue breakdown.
_SECTOR_TO_ISSUE: dict[str, str | None] = {
    # Economic / Business
    "Finance":                   "Economic Policy",
    "Finance & Banking":         "Economic Policy",
    "Business Executive":        "Business Interests",
    "Business/Management":       "Business Interests",
    "Consulting":                "Business Interests",
    "Lobbying & Consulting":     "Business Interests",
    "Lobbying":                  "Business Interests",
    # Real Estate
    "Real Estate":               "Real Estate",
    "Real Estate & Construction":"Real Estate",
    "Construction":              "Real Estate",
    # Healthcare
    "Healthcare":                "Healthcare",
    "Healthcare & Pharma":       "Healthcare",
    # Energy / Environment
    "Energy":                    "Energy & Environment",
    "Energy & Oil/Gas":          "Energy & Environment",
    # Defense / Security
    "Defense":                   "Defense & Security",
    "Defense & Aerospace":       "Defense & Security",
    # Legal
    "Legal":                     "Legal & Advocacy",
    "Advocacy":                  "Legal & Advocacy",
    "Nonprofit":                 "Legal & Advocacy",
    "Ideological/PAC":           "Legal & Advocacy",
    # Technology
    "Technology":                "Technology",
    "Technology & IT":           "Technology",
    # Education
    "Education":                 "Education",
    # Agriculture
    "Agriculture":               "Agriculture",
    # Labor
    "Labor & Unions":            "Labor",
    # Other identifiable industries
    "Transportation":            "Transportation",
    "Manufacturing":             "Manufacturing",
    "Automotive":                "Manufacturing",
    "Telecom & Media":           "Media & Comms",
    "Media/Creative":            "Media & Comms",
    "Government":                "Government",
    "Government/Public":         "Government",
    "Research/Science":          "Education",
    "Retail & Hospitality":      "Business Interests",
    "Hospitality":               "Business Interests",
    # Skip — generic / personal, not policy signals
    "Retired":                   None,
    "Homemaker":                 None,
    "Student":                   None,
    "Grassroots":                None,
    "Self-Employed":             None,
    "Self-Employed (Other)":     None,
    "Political/Campaign":        None,
    "Other (named employer)":    None,
    "Other/Unknown":             None,
    "Other":                     None,
}

# Per-issue "lean" inference (donor-base signal, not a political label)
_ISSUE_LEAN: dict[str, str] = {
    "Economic Policy":    "Pro-business / deregulation",
    "Business Interests": "Pro-business / free market",
    "Real Estate":        "Development & property rights",
    "Healthcare":         "Pharma / provider interests",
    "Energy & Environment": "Fossil fuel / energy sector",
    "Defense & Security": "Military spending / hawkish",
    "Legal & Advocacy":   "Institutional / bipartisan",
    "Technology":         "Tech industry / innovation",
    "Education":          "Academic / research community",
    "Agriculture":        "Rural / farm interests",
    "Labor":              "Union-aligned / worker interests",
    "Government":         "Public sector / civic",
    "Media & Comms":      "Media / comms industry",
    "Manufacturing":      "Industrial / supply chain",
    "Transportation":     "Infrastructure / logistics",
}


def _build_issue_areas(sectors: list[dict]) -> list[dict]:
    merged: dict[str, float] = {}
    for s in sectors:
        issue = _SECTOR_TO_ISSUE.get(s["label"])
        if issue is None:
            continue
        merged[issue] = merged.get(issue, 0.0) + (s["total"] or 0)
    return [
        {"label": k, "total": round(v, 0), "lean": _ISSUE_LEAN.get(k, "")}
        for k, v in sorted(merged.items(), key=lambda x: -x[1])
        if v > 0
    ]


def _tag_pac(pac: dict) -> dict:
    n = (pac["name"] or "").upper()
    if any(k in n for k in ("NRCC", "NRSC", "REPUBLICAN", "FREEDOM CAUCUS",
                             "CLUB FOR GROWTH", "HERITAGE ACTION", "TRUMP",
                             "MAGA", "CONSERVATIVE", "TEA PARTY", "PATRIOTS FOR",
                             "AMERICA FIRST")):
        tag = "Conservative"
    elif any(k in n for k in ("DCCC", "DSCC", "DEMOCRAT", "EMILY'S LIST",
                               "PLANNED PARENTHOOD", "PROGRESSIVE", "SIERRA CLUB",
                               "BRADY", "EVERYTOWN", "MOVEON", "END CITIZENS",
                               "RUN FOR SOMETHING")):
        tag = "Progressive"
    elif any(k in n for k in ("NRA", "GUN OWNERS", "FIREARM", "SECOND AMENDMENT",
                               "2ND AMENDMENT", "NSSF")):
        tag = "Pro-Gun"
    elif any(k in n for k in ("AFL-CIO", "TEAMSTER", "SEIU", "UFCW", "AFSCME",
                               "UAW", "IBEW", " UNION", "LABOR")):
        tag = "Labor"
    elif any(k in n for k in ("CHAMBER", "NFIB", "NAM ", "BUSINESS ROUNDTABLE")):
        tag = "Business"
    elif any(k in n for k in ("HEALTH", "HOSPITAL", "PHARMA", "MEDICAL",
                               " AMA ", "AHIP", "NURSES")):
        tag = "Healthcare"
    elif any(k in n for k in ("OIL", " GAS", "ENERGY", "PETROLEUM", "COAL",
                               "PIPELINE", "REFIN")):
        tag = "Energy"
    elif any(k in n for k in ("DEFENSE", "VETERAN", "MILITARY", "ARMED FORCES")):
        tag = "Defense"
    elif any(k in n for k in ("FOR CONGRESS", "FOR VA", "FOR U.S.", "FOR SENATE",
                               "CAMPAIGN COMMITTEE", "ELECTION FUND")):
        tag = "Candidate Transfer"
    else:
        tag = "Other"
    return {**pac, "ideology": tag}


def _conn():
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    conn = sqlite3.connect(_POLLS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _inject(template: str, key: str, data) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / template).open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window.{key}={safe};</script></head>", 1)


def _fmt_name(raw: str) -> str:
    if "," in raw:
        last, first = raw.split(",", 1)
        return f"{first.strip().title()} {last.strip().title()}"
    return raw.title()


def _fetch(conn: sqlite3.Connection, cand_id: str) -> dict:
    cand = conn.execute(
        "SELECT * FROM fec_va_house_candidates WHERE cand_id = ?", (cand_id,)
    ).fetchone()
    if not cand:
        raise HTTPException(status_code=404, detail=f"Candidate {cand_id!r} not found")

    # ── donor sector breakdown ────────────────────────────────────────────────
    sector_rows = conn.execute("""
        SELECT sector, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM   fec_va_house_contributions
        WHERE  cand_id = ? AND amount > 0
        GROUP  BY sector ORDER BY total DESC
    """, (cand_id,)).fetchall()
    sectors = [
        {"label": r["sector"] or "Unknown", "total": r["total"], "donors": r["donors"]}
        for r in sector_rows
    ]

    # ── top employers ─────────────────────────────────────────────────────────
    _SKIP_EMP = ("RETIRED", "NOT EMPLOYED", "NONE", "N/A", "SELF-EMPLOYED",
                 "SELF EMPLOYED", "HOMEMAKER", "INFORMATION REQUESTED")
    emp_rows = conn.execute("""
        SELECT employer, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM   fec_va_house_contributions
        WHERE  cand_id = ? AND amount > 0
          AND  employer IS NOT NULL AND employer != ''
        GROUP  BY employer ORDER BY total DESC LIMIT 15
    """, (cand_id,)).fetchall()
    employers = [
        {"name": r["employer"], "total": r["total"], "donors": r["donors"]}
        for r in emp_rows
        if r["employer"].upper() not in _SKIP_EMP
    ][:12]

    # ── geography ─────────────────────────────────────────────────────────────
    geo_rows = conn.execute("""
        SELECT CASE WHEN state = 'VA' THEN 'In-state' ELSE 'Out-of-state' END AS geo,
               ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM   fec_va_house_contributions
        WHERE  cand_id = ? AND amount > 0
        GROUP  BY geo ORDER BY total DESC
    """, (cand_id,)).fetchall()
    geo = [{"label": r["geo"], "total": r["total"], "donors": r["donors"]} for r in geo_rows]

    # ── monthly trend ─────────────────────────────────────────────────────────
    monthly_rows = conn.execute("""
        SELECT SUBSTR(contrib_date, 1, 7) AS month,
               ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM   fec_va_house_contributions
        WHERE  cand_id = ? AND amount > 0 AND contrib_date IS NOT NULL
        GROUP  BY month ORDER BY month
    """, (cand_id,)).fetchall()
    monthly = [
        {"month": r["month"], "total": r["total"], "donors": r["donors"]}
        for r in monthly_rows
    ]

    # ── donation size tiers ───────────────────────────────────────────────────
    tier_rows = conn.execute("""
        SELECT
            CASE
                WHEN amount < 200  THEN 'Small (<$200)'
                WHEN amount < 1000 THEN 'Mid ($200-$1K)'
                WHEN amount < 3300 THEN 'Large ($1K-$3.3K)'
                ELSE                    'Max-Out ($3.3K+)'
            END AS tier,
            ROUND(SUM(amount),0) AS total, COUNT(*) AS donors,
            CASE WHEN amount < 200 THEN 1 WHEN amount < 1000 THEN 2
                 WHEN amount < 3300 THEN 3 ELSE 4 END AS ord
        FROM   fec_va_house_contributions
        WHERE  cand_id = ? AND amount > 0
        GROUP  BY tier, ord ORDER BY ord
    """, (cand_id,)).fetchall()
    tiers = [
        {"label": r["tier"], "total": r["total"], "donors": r["donors"]}
        for r in tier_rows
    ]

    # ── PAC contributions ─────────────────────────────────────────────────────
    pac_rows = conn.execute("""
        SELECT pac_name, pac_id, pac_state,
               ROUND(SUM(amount),0) AS total, COUNT(*) AS n,
               GROUP_CONCAT(DISTINCT transaction_type) AS types
        FROM   fec_va_house_pac_contributions
        WHERE  cand_id = ? AND amount > 0
        GROUP  BY pac_id, pac_name ORDER BY total DESC
    """, (cand_id,)).fetchall()
    pacs = [
        {"name": r["pac_name"], "id": r["pac_id"], "state": r["pac_state"] or "",
         "total": r["total"], "n": r["n"], "types": r["types"] or ""}
        for r in pac_rows
    ]

    # ── summary aggregates ────────────────────────────────────────────────────
    itemized = conn.execute(
        "SELECT ROUND(SUM(amount),0), COUNT(*) FROM fec_va_house_contributions"
        " WHERE cand_id = ? AND amount > 0", (cand_id,)
    ).fetchone()
    itemized_total = itemized[0] or 0
    itemized_donors = itemized[1] or 0
    pac_total = sum(p["total"] for p in pacs)

    grassroots_row = conn.execute(
        "SELECT ROUND(SUM(amount),0), COUNT(*) FROM fec_va_house_contributions"
        " WHERE cand_id = ? AND amount < 200 AND amount > 0", (cand_id,)
    ).fetchone()
    grassroots_total = grassroots_row[0] or 0
    grassroots_pct = round(100 * grassroots_total / itemized_total, 1) if itemized_total else 0

    return {
        "cand_id":          cand_id,
        "display_name":     _fmt_name(cand["name"] or cand_id),
        "party":            cand["party"] or "?",
        "district":         cand["district"],
        "ici":              cand["ici"] or "",
        "ici_label":        _ICI_LABEL.get(cand["ici"] or "", "Unknown"),
        "committee_id":     cand["committee_id"] or "",
        "cycle":            cand["cycle"] or 2026,
        "total_receipts":   round(cand["total_receipts"] or 0, 0),
        "ind_contributions":round(cand["ind_contributions"] or 0, 0),
        "total_disbursements": round(cand["total_disbursements"] or 0, 0),
        "cash_on_hand":     round(cand["cash_on_hand"] or 0, 0),
        "itemized_total":   itemized_total,
        "itemized_donors":  itemized_donors,
        "pac_total":        pac_total,
        "grassroots_total": grassroots_total,
        "grassroots_pct":   grassroots_pct,
        "sectors":          sectors,
        "issue_areas":      _build_issue_areas(sectors),
        "employers":        employers,
        "geo":              geo,
        "monthly":          monthly,
        "tiers":            tiers,
        "pacs":             [_tag_pac(p) for p in pacs],
    }


@router.get("/candidate/federal/{cand_id}", response_class=HTMLResponse)
def federal_candidate_page(cand_id: str):
    conn = _conn()
    try:
        data = _fetch(conn, cand_id.upper())
        return _inject("candidate_profile_federal.html", "_PROFILE_DATA", data)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Profile unavailable: {exc}") from exc
    finally:
        conn.close()


@router.get("/api/candidate/federal/{cand_id}")
def federal_candidate_api(cand_id: str):
    conn = _conn()
    try:
        return _fetch(conn, cand_id.upper())
    except HTTPException:
        raise
    finally:
        conn.close()
