"""Donor heat map — US states choropleth + VA zip bubble map."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["donor_map"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")
_CENTROIDS_FILE  = _BASE_DIR / "data" / "zip_centroids.json"
_MAPBOX_TOKEN    = os.getenv("MAPBOX_TOKEN", "")

# Load centroid lookup once at import time
try:
    with _CENTROIDS_FILE.open("r", encoding="utf-8") as _f:
        _CENTROIDS: dict[str, list[float]] = json.load(_f)
except FileNotFoundError:
    _CENTROIDS = {}


def _conn():
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    c = sqlite3.connect(_POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


def _donor_map_data(conn) -> dict:
    # ── State-level totals (VA SBE data — all donor states) ──────────────────
    state_rows = conn.execute("""
        SELECT state_code                        AS state,
               ROUND(SUM(amount), 0)             AS total,
               COUNT(*)                          AS donors
        FROM   va_cf_schedule_a
        WHERE  state_code IS NOT NULL
          AND  state_code != ''
          AND  length(state_code) = 2
        GROUP  BY state_code
        ORDER  BY total DESC
    """).fetchall()

    # ── VA zip-code totals for bubble map ─────────────────────────────────────
    zip_rows = conn.execute("""
        SELECT substr(zip_code, 1, 5)            AS z5,
               MAX(city)                         AS city,
               ROUND(SUM(amount), 0)             AS total,
               COUNT(*)                          AS donors
        FROM   va_cf_schedule_a
        WHERE  state_code = 'VA'
          AND  zip_code IS NOT NULL
          AND  length(zip_code) >= 5
          AND  zip_code GLOB '[0-9]*'
        GROUP  BY z5
        HAVING total >= 50000
        ORDER  BY total DESC
    """).fetchall()

    # Enrich zip rows with centroid coords
    va_zips = []
    for r in zip_rows:
        z5 = r["z5"]
        coords = _CENTROIDS.get(z5)
        if coords:
            va_zips.append({
                "zip":    z5,
                "city":   (r["city"] or "").title(),
                "lat":    coords[0],
                "lng":    coords[1],
                "total":  r["total"],
                "donors": r["donors"],
            })

    return {
        "states":  [dict(r) for r in state_rows],
        "va_zips": va_zips,
    }


def _inject(data: dict) -> str:
    safe  = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    token = json.dumps(_MAPBOX_TOKEN)
    with (_BASE_DIR / "templates" / "donor_map.html").open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace(
        "</head>",
        f"<script>window._MAP_DATA={safe};window._MAPBOX_TOKEN={token};</script></head>",
        1,
    )


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/donor-map")
def api_donor_map():
    """Return donor geographic distribution data."""
    conn = _conn()
    try:
        return _donor_map_data(conn)
    finally:
        conn.close()


# ── HTML page ─────────────────────────────────────────────────────────────────

@router.get("/donor-map", response_class=HTMLResponse)
def donor_map_page():
    """Interactive donor heat map — US states + Virginia zip detail."""
    conn = _conn()
    try:
        data = _donor_map_data(conn)
        return _inject(data)
    finally:
        conn.close()
