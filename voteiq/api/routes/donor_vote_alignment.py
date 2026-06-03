"""
Donor-vote alignment endpoints.

Endpoints:
  GET /api/donor-vote-alignment             — leaderboard (most/least aligned)
  GET /api/donor-vote-alignment/{name}      — single legislator breakdown
  GET /api/donor-vote-alignment/sector/{s}  — all legislators for a sector
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["donor_vote_alignment"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_DATA_DIR = os.getenv("DATA_DIR", str(_BASE_DIR))
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_POLLS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("by_sector_json"):
        try:
            d["by_sector"] = json.loads(d.pop("by_sector_json"))
        except Exception:
            d["by_sector"] = []
    return d


# ── Leaderboard ────────────────────────────────────────────────────────────────

@router.get("/donor-vote-alignment")
def get_alignment_leaderboard(
    sector: Optional[str] = Query(None, description="Filter by top_donor_sector"),
    min_votes: int = Query(5, description="Min sector vote count"),
    limit: int = Query(50, le=200),
    order: str = Query("delta_desc", description="delta_desc|delta_asc|sector_rate_desc|name"),
):
    """
    Return legislators ranked by alignment_delta — how much more (or less)
    they vote YES on bills matching their top donor sector vs. everything else.

    Positive delta  → legislator votes YES more on donor-sector bills.
    Negative delta  → legislator votes YES less on donor-sector bills.
    """
    conn = _conn()

    order_clause = {
        "delta_desc":      "alignment_delta DESC",
        "delta_asc":       "alignment_delta ASC",
        "sector_rate_desc": "sector_yes_rate DESC",
        "name":            "name ASC",
    }.get(order, "alignment_delta DESC")

    params: list = [min_votes]
    sector_filter = ""
    if sector:
        sector_filter = "AND top_donor_sector = ?"
        params.append(sector)
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT legislator_id, name, party, chamber,
               top_donor_sector, top_sector_amt,
               sector_yes_rate, other_yes_rate, alignment_delta,
               sector_vote_count, other_vote_count,
               by_sector_json, updated_at
        FROM donor_vote_alignment
        WHERE alignment_delta IS NOT NULL
          AND sector_vote_count >= ?
          {sector_filter}
        ORDER BY {order_clause}
        LIMIT ?
        """,
        params,
    ).fetchall()
    conn.close()

    return JSONResponse([_row_to_dict(r) for r in rows])


# ── Per-legislator ──────────────────────────────────────────────────────────────

@router.get("/donor-vote-alignment/sector/{sector_name}")
def get_alignment_by_sector(
    sector_name: str,
    min_votes: int = Query(3),
    limit: int = Query(100, le=200),
):
    """All legislators whose top donor sector matches sector_name."""
    conn = _conn()
    rows = conn.execute(
        """
        SELECT legislator_id, name, party, chamber,
               top_donor_sector, top_sector_amt,
               sector_yes_rate, other_yes_rate, alignment_delta,
               sector_vote_count, other_vote_count,
               by_sector_json, updated_at
        FROM donor_vote_alignment
        WHERE top_donor_sector = ?
          AND sector_vote_count >= ?
        ORDER BY alignment_delta DESC
        LIMIT ?
        """,
        (sector_name, min_votes, limit),
    ).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(404, f"No alignment data for sector '{sector_name}'")
    return JSONResponse([_row_to_dict(r) for r in rows])


@router.get("/donor-vote-alignment/{legislator_name}")
def get_alignment_for_legislator(legislator_name: str):
    """Full donor-vote alignment breakdown for a single legislator."""
    conn = _conn()
    # Try exact name match first, then LIKE
    row = conn.execute(
        """
        SELECT * FROM donor_vote_alignment
        WHERE lower(name) = lower(?)
        """,
        (legislator_name,),
    ).fetchone()

    if not row:
        row = conn.execute(
            """
            SELECT * FROM donor_vote_alignment
            WHERE name LIKE ?
            ORDER BY length(name) ASC
            LIMIT 1
            """,
            (f"%{legislator_name}%",),
        ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, f"No alignment data for '{legislator_name}'")

    data = _row_to_dict(row)

    # Add human-readable interpretation
    delta = data.get("alignment_delta")
    sector = data.get("top_donor_sector", "")
    if delta is not None:
        if delta >= 15:
            data["interpretation"] = (
                f"Strong alignment: votes YES on {sector} bills "
                f"{delta:.1f}pp more than other bills"
            )
        elif delta >= 5:
            data["interpretation"] = (
                f"Moderate alignment: votes YES on {sector} bills "
                f"{delta:.1f}pp more than other bills"
            )
        elif delta <= -15:
            data["interpretation"] = (
                f"Contrary pattern: votes YES on {sector} bills "
                f"{abs(delta):.1f}pp LESS than other bills despite donor ties"
            )
        elif delta <= -5:
            data["interpretation"] = (
                f"Slight contrary pattern: votes YES on {sector} bills "
                f"{abs(delta):.1f}pp less than other bills"
            )
        else:
            data["interpretation"] = (
                f"No clear alignment: vote pattern on {sector} bills "
                f"is close to overall average"
            )

    return JSONResponse(data)
