"""Donor heat maps — state races and federal races, separate pages."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["donor_map"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")
_CENTROIDS_FILE = _BASE_DIR / "data" / "zip_centroids.json"
_MAPBOX_TOKEN   = os.getenv("MAPBOX_TOKEN", "")

# Skip states/territories that aren't US states
_SKIP_STATES = {"ZZ", "PR", "GU", "VI", "AS", "MP", "AA", "AE", "AP"}

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


def _inject(data: dict, template: str) -> str:
    safe  = json.dumps(data, default=str).replace("</script>", "<\\/script>")
    token = json.dumps(_MAPBOX_TOKEN)
    with (_BASE_DIR / "templates" / template).open("r", encoding="utf-8") as f:
        html = f.read()
    return html.replace(
        "</head>",
        f"<script>window._MAP_DATA={safe};window._MAPBOX_TOKEN={token};</script></head>",
        1,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  STATE DONOR MAP  (/donor-map-state)
# ══════════════════════════════════════════════════════════════════════════════

def _state_donor_data(conn) -> dict:
    state_rows = conn.execute("""
        SELECT state_code AS state,
               ROUND(SUM(amount), 0) AS total,
               COUNT(*)              AS donors
        FROM   va_cf_schedule_a
        WHERE  state_code IS NOT NULL
          AND  state_code != ''
          AND  length(state_code) = 2
        GROUP  BY state_code
        ORDER  BY total DESC
    """).fetchall()

    zip_rows = conn.execute("""
        SELECT substr(zip_code, 1, 5) AS z5,
               MAX(city)              AS city,
               ROUND(SUM(amount), 0)  AS total,
               COUNT(*)               AS donors
        FROM   va_cf_schedule_a
        WHERE  state_code = 'VA'
          AND  zip_code IS NOT NULL
          AND  length(zip_code) >= 5
          AND  zip_code GLOB '[0-9]*'
        GROUP  BY z5
        HAVING total >= 50000
        ORDER  BY total DESC
    """).fetchall()

    va_zips = []
    for r in zip_rows:
        coords = _CENTROIDS.get(r["z5"])
        if coords:
            va_zips.append({
                "zip": r["z5"], "city": (r["city"] or "").title(),
                "lat": coords[0], "lng": coords[1],
                "total": r["total"], "donors": r["donors"],
            })

    states = [dict(r) for r in state_rows
              if r["state"] not in _SKIP_STATES]
    return {"source": "state", "states": states, "va_zips": va_zips}


@router.get("/api/donor-map-state")
def api_donor_map_state():
    conn = _conn()
    try:
        return _state_donor_data(conn)
    finally:
        conn.close()


@router.get("/donor-map-state", response_class=HTMLResponse)
def donor_map_state_page():
    """State-race donor heat map — US choropleth + Virginia zip detail."""
    conn = _conn()
    try:
        return _inject(_state_donor_data(conn), "donor_map_state.html")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  FEDERAL DONOR MAP  (/donor-map-federal)
# ══════════════════════════════════════════════════════════════════════════════

def _norm_name(raw: str) -> str:
    """'Lastname, Firstname M.' → 'Firstname Lastname'."""
    raw = raw.strip()
    if "," in raw:
        last, first = raw.split(",", 1)
        first = re.sub(r"\s+[A-Z]\.$", "", first.strip())
        return f"{first.strip()} {last.strip()}"
    return raw


def _federal_donor_data(conn) -> dict:
    rows = conn.execute("""
        SELECT candidate_name, bioguide_id, state,
               ROUND(SUM(amount), 0) AS total,
               COUNT(*)              AS donors
        FROM   fec_individual_contributions
        WHERE  state IS NOT NULL AND state != ''
          AND  candidate_name IS NOT NULL
        GROUP  BY candidate_name, bioguide_id, state
        ORDER  BY total DESC
    """).fetchall()

    # Merge rows by bioguide_id (where available) or normalised name
    key_to_name: dict[str, str] = {}
    cand_states: dict[str, dict[str, dict]] = {}

    for r in rows:
        if r["state"] in _SKIP_STATES:
            continue
        bid  = (r["bioguide_id"] or "").strip()
        norm = _norm_name(r["candidate_name"])
        key  = bid if bid else norm.lower()

        if key not in key_to_name:
            key_to_name[key] = norm
        cname = key_to_name[key]

        cand_states.setdefault(cname, {})
        s = r["state"]
        if s not in cand_states[cname]:
            cand_states[cname][s] = {"state": s, "total": 0, "donors": 0}
        cand_states[cname][s]["total"]  += r["total"]
        cand_states[cname][s]["donors"] += r["donors"]

    # All-candidate aggregate
    all_rows = conn.execute("""
        SELECT state,
               ROUND(SUM(amount), 0) AS total,
               COUNT(*)              AS donors
        FROM   fec_individual_contributions
        WHERE  state IS NOT NULL AND state != ''
        GROUP  BY state
        ORDER  BY total DESC
    """).fetchall()

    states_all = [dict(r) for r in all_rows
                  if r["state"] not in _SKIP_STATES]

    by_candidate = {
        name: sorted(states.values(), key=lambda x: -x["total"])
        for name, states in cand_states.items()
    }
    candidates = sorted(by_candidate, key=lambda c: -sum(s["total"] for s in by_candidate[c]))

    return {
        "source":       "federal",
        "states_all":   states_all,
        "by_candidate": by_candidate,
        "candidates":   candidates,
    }


@router.get("/api/donor-map-federal")
def api_donor_map_federal():
    conn = _conn()
    try:
        return _federal_donor_data(conn)
    finally:
        conn.close()


@router.get("/donor-map-federal", response_class=HTMLResponse)
def donor_map_federal_page():
    """Federal-race donor map — US choropleth with per-candidate filter."""
    conn = _conn()
    try:
        return _inject(_federal_donor_data(conn), "donor_map_federal.html")
    finally:
        conn.close()


# ── Legacy redirect ───────────────────────────────────────────────────────────

@router.get("/donor-map", response_class=HTMLResponse)
def donor_map_redirect():
    return RedirectResponse("/donor-map-state", status_code=301)
