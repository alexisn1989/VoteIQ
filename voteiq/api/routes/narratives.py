"""
Legislator narrative API — serves pre-generated civic profiles.

GET /api/legislator-narrative?name=Emily Jordan
GET /api/legislator-narrative?hod=17          (by HOD district number)
GET /api/legislator-narrative?sd=4            (by Senate district number)
GET /api/legislator-narratives/batch?names=X,Y,Z  (up to 6 names)
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["narratives"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "polls.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_POLLS_DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    d = dict(row)
    if d.get("key_stats_json"):
        try:
            d["key_stats"] = json.loads(d.pop("key_stats_json"))
        except Exception:
            d.pop("key_stats_json", None)
    return d


@router.get("/legislator-narrative")
def get_narrative(
    name: Optional[str] = Query(None, description="Legislator name (partial match OK)"),
    hod:  Optional[int] = Query(None, description="House of Delegates district number"),
    sd:   Optional[int] = Query(None, description="State Senate district number"),
):
    """Return the pre-generated civic profile for a single legislator."""
    conn = _conn()

    row = None
    if name:
        # Exact match first
        row = conn.execute(
            "SELECT * FROM legislator_narratives WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        if not row:
            # Last-name partial match
            last = name.split()[-1] if name.split() else name
            row = conn.execute(
                "SELECT * FROM legislator_narratives WHERE lower(name) LIKE lower(?)"
                " ORDER BY length(name) ASC LIMIT 1",
                (f"%{last}%",),
            ).fetchone()
    elif hod is not None:
        row = conn.execute(
            "SELECT * FROM legislator_narratives "
            "WHERE chamber LIKE '%Delegate%' AND district = ? LIMIT 1",
            (str(hod),),
        ).fetchone()
    elif sd is not None:
        row = conn.execute(
            "SELECT * FROM legislator_narratives "
            "WHERE chamber LIKE '%Senate%' AND district = ? LIMIT 1",
            (str(sd),),
        ).fetchone()

    conn.close()

    if not row:
        return JSONResponse({"found": False, "narrative_short": None, "key_stats": {}})

    d = _row_to_dict(row)
    d["found"] = True
    # Don't return the full narrative in this lightweight endpoint — keep it fast
    d.pop("narrative", None)
    return JSONResponse(d)


@router.get("/legislator-narratives/batch")
def get_narratives_batch(
    names: str = Query(..., description="Comma-separated legislator names (up to 6)"),
):
    """
    Return short profiles for up to 6 legislators by name.
    Used to pre-load rep profiles after district detection.
    """
    name_list = [n.strip() for n in names.split(",") if n.strip()][:6]
    if not name_list:
        return JSONResponse([])

    conn = _conn()
    results = []
    for name in name_list:
        last = name.split()[-1] if name.split() else name
        row = conn.execute(
            "SELECT legislator_id, name, chamber, district, party, "
            "narrative_short, key_stats_json "
            "FROM legislator_narratives "
            "WHERE lower(name) = lower(?) OR lower(name) LIKE lower(?) "
            "ORDER BY length(name) ASC LIMIT 1",
            (name, f"%{last}%"),
        ).fetchone()
        if row:
            d = _row_to_dict(row)
            d["found"] = True
            d["queried_name"] = name
            results.append(d)

    conn.close()
    return JSONResponse(results)
