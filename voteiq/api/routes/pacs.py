"""PAC independent expenditure table — sortable/filterable HTML + JSON API."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["pacs"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")
_CACHE_FILE = _BASE_DIR / "data" / "pac_spending_cache.json"

# Pre-computed cache — loaded at import time, eliminates slow queries on cold start
try:
    with _CACHE_FILE.open("r", encoding="utf-8") as _f:
        _CACHE: dict | None = json.load(_f)
    # support both old format (list) and new format (dict with pacs/candidates)
    if isinstance(_CACHE, list):
        _CACHE = {"pacs": _CACHE, "candidates": []}
    print(f"[pacs] Cache loaded: {len(_CACHE.get('pacs', []))} PACs, "
          f"{len(_CACHE.get('candidates', []))} candidates from pac_spending_cache.json")
except Exception:
    _CACHE = None


def _conn():
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    c = sqlite3.connect(_POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


# ── PAC-level aggregation ─────────────────────────────────────────────────────

def _pac_list(conn) -> list[dict]:
    """Aggregate independent expenditures by PAC with ideology metadata.

    JOIN uses short_name fallback so 'HMP' matches 'HOUSE MAJORITY PAC', etc.
    Candidate breakdown de-duplicated via bioguide_id → congress_members.name.
    """
    rows = conn.execute("""
        SELECT
            ie.committee_name,
            ie.committee_id,
            COALESCE(pi.short_name, pi2.short_name)     AS short_name,
            COALESCE(pi.ideology,   pi2.ideology)        AS ideology,
            COALESCE(pi.alignment,  pi2.alignment)       AS alignment,
            COALESCE(pi.issue_focus,pi2.issue_focus)     AS issue_focus,
            ROUND(SUM(CASE WHEN ie.support_oppose = 'S' THEN ie.expenditure_amount ELSE 0 END), 0) AS support_total,
            ROUND(SUM(CASE WHEN ie.support_oppose = 'O' THEN ie.expenditure_amount ELSE 0 END), 0) AS oppose_total,
            ROUND(SUM(ie.expenditure_amount), 0) AS grand_total,
            COUNT(DISTINCT ie.bioguide_id) AS num_candidates,
            MIN(ie.cycle) AS cycle
        FROM fec_independent_expenditures ie
        -- primary match: exact committee_name
        LEFT JOIN pac_ideology pi
               ON upper(trim(pi.committee_name)) = upper(trim(ie.committee_name))
        -- fallback: IE stores abbreviated name (e.g. 'HMP') that matches short_name
        LEFT JOIN pac_ideology pi2
               ON pi.id IS NULL
              AND upper(trim(pi2.short_name)) = upper(trim(ie.committee_name))
        GROUP BY ie.committee_name, ie.committee_id
        ORDER BY grand_total DESC
    """).fetchall()

    # Candidate breakdown grouped by bioguide_id to eliminate name variants
    pac_candidates: dict[str, list] = {}
    for r in conn.execute("""
        SELECT ie.committee_name,
               ie.bioguide_id,
               cm.name                              AS candidate_display,
               ie.support_oppose,
               ROUND(SUM(ie.expenditure_amount), 0) AS total
        FROM fec_independent_expenditures ie
        LEFT JOIN congress_members cm ON cm.bioguide_id = ie.bioguide_id
        GROUP BY ie.committee_name, ie.bioguide_id, ie.support_oppose
        ORDER BY total DESC
    """).fetchall():
        name = r["committee_name"]
        pac_candidates.setdefault(name, []).append({
            "candidate":      r["candidate_display"] or r["bioguide_id"],
            "bioguide_id":    r["bioguide_id"],
            "support_oppose": r["support_oppose"],
            "total":          r["total"],
        })

    # Cycle breakdown per PAC
    pac_cycles: dict[str, dict] = {}
    for r in conn.execute("""
        SELECT committee_name, cycle,
               ROUND(SUM(expenditure_amount), 0) AS total
        FROM fec_independent_expenditures
        GROUP BY committee_name, cycle
        ORDER BY committee_name, cycle
    """).fetchall():
        pac_cycles.setdefault(r["committee_name"], {})[r["cycle"]] = r["total"]

    out = []
    for r in rows:
        d = dict(r)
        d["candidates"]  = pac_candidates.get(r["committee_name"], [])
        d["by_cycle"]    = pac_cycles.get(r["committee_name"], {})
        out.append(d)
    return out


# ── Candidate-level aggregation ───────────────────────────────────────────────

def _candidate_view(conn) -> list[dict]:
    """Per-candidate PAC spending summary — for the 'By Candidate' tab."""
    rows = conn.execute("""
        SELECT ie.bioguide_id,
               cm.name                          AS candidate_name,
               cm.party,
               ie.office,
               ie.district,
               ROUND(SUM(CASE WHEN ie.support_oppose='S' THEN ie.expenditure_amount ELSE 0 END), 0) AS support,
               ROUND(SUM(CASE WHEN ie.support_oppose='O' THEN ie.expenditure_amount ELSE 0 END), 0) AS oppose,
               ROUND(SUM(ie.expenditure_amount), 0) AS total,
               MIN(ie.cycle)  AS first_cycle,
               MAX(ie.cycle)  AS last_cycle
        FROM fec_independent_expenditures ie
        LEFT JOIN congress_members cm ON cm.bioguide_id = ie.bioguide_id
        GROUP BY ie.bioguide_id
        ORDER BY total DESC
    """).fetchall()

    # Top PACs per candidate (up to 8)
    pac_by_cand: dict[str, list] = {}
    for r in conn.execute("""
        SELECT ie.bioguide_id, ie.committee_name,
               ROUND(SUM(CASE WHEN ie.support_oppose='S' THEN ie.expenditure_amount ELSE 0 END), 0) AS support,
               ROUND(SUM(CASE WHEN ie.support_oppose='O' THEN ie.expenditure_amount ELSE 0 END), 0) AS oppose,
               ROUND(SUM(ie.expenditure_amount), 0) AS total
        FROM fec_independent_expenditures ie
        GROUP BY ie.bioguide_id, ie.committee_name
        ORDER BY ie.bioguide_id, total DESC
    """).fetchall():
        bgid = r["bioguide_id"]
        if len(pac_by_cand.get(bgid, [])) < 8:
            pac_by_cand.setdefault(bgid, []).append({
                "pac":     r["committee_name"],
                "support": r["support"],
                "oppose":  r["oppose"],
                "total":   r["total"],
            })

    # Cycle breakdown per candidate
    cycle_by_cand: dict[str, dict] = {}
    for r in conn.execute("""
        SELECT bioguide_id, cycle,
               ROUND(SUM(CASE WHEN support_oppose='S' THEN expenditure_amount ELSE 0 END), 0) AS support,
               ROUND(SUM(CASE WHEN support_oppose='O' THEN expenditure_amount ELSE 0 END), 0) AS oppose,
               ROUND(SUM(expenditure_amount), 0) AS total
        FROM fec_independent_expenditures
        GROUP BY bioguide_id, cycle
        ORDER BY bioguide_id, cycle
    """).fetchall():
        bgid = r["bioguide_id"]
        cycle_by_cand.setdefault(bgid, {})[r["cycle"]] = {
            "support": r["support"],
            "oppose":  r["oppose"],
            "total":   r["total"],
        }

    out = []
    for r in rows:
        bgid = r["bioguide_id"]
        support = r["support"] or 0
        oppose  = r["oppose"]  or 0
        out.append({
            "bioguide_id":    bgid,
            "candidate_name": r["candidate_name"] or bgid,
            "party":          r["party"],
            "office":         r["office"],
            "district":       r["district"],
            "support":        support,
            "oppose":         oppose,
            "total":          r["total"],
            "net":            support - oppose,   # positive = net supported
            "top_pacs":       pac_by_cand.get(bgid, []),
            "by_cycle":       cycle_by_cand.get(bgid, {}),
        })
    return out


# ── HTML injection ────────────────────────────────────────────────────────────

def _inject(data: dict) -> str:
    pacs_json  = json.dumps(data["pacs"],       default=str).replace("</script>", "<\\/script>")
    cands_json = json.dumps(data["candidates"], default=str).replace("</script>", "<\\/script>")
    inject = (
        f"<script>window._PAC_DATA={pacs_json};"
        f"window._CAND_DATA={cands_json};</script>"
    )
    with (_BASE_DIR / "templates" / "pac_spending.html").open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", inject + "</head>", 1)


def _build_data(conn) -> dict:
    return {
        "pacs":       _pac_list(conn),
        "candidates": _candidate_view(conn),
    }


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/pac-spending")
def api_pac_spending():
    """Return PAC independent expenditure data for VA federal races."""
    if _CACHE is not None:
        return {
            "pacs":       _CACHE.get("pacs", []),
            "candidates": _CACHE.get("candidates", []),
            "total":      len(_CACHE.get("pacs", [])),
        }
    conn = _conn()
    try:
        data = _build_data(conn)
        return {"pacs": data["pacs"], "candidates": data["candidates"], "total": len(data["pacs"])}
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"PAC data not yet available: {exc}")
    finally:
        conn.close()


# ── HTML page ─────────────────────────────────────────────────────────────────

@router.get("/pac-spending", response_class=HTMLResponse)
def pac_spending_page():
    """PAC independent expenditure table for VA federal races."""
    if _CACHE is not None:
        return _inject(_CACHE)
    conn = _conn()
    try:
        data = _build_data(conn)
        return _inject(data)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"PAC data not yet available: {exc}")
    finally:
        conn.close()
