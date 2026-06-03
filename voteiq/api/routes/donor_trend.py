"""
Donor cycle trend endpoints — investment-return signal.

Endpoints:
  GET /api/donor-trend                     — leaderboard of biggest institutional spikes
  GET /api/donor-trend/{donor_name}        — full sparkline + context for one donor
  GET /api/donor-trend/sector/{sector}     — all donors spiking in a given sector
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["donor_trend"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_POLLS_DB = os.path.join(os.getenv("DATA_DIR", str(_BASE_DIR)), "polls.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_POLLS_DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ── Leaderboard ───────────────────────────────────────────────────────────────

@router.get("/donor-trend")
def donor_trend_leaderboard(
    min_amount:  float = Query(500_000, description="Min spike-cycle amount"),
    min_zscore:  float = Query(1.5,    description="Min z-score"),
    sector:      Optional[str] = Query(None),
    parity:      Optional[str] = Query(None, description="'odd' or 'even'"),
    limit:       int   = Query(50, le=200),
):
    """
    Top institutional donation spikes ranked by z-score.
    Filters to non-individual donors by default.
    """
    conn = _conn()
    params: list = [min_amount, min_zscore]
    filters = "AND is_individual = 0"

    if sector:
        filters += " AND donor_key IN (SELECT donor_key FROM donor_entity_map WHERE canonical_name LIKE ?)"
        params.append(f"%{sector}%")
    if parity:
        filters += " AND cycle_parity = ?"
        params.append(parity)
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT t.donor_key, t.canonical_name, t.election_cycle,
               t.cycle_parity, t.total_amount, t.gift_count, t.recipient_count,
               t.zscore, t.pct_change_prior, t.ratio_vs_mean,
               t.is_spike, t.spike_magnitude,
               e.variant_names, e.total_all_cycles, e.active_cycles
        FROM donor_cycle_trends t
        LEFT JOIN donor_entity_map e ON e.donor_key = t.donor_key
        WHERE t.total_amount >= ? AND t.zscore >= ? AND t.is_spike = 1
          {filters}
        ORDER BY t.zscore DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        if d.get("variant_names"):
            try:
                d["variant_names"] = json.loads(d["variant_names"])
            except Exception:
                pass
        results.append(d)
    return JSONResponse(results)


# ── Per-sector spikes ─────────────────────────────────────────────────────────

@router.get("/donor-trend/sector/{sector_name}")
def donor_trend_by_sector(
    sector_name: str,
    min_amount:  float = Query(100_000),
    limit:       int   = Query(100, le=200),
):
    """
    All donors whose canonical name matches sector_name (fuzzy),
    showing every cycle with spike flags.
    """
    conn = _conn()
    rows = conn.execute(
        """
        SELECT t.donor_key, t.canonical_name, t.election_cycle,
               t.cycle_parity, t.total_amount,
               t.zscore, t.pct_change_prior, t.ratio_vs_mean,
               t.is_spike, t.spike_magnitude
        FROM donor_cycle_trends t
        WHERE t.is_individual = 0
          AND t.total_amount >= ?
          AND t.donor_key IN (
              SELECT donor_key FROM donor_entity_map
              WHERE lower(canonical_name) LIKE lower(?)
          )
        ORDER BY t.canonical_name, t.election_cycle
        LIMIT ?
        """,
        (min_amount, f"%{sector_name}%", limit),
    ).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(404, f"No trend data matching '{sector_name}'")
    return JSONResponse([dict(r) for r in rows])


# ── Per-donor sparkline ───────────────────────────────────────────────────────

@router.get("/donor-trend/{donor_name}")
def donor_trend_detail(donor_name: str):
    """
    Full cycle history for a single donor: sparkline-ready per-cycle data
    annotated with legislative context for spike years.
    """
    conn = _conn()

    # Resolve donor_key — try canonical name match first, then fuzzy
    entity = conn.execute(
        """
        SELECT donor_key, canonical_name, variant_names,
               first_cycle, last_cycle, total_all_cycles, active_cycles
        FROM donor_entity_map
        WHERE lower(canonical_name) = lower(?)
        """,
        (donor_name,),
    ).fetchone()

    if not entity:
        entity = conn.execute(
            """
            SELECT donor_key, canonical_name, variant_names,
                   first_cycle, last_cycle, total_all_cycles, active_cycles
            FROM donor_entity_map
            WHERE lower(canonical_name) LIKE lower(?)
            ORDER BY total_all_cycles DESC
            LIMIT 1
            """,
            (f"%{donor_name}%",),
        ).fetchone()

    if not entity:
        raise HTTPException(404, f"No donor trend data for '{donor_name}'")

    donor_key      = entity["donor_key"]
    canonical_name = entity["canonical_name"]

    # Fetch all cycles for this donor
    cycles = conn.execute(
        """
        SELECT election_cycle, cycle_parity, total_amount, gift_count,
               recipient_count, zscore, pct_change_prior, ratio_vs_mean,
               is_spike, spike_magnitude, is_individual
        FROM donor_cycle_trends
        WHERE donor_key = ?
        ORDER BY election_cycle
        """,
        (donor_key,),
    ).fetchall()

    # For each spike cycle, fetch legislative context
    spike_cycles = [r["election_cycle"] for r in cycles if r["is_spike"]]
    context_by_cycle: dict[str, list[dict]] = {}

    if spike_cycles:
        placeholders = ",".join("?" for _ in spike_cycles)
        ctx_rows = conn.execute(
            f"""
            SELECT election_cycle, donor_sector, event_type, title,
                   description, bill_number, significance, source
            FROM cycle_context
            WHERE election_cycle IN ({placeholders})
            ORDER BY election_cycle, significance DESC, source
            """,
            spike_cycles,
        ).fetchall()
        for row in ctx_rows:
            cy = row["election_cycle"]
            context_by_cycle.setdefault(cy, []).append(dict(row))

    conn.close()

    # Build sparkline series
    sparkline = []
    for r in cycles:
        cy   = r["election_cycle"]
        item = dict(r)
        item["context"] = context_by_cycle.get(cy, [])
        if item["is_spike"] and not item["context"]:
            item["context"] = [{
                "election_cycle": cy,
                "donor_sector":   None,
                "event_type":     "unknown",
                "title":          f"{cy} legislative session",
                "description":    "No specific context found for this cycle.",
                "significance":   "low",
                "source":         "none",
            }]
        sparkline.append(item)

    # Summary stats
    odd_amounts  = [r["total_amount"] for r in cycles if r["cycle_parity"] == "odd"]
    even_amounts = [r["total_amount"] for r in cycles if r["cycle_parity"] == "even"]

    def _safe_mean(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 2) if vals else None

    summary = {
        "donor_key":       donor_key,
        "canonical_name":  canonical_name,
        "variant_names":   json.loads(entity["variant_names"] or "[]"),
        "first_cycle":     entity["first_cycle"],
        "last_cycle":      entity["last_cycle"],
        "total_all_cycles": entity["total_all_cycles"],
        "active_cycles":   entity["active_cycles"],
        "odd_year_mean":   _safe_mean(odd_amounts),
        "even_year_mean":  _safe_mean(even_amounts),
        "spike_cycles":    spike_cycles,
        "sparkline":       sparkline,
    }
    return JSONResponse(summary)
