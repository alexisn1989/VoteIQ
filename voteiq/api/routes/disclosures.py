"""
Financial disclosure endpoints.

Endpoints:
  GET /api/disclosures/{name}   — SOEI disclosures for one legislator
  GET /api/conflicts            — conflict-of-interest signal leaderboard
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["disclosures"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_DATA_DIR = os.getenv("DATA_DIR", str(_BASE_DIR))
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_POLLS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── Per-legislator disclosures ─────────────────────────────────────────────────

@router.get("/disclosures/{name}")
def get_disclosures(
    name: str,
    year: Optional[str] = Query(None, description="Filing year, e.g. 2024"),
    part: Optional[str] = Query(None, description="employment|business_officer|real_estate|securities"),
):
    """
    Return all SOEI financial disclosure records for a legislator.
    Name match is case-insensitive substring (last name sufficient).
    """
    conn = _conn()
    try:
        params: list = [f"%{name}%"]
        sql = """
            SELECT legislator_name, filing_year, filing_id, part,
                   entity_name, role_or_type, sector, amount_range,
                   raw_text, scraped_at
            FROM legislator_financial_disclosures
            WHERE lower(legislator_name) LIKE lower(?)
              AND entity_name != 'NONE DISCLOSED'
        """
        if year:
            sql += " AND filing_year = ?"
            params.append(year)
        if part:
            sql += " AND part = ?"
            params.append(part)
        sql += " ORDER BY filing_year DESC, part"

        rows = conn.execute(sql, params).fetchall()
        if not rows:
            # Check whether they exist but have nothing to disclose
            any_row = conn.execute(
                "SELECT 1 FROM legislator_financial_disclosures WHERE lower(legislator_name) LIKE lower(?)",
                [f"%{name}%"],
            ).fetchone()
            if not any_row:
                raise HTTPException(404, f"No disclosure data found for '{name}'")
            return JSONResponse({"legislator": name, "disclosures": [], "note": "No disclosable interests on file"})

        # Group by year → part
        by_year: dict = {}
        for r in rows:
            yr = r["filing_year"] or "unknown"
            by_year.setdefault(yr, []).append({
                "part":         r["part"],
                "entity_name":  r["entity_name"],
                "role_or_type": r["role_or_type"],
                "sector":       r["sector"],
                "amount_range": r["amount_range"],
                "filing_id":    r["filing_id"],
                "scraped_at":   r["scraped_at"],
            })

        legislator_name = rows[0]["legislator_name"]
        return JSONResponse({
            "legislator": legislator_name,
            "years":      sorted(by_year.keys(), reverse=True),
            "disclosures": by_year,
            "source":     "vec_soei",
        })
    finally:
        conn.close()


# ── Conflict leaderboard ───────────────────────────────────────────────────────

@router.get("/conflicts")
def get_conflicts(
    min_signals: int = Query(2, description="Minimum overlap signals (1-3)"),
    sector: Optional[str] = Query(None, description="Filter by disclosure sector"),
    limit: int = Query(50, le=200),
):
    """
    Return legislators with overlapping financial disclosures, donor sectors,
    and voting patterns — ranked by number of corroborating signals (max 3).

    Signals:
      1. Disclosed business/employment sector matches top donor sector
      2. Total campaign donations from that sector > $50 k
      3. Vote-yes rate on sector bills deviates ≥ 5 pp from their overall rate
    """
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT
                d.legislator_name,
                d.sector                        AS disclosure_sector,
                d.entity_name                   AS disclosed_entity,
                d.part                          AS disclosure_type,
                d.filing_year,
                c.top_sector                    AS donor_top_sector,
                ROUND(c.total_raised, 0)        AS total_raised,
                ROUND(a.alignment_delta, 1)     AS vote_delta,
                a.sector_yes_rate               AS vote_yes_rate
            FROM legislator_financial_disclosures d
            LEFT JOIN campaign_finance_summary c
                   ON lower(c.name) LIKE '%' || lower(
                          substr(d.legislator_name,
                                 instr(d.legislator_name,' ')+1)) || '%'
                  AND c.source = 'va_sbe'
            LEFT JOIN donor_vote_alignment a
                   ON a.legislator_id = c.legislator_id
            WHERE d.sector IS NOT NULL
              AND d.entity_name != 'NONE DISCLOSED'
              AND d.source = 'vec_soei'
            ORDER BY d.legislator_name, d.sector
        """).fetchall()
    finally:
        conn.close()

    seen: set = set()
    results = []
    for r in rows:
        key = (r["legislator_name"], r["disclosure_sector"])
        if key in seen:
            continue
        seen.add(key)

        ds = (r["disclosure_sector"] or "").split("/")[0].lower()
        dts = (r["donor_top_sector"] or "").lower()
        sector_match = bool(ds and ds in dts)
        vote_signal  = r["vote_delta"] is not None and abs(r["vote_delta"]) >= 5
        donor_signal = bool(r["total_raised"] and r["total_raised"] > 50_000)
        signal_count = sum([sector_match, vote_signal, donor_signal])

        if signal_count < min_signals:
            continue
        if sector and sector.lower() not in (r["disclosure_sector"] or "").lower():
            continue

        results.append({
            "legislator":        r["legislator_name"],
            "disclosure_sector": r["disclosure_sector"],
            "disclosed_entity":  r["disclosed_entity"],
            "disclosure_type":   r["disclosure_type"],
            "filing_year":       r["filing_year"],
            "donor_top_sector":  r["donor_top_sector"],
            "total_raised":      r["total_raised"],
            "vote_delta":        r["vote_delta"],
            "vote_yes_rate":     r["vote_yes_rate"],
            "sector_overlap":    sector_match,
            "vote_signal":       vote_signal,
            "donor_signal":      donor_signal,
            "signal_count":      signal_count,
        })

    results.sort(key=lambda x: -x["signal_count"])
    return JSONResponse({
        "count":   len(results[:limit]),
        "conflicts": results[:limit],
    })
