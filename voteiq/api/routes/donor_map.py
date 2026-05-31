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
_CENTROIDS_FILE  = _BASE_DIR / "data" / "zip_centroids.json"
_CACHE_FILE      = _BASE_DIR / "data" / "donor_map_cache.json"
_MAPBOX_TOKEN    = os.getenv("MAPBOX_TOKEN", "")

# Skip states/territories that aren't US states
_SKIP_STATES = {"ZZ", "PR", "GU", "VI", "AS", "MP", "AA", "AE", "AP"}

try:
    with _CENTROIDS_FILE.open("r", encoding="utf-8") as _f:
        _CENTROIDS: dict[str, list[float]] = json.load(_f)
except FileNotFoundError:
    _CENTROIDS = {}

# Pre-computed cache — eliminates slow JOIN queries at request time
try:
    with _CACHE_FILE.open("r", encoding="utf-8") as _f:
        _DONOR_CACHE: dict = json.load(_f)
    print("[donor-map] Cache loaded from donor_map_cache.json")
except FileNotFoundError:
    _DONOR_CACHE = {}


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

def _zip_rows_to_list(rows) -> list[dict]:
    out = []
    for r in rows:
        coords = _CENTROIDS.get(r["z5"])
        if coords:
            out.append({
                "zip": r["z5"], "city": (r["city"] or "").title(),
                "lat": coords[0], "lng": coords[1],
                "total": r["total"], "donors": r["donors"],
            })
    return out


_STATE_SQL = """
    SELECT state_code AS state, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a
    WHERE  state_code IS NOT NULL AND state_code != '' AND length(state_code)=2
    GROUP  BY state_code ORDER BY total DESC
"""
_STATE_PARTY_SQL = """
    SELECT a.state_code AS state, ROUND(SUM(a.amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a a
    JOIN   va_cf_reports r ON r.ReportUID = a.report_uid
    WHERE  r.Party = ? AND a.state_code IS NOT NULL
      AND  a.state_code != '' AND length(a.state_code)=2
    GROUP  BY a.state_code ORDER BY total DESC
"""
_ZIP_SQL = """
    SELECT substr(zip_code,1,5) AS z5, MAX(city) AS city,
           ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a
    WHERE  state_code='VA' AND zip_code IS NOT NULL
      AND  length(zip_code)>=5 AND zip_code GLOB '[0-9]*'
    GROUP  BY z5 HAVING total>=50000 ORDER BY total DESC
"""
_ZIP_PARTY_SQL = """
    SELECT substr(a.zip_code,1,5) AS z5, MAX(a.city) AS city,
           ROUND(SUM(a.amount),0) AS total, COUNT(*) AS donors
    FROM   va_cf_schedule_a a
    JOIN   va_cf_reports r ON r.ReportUID = a.report_uid
    WHERE  r.Party = ? AND a.state_code='VA'
      AND  a.zip_code IS NOT NULL AND length(a.zip_code)>=5
      AND  a.zip_code GLOB '[0-9]*'
    GROUP  BY z5 HAVING total>=25000 ORDER BY total DESC
"""


def _state_donor_data(conn) -> dict:
    states     = [dict(r) for r in conn.execute(_STATE_SQL).fetchall()       if r["state"] not in _SKIP_STATES]
    states_dem = [dict(r) for r in conn.execute(_STATE_PARTY_SQL, ("Democratic",)).fetchall() if r["state"] not in _SKIP_STATES]
    states_rep = [dict(r) for r in conn.execute(_STATE_PARTY_SQL, ("Republican",)).fetchall() if r["state"] not in _SKIP_STATES]

    va_zips     = _zip_rows_to_list(conn.execute(_ZIP_SQL).fetchall())
    va_zips_dem = _zip_rows_to_list(conn.execute(_ZIP_PARTY_SQL, ("Democratic",)).fetchall())
    va_zips_rep = _zip_rows_to_list(conn.execute(_ZIP_PARTY_SQL, ("Republican",)).fetchall())

    return {
        "source": "state",
        "states": states, "states_dem": states_dem, "states_rep": states_rep,
        "va_zips": va_zips, "va_zips_dem": va_zips_dem, "va_zips_rep": va_zips_rep,
    }


@router.get("/api/donor-map-state")
def api_donor_map_state():
    if "state" in _DONOR_CACHE:
        return _DONOR_CACHE["state"]
    conn = _conn()
    try:
        return _state_donor_data(conn)
    finally:
        conn.close()


@router.get("/donor-map-state", response_class=HTMLResponse)
def donor_map_state_page():
    """State-race donor heat map — US choropleth + Virginia zip detail."""
    if "state" in _DONOR_CACHE:
        return _inject(_DONOR_CACHE["state"], "donor_map_state.html")
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
    # Candidate→party lookup from congress_members
    party_map: dict[str, str] = {}   # bioguide_id → party
    for r in conn.execute("SELECT bioguide_id, party FROM congress_members").fetchall():
        party_map[r["bioguide_id"]] = r["party"]

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

    key_to_name:  dict[str, str]  = {}
    key_to_party: dict[str, str]  = {}
    cand_states:  dict[str, dict] = {}

    for r in rows:
        if r["state"] in _SKIP_STATES:
            continue
        bid   = (r["bioguide_id"] or "").strip()
        norm  = _norm_name(r["candidate_name"])
        key   = bid if bid else norm.lower()

        if key not in key_to_name:
            key_to_name[key]  = norm
            key_to_party[key] = party_map.get(bid, "Unknown") if bid else "Unknown"
        cname = key_to_name[key]

        cand_states.setdefault(cname, {})
        s = r["state"]
        if s not in cand_states[cname]:
            cand_states[cname][s] = {"state": s, "total": 0, "donors": 0}
        cand_states[cname][s]["total"]  += r["total"]
        cand_states[cname][s]["donors"] += r["donors"]

    # Party-split aggregate queries
    def _fed_party_states(party: str) -> list[dict]:
        rows2 = conn.execute("""
            SELECT f.state, ROUND(SUM(f.amount),0) AS total, COUNT(*) AS donors
            FROM   fec_individual_contributions f
            JOIN   congress_members m ON m.bioguide_id = f.bioguide_id
            WHERE  m.party = ? AND f.state IS NOT NULL AND f.state != ''
            GROUP  BY f.state ORDER BY total DESC
        """, (party,)).fetchall()
        return [dict(r) for r in rows2 if r["state"] not in _SKIP_STATES]

    all_rows = conn.execute("""
        SELECT state, ROUND(SUM(amount),0) AS total, COUNT(*) AS donors
        FROM   fec_individual_contributions
        WHERE  state IS NOT NULL AND state != ''
        GROUP  BY state ORDER BY total DESC
    """).fetchall()

    states_all = [dict(r) for r in all_rows if r["state"] not in _SKIP_STATES]
    states_dem = _fed_party_states("Democratic")
    states_rep = _fed_party_states("Republican")

    by_candidate = {
        name: sorted(states.values(), key=lambda x: -x["total"])
        for name, states in cand_states.items()
    }
    # candidates as list of {name, party}
    candidates = sorted(
        [{"name": k, "party": key_to_party[key]}
         for key, k in key_to_name.items()],
        key=lambda c: -sum(s["total"] for s in by_candidate[c["name"]])
    )

    return {
        "source":       "federal",
        "states_all":   states_all,
        "states_dem":   states_dem,
        "states_rep":   states_rep,
        "by_candidate": by_candidate,
        "candidates":   candidates,
    }


@router.get("/api/donor-map-federal")
def api_donor_map_federal():
    if "federal" in _DONOR_CACHE:
        return _DONOR_CACHE["federal"]
    conn = _conn()
    try:
        return _federal_donor_data(conn)
    finally:
        conn.close()


@router.get("/donor-map-federal", response_class=HTMLResponse)
def donor_map_federal_page():
    """Federal-race donor map — US choropleth with per-candidate filter."""
    if "federal" in _DONOR_CACHE:
        return _inject(_DONOR_CACHE["federal"], "donor_map_federal.html")
    conn = _conn()
    try:
        return _inject(_federal_donor_data(conn), "donor_map_federal.html")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  DONOR–LEGISLATION CORRELATION  (/donor-legislation)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/donor-legislation", response_class=HTMLResponse)
def donor_legislation_page():
    """Donor-to-legislation alignment for VA state legislators."""
    data = _DONOR_CACHE.get("state", {})
    return _inject(data, "donor_legislation.html")


# ── Legacy redirect ───────────────────────────────────────────────────────────

@router.get("/donor-map", response_class=HTMLResponse)
def donor_map_redirect():
    return RedirectResponse("/donor-map-state", status_code=301)
