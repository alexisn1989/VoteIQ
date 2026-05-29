"""VA Federal Delegation table — sortable/filterable HTML + JSON API."""
from __future__ import annotations

import json
import os
import sqlite3
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
    out = []
    for r in rows:
        d = dict(r)
        # Parse sector list for spark display
        d["by_sector"] = json.loads(d.pop("by_sector_json") or "[]")
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
