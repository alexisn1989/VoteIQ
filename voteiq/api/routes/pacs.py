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
        _PAC_SPENDING_CACHE: list[dict] | None = json.load(_f)
    print(f"[pacs] Cache loaded: {len(_PAC_SPENDING_CACHE)} PACs from pac_spending_cache.json")
except Exception:
    _PAC_SPENDING_CACHE = None


def _conn():
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    c = sqlite3.connect(_POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


def _pac_list(conn) -> list[dict]:
    """Aggregate independent expenditures by PAC with ideology metadata."""
    rows = conn.execute("""
        SELECT
            ie.committee_name,
            ie.committee_id,
            pi.short_name,
            pi.ideology,
            pi.alignment,
            pi.issue_focus,
            ROUND(SUM(CASE WHEN ie.support_oppose = 'S' THEN ie.expenditure_amount ELSE 0 END), 0) AS support_total,
            ROUND(SUM(CASE WHEN ie.support_oppose = 'O' THEN ie.expenditure_amount ELSE 0 END), 0) AS oppose_total,
            ROUND(SUM(ie.expenditure_amount), 0) AS grand_total,
            COUNT(DISTINCT ie.candidate_name) AS num_candidates,
            MIN(ie.cycle) AS cycle
        FROM fec_independent_expenditures ie
        LEFT JOIN pac_ideology pi
               ON upper(trim(pi.committee_name)) = upper(trim(ie.committee_name))
        GROUP BY ie.committee_name, ie.committee_id
        ORDER BY grand_total DESC
    """).fetchall()

    # For each PAC grab the candidate breakdown (support + oppose separately)
    pac_candidates = {}
    for r in conn.execute("""
        SELECT committee_name, candidate_name, support_oppose,
               ROUND(SUM(expenditure_amount), 0) AS total
        FROM fec_independent_expenditures
        GROUP BY committee_name, candidate_name, support_oppose
        ORDER BY total DESC
    """).fetchall():
        name = r["committee_name"]
        pac_candidates.setdefault(name, []).append({
            "candidate": r["candidate_name"],
            "support_oppose": r["support_oppose"],
            "total": r["total"],
        })

    out = []
    for r in rows:
        d = dict(r)
        d["candidates"] = pac_candidates.get(r["committee_name"], [])
        out.append(d)
    return out


def _inject(data: list[dict]) -> str:
    safe = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    with (_BASE_DIR / "templates" / "pac_spending.html").open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace("</head>", f"<script>window._PAC_DATA={safe};</script></head>", 1)


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/pac-spending")
def api_pac_spending():
    """Return PAC independent expenditure data for VA federal races."""
    if _PAC_SPENDING_CACHE is not None:
        return {"pacs": _PAC_SPENDING_CACHE, "total": len(_PAC_SPENDING_CACHE)}
    conn = _conn()
    try:
        data = _pac_list(conn)
        return {"pacs": data, "total": len(data)}
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"PAC data not yet available: {exc}")
    finally:
        conn.close()


# ── HTML page ─────────────────────────────────────────────────────────────────

@router.get("/pac-spending", response_class=HTMLResponse)
def pac_spending_page():
    """PAC independent expenditure table for VA federal races."""
    if _PAC_SPENDING_CACHE is not None:
        return _inject(_PAC_SPENDING_CACHE)
    conn = _conn()
    try:
        data = _pac_list(conn)
        return _inject(data)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"PAC data not yet available: {exc}")
    finally:
        conn.close()
