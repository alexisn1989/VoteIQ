"""
Lobbyist cross-reference: registered VA lobbyists × campaign donors.

Endpoints:
  GET /lobbyist-crossref                  — aggregate view (top lobbying entities)
  GET /lobbyist-crossref/{candidate_name} — per-legislator enriched donor list
  GET /api/lobbyist-count/{principal}     — quick count for a single principal name
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["lobbyist"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_data_dir_raw = os.getenv("DATA_DIR", str(_BASE_DIR))
_DATA_DIR = _data_dir_raw if os.path.isdir(_data_dir_raw) else str(_BASE_DIR)
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")


def _conn() -> sqlite3.Connection:
    if not os.path.isfile(_POLLS_DB):
        raise HTTPException(status_code=503, detail="Database not available")
    c = sqlite3.connect(_POLLS_DB)
    c.row_factory = sqlite3.Row
    return c


# ── Name normalisation ────────────────────────────────────────────────────────

# Corporate / legal / PAC suffixes that are uninformative for matching
_STRIP = re.compile(
    r"\b(llc|inc|corp|co|ltd|lp|lllp|pllc|plc|pc|"
    r"pac|bankpac|politicalpac|"
    r"association|assoc|assn|"
    r"committee|council|coalition|federation|alliance|league|union|"
    r"foundation|institute|"
    r"fund|funding|"
    r"group|holding|holdings|"
    r"services|service|solutions|management|enterprises|resources|"
    r"company|companies|industries|industry|"
    r"international|national|american|america|"
    r"government|governmental|good|club|political|action|better|"
    r"future|common|united|general|"
    r"virginia|va)\b\.?",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9 ]")
_SPACES = re.compile(r"\s+")


def _brand_keyword(name: str) -> str:
    """Extract the most identifying brand word(s) from a company name.

    'Dominion Energy Inc. PAC Virginia'  -> 'dominion energy'
    'Altria Client Services Inc.'        -> 'altria client'
    'Virginia Bankers Association BankPAC' -> 'bankers'
    """
    if not name:
        return ""
    cleaned = _STRIP.sub(" ", name.lower())
    cleaned = _PUNCT.sub(" ", cleaned)
    cleaned = _SPACES.sub(" ", cleaned).strip()
    words = [w for w in cleaned.split() if len(w) >= 4]
    if not words:
        words = [w for w in cleaned.split() if len(w) >= 2]
    if not words:
        return name.lower().split()[0][:20]
    # Take first 2 meaningful words
    return " ".join(words[:2])


def _find_principals(conn: sqlite3.Connection,
                     keyword: str,
                     year_range: Optional[str] = None) -> list[dict]:
    """Return lobbyist rows for principals whose name contains keyword."""
    if len(keyword) < 3:
        return []
    params: list = [f"%{keyword}%"]
    sql = """
        SELECT principal_name,
               year_range,
               COUNT(DISTINCT lobbyist_name) AS lobbyist_count,
               GROUP_CONCAT(DISTINCT lobbyist_name) AS lobbyist_names
          FROM lobbyist_registrations
         WHERE lower(principal_name) LIKE lower(?)
    """
    if year_range:
        sql += " AND year_range = ?"
        params.append(year_range)
    sql += " GROUP BY principal_name, year_range"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Per-legislator cross-reference ───────────────────────────────────────────

@router.get("/lobbyist-crossref/{candidate_name}")
def lobbyist_crossref_candidate(
    candidate_name: str,
    cycle: str = Query("2026", description="Election cycle, e.g. 2026"),
    year_range: str = Query("", description="Lobbyist year range, e.g. 2025-2026"),
):
    """Return a legislator's top donors enriched with lobbyist registration counts.

    Sources top_donors_json from campaign_finance_summary (Virginia SBE records),
    then cross-references each donor against lobbyist_registrations.
    """
    conn = _conn()
    try:
        # Pull top_donors_json from campaign_finance_summary for this legislator
        row = conn.execute(
            """
            SELECT top_donors_json, latest_cycle
              FROM campaign_finance_summary
             WHERE lower(name) = lower(?)
             LIMIT 1
            """,
            (candidate_name,),
        ).fetchone()

        if not row or not row["top_donors_json"]:
            return JSONResponse({"candidate": candidate_name, "cycle": cycle,
                                 "donors_total": 0, "with_lobbyists": 0, "donors": []})

        import json as _json
        raw_donors = _json.loads(row["top_donors_json"])  # list of {contributor_name, total, ...}
        used_cycle = cycle or (row["latest_cycle"] or "2026")

        # Determine lobbyist year range from cycle
        lob_year = year_range
        if not lob_year:
            try:
                yr = int(used_cycle)
                lob_year = f"{yr-1}-{yr}"
            except Exception:
                lob_year = "2025-2026"

        results = []
        for d in raw_donors:
            donor_name = d.get("contributor_name") or d.get("donor_name") or ""
            total      = d.get("total") or 0
            sector     = d.get("sector") or ""
            if not donor_name:
                continue

            keyword    = _brand_keyword(donor_name)
            # Use first keyword word only for broader matching
            first_kw   = keyword.split()[0] if keyword else ""
            principals = _find_principals(conn, first_kw, lob_year) if len(first_kw) >= 3 else []

            entry = {
                "donor_name":      donor_name,
                "total":           total,
                "sector":          sector,
                "lobbyist_data":   principals,
                "total_lobbyists": sum(p["lobbyist_count"] for p in principals),
            }
            results.append(entry)

        results.sort(key=lambda x: (-x["total_lobbyists"], -x["total"]))

        return JSONResponse({
            "candidate":      candidate_name,
            "cycle":          used_cycle,
            "lobbyist_year":  lob_year,
            "donors_total":   len(results),
            "with_lobbyists": sum(1 for r in results if r["total_lobbyists"] > 0),
            "donors":         results,
        })
    finally:
        conn.close()


# ── Aggregate cross-reference ─────────────────────────────────────────────────

@router.get("/lobbyist-crossref")
def lobbyist_crossref_aggregate(
    year_range: str = Query("2025-2026", description="Lobbyist year range"),
    min_lobbyists: int = Query(3, description="Minimum lobbyist count to include"),
    limit: int = Query(50, description="Max entities to return"),
):
    """Return top lobbying entities ranked by lobbyist count × total donations.

    Aggregates across all VA legislators in polls.db.
    """
    conn = _conn()
    try:
        # All registered principals with their lobbyist counts
        principal_rows = conn.execute(
            """
            SELECT principal_name,
                   COUNT(DISTINCT lobbyist_name) AS lobbyist_count,
                   GROUP_CONCAT(DISTINCT lobbyist_name) AS lobbyist_names
              FROM lobbyist_registrations
             WHERE year_range = ?
             GROUP BY principal_name
            HAVING lobbyist_count >= ?
             ORDER BY lobbyist_count DESC
             LIMIT 200
            """,
            (year_range, min_lobbyists),
        ).fetchall()

        # For each principal, find matching donors in va_cf_schedule_a (corporate only)
        results = []
        for p in principal_rows:
            keyword  = _brand_keyword(p["principal_name"])
            first_kw = keyword.split()[0] if keyword else ""
            if len(first_kw) < 3:
                continue

            # Total donations from corporate donors matching this brand keyword
            donor_agg = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0)         AS total_donated,
                       COUNT(DISTINCT candidate_name)   AS legislator_count,
                       COUNT(DISTINCT last_or_company)  AS donor_entity_count
                  FROM va_cf_schedule_a
                 WHERE is_individual = 0
                   AND lower(last_or_company) LIKE lower(?)
                """,
                (f"%{first_kw}%",),
            ).fetchone()

            results.append({
                "principal_name":    p["principal_name"],
                "lobbyist_count":    p["lobbyist_count"],
                "lobbyist_names":    (p["lobbyist_names"] or "").split(",")[:5],
                "total_donated":     round(donor_agg["total_donated"] or 0, 2),
                "legislator_count":  donor_agg["legislator_count"] or 0,
                "donor_entity_count": donor_agg["donor_entity_count"] or 0,
                "keyword":           first_kw,
            })

        # Score = lobbyist_count * log(1 + total_donated)
        import math
        for r in results:
            r["influence_score"] = round(
                r["lobbyist_count"] * math.log1p(r["total_donated"]), 1
            )

        results.sort(key=lambda x: -x["influence_score"])
        results = results[:limit]

        return JSONResponse({
            "year_range":      year_range,
            "total_entities":  len(results),
            "entities":        results,
        })
    finally:
        conn.close()


# ── Quick lookup ──────────────────────────────────────────────────────────────

@router.get("/lobbyist-count")
def lobbyist_count(
    principal: str = Query(..., description="Principal/company name to look up"),
    year_range: str = Query("2025-2026"),
):
    """Return lobbyist count for a given principal name keyword."""
    conn = _conn()
    try:
        keyword = _brand_keyword(principal)
        rows = conn.execute(
            """
            SELECT principal_name,
                   COUNT(DISTINCT lobbyist_name) AS n,
                   GROUP_CONCAT(DISTINCT lobbyist_name) AS names
              FROM lobbyist_registrations
             WHERE lower(principal_name) LIKE lower(?) AND year_range = ?
             GROUP BY principal_name
             ORDER BY n DESC
            """,
            (f"%{keyword}%", year_range),
        ).fetchall()
        return JSONResponse({
            "principal":  principal,
            "keyword":    keyword,
            "year_range": year_range,
            "matches":    [dict(r) for r in rows],
            "total":      sum(r["n"] for r in rows),
        })
    finally:
        conn.close()
