"""
app/prediction/feature_queries.py

Feature engineering queries for the vote prediction pipeline.

Rules enforced here:
- All SQL uses parameterized queries — no f-string interpolation on user values.
- All legislator lookups go through va_name_index. Never join on raw name strings.
- va_cf_schedule_a is the only source for GA legislator contributions.
  va_cf_reports is not used.
- va_legislator_vote_summary is primary for yes/no aggregates; fall back to
  computing from va_legislator_recent_votes only when legislator+session is absent.
- Policy-area filtering uses a two-path fallback logged via classification_source:
    Path A ("classified")             — JOIN va_bill_policy_areas (after classify job)
    Path B ("title_keyword_fallback") — va_bills.title LIKE keyword
    "none"                            — policy area cannot be applied (donor data)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

SOURCE_CLASSIFIED = "classified"
SOURCE_KEYWORD    = "title_keyword_fallback"
SOURCE_NONE       = "none"

MIN_VOTE_COUNT         = 50
MIN_CONTRIBUTION_COUNT = 20

# Keyword fallback for each policy label — used when classify job has not run.
# Single short stem chosen to maximise recall in title LIKE queries.
_POLICY_KEYWORDS: dict[str, str] = {
    "EDUCATION":       "school",
    "HEALTHCARE":      "health",
    "TAXATION":        "tax",
    "ENVIRONMENT":     "environment",
    "CRIMINAL_JUSTICE":"criminal",
    "ELECTIONS":       "election",
    "TRANSPORTATION":  "transport",
    "ENERGY":          "energy",
    "HOUSING":         "housing",
    "LABOR":           "labor",
    "SOCIAL_SERVICES": "social",
    "AGRICULTURE":     "agricult",
    "BUDGET":          "budget",
    "OTHER":           "",
}

_PASS_TOKENS = {"passed", "signed", "enacted", "approved", "chaptered"}


# ── Internal helpers ─────────────────────────────────────────────────────────

def _resolve_name(conn: sqlite3.Connection, canonical_name: str) -> dict | None:
    """Look up voter/CF/DV table names and party from va_name_index."""
    row = conn.execute(
        """
        SELECT votes_table_name, cf_table_name, dv_table_name, party, chamber
        FROM va_name_index
        WHERE canonical_name = ?
        """,
        (canonical_name,),
    ).fetchone()
    if not row:
        logger.warning("_resolve_name: %r not found in va_name_index", canonical_name)
        return None
    return {
        "votes_table_name": row[0],
        "cf_table_name":    row[1],
        "dv_table_name":    row[2],
        "party":            row[3] or "",
        "chamber":          row[4] or "",
    }


def _policy_filter_path(conn: sqlite3.Connection, policy_area: str) -> tuple[str, str]:
    """
    Determine which policy-area path to use for this query.

    Returns (source, keyword):
      source=SOURCE_CLASSIFIED, keyword="" when Path A is available.
      source=SOURCE_KEYWORD, keyword=<stem> when falling back to title LIKE.
    """
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM va_bill_policy_areas WHERE policy_area = ?",
            (policy_area,),
        ).fetchone()[0]
        if n > 0:
            logger.debug("policy_filter_path: Path A for %r (%d bills)", policy_area, n)
            return SOURCE_CLASSIFIED, ""
    except Exception:
        pass  # table missing — fall through to keyword path

    keyword = _POLICY_KEYWORDS.get(
        policy_area, policy_area.lower().replace("_", " ")
    )
    logger.info(
        "policy_filter_path: Path B for %r — keyword=%r", policy_area, keyword
    )
    return SOURCE_KEYWORD, keyword


def _empty_consistency(source: str) -> dict:
    return {
        "total_votes": 0, "yes_rate": None, "party_loyalty_rate": None,
        "flip_rate": 0.0, "sufficient_data": False, "classification_source": source,
    }


def _empty_timing(source: str) -> dict:
    return {
        "avg_lead_days": None, "contribution_count": 0,
        "money_precedes_votes": False, "sufficient_data": False,
        "classification_source": source,
    }


def _empty_outcomes(source: str) -> dict:
    return {
        "pass_rate": None, "avg_yes_votes": None, "partisan_split": None,
        "total_bills": 0, "classification_source": source,
    }


# ── 2a. Vote consistency ─────────────────────────────────────────────────────

def get_vote_consistency(
    db: sqlite3.Connection, canonical_name: str, policy_area: str
) -> dict | None:
    """
    Returns:
      total_votes            int
      yes_rate               float (0–1) | None
      party_loyalty_rate     float (0–1) | None
      flip_rate              float (0–1) — bills where legislator changed position
      sufficient_data        bool  (False when total_votes < 50)
      classification_source  str
    """
    name_row = _resolve_name(db, canonical_name)
    if not name_row or not name_row["votes_table_name"]:
        logger.warning("get_vote_consistency: no votes mapping for %r", canonical_name)
        return None

    voter_name = name_row["votes_table_name"]
    party      = name_row["party"]
    source, kw = _policy_filter_path(db, policy_area)

    try:
        if source == SOURCE_CLASSIFIED:
            vote_counts = db.execute(
                """
                SELECT v.option, COUNT(*) AS n
                FROM va_legislator_recent_votes v
                JOIN va_bill_policy_areas pa
                  ON pa.bill_number = v.bill_id AND pa.session = v.session
                WHERE v.voter_name  = ?
                  AND pa.policy_area = ?
                  AND v.is_procedural = 0
                GROUP BY v.option
                """,
                (voter_name, policy_area),
            ).fetchall()

            flip_rows = db.execute(
                """
                SELECT v.bill_id,
                       COUNT(DISTINCT v.session) AS sessions,
                       COUNT(DISTINCT v.option)  AS distinct_opts
                FROM va_legislator_recent_votes v
                JOIN va_bill_policy_areas pa
                  ON pa.bill_number = v.bill_id AND pa.session = v.session
                WHERE v.voter_name  = ?
                  AND pa.policy_area = ?
                  AND v.is_procedural = 0
                GROUP BY v.bill_id
                HAVING sessions > 1
                """,
                (voter_name, policy_area),
            ).fetchall()

            loyalty_row = db.execute(
                """
                WITH my_bills AS (
                    SELECT DISTINCT v.bill_id, v.session
                    FROM va_legislator_recent_votes v
                    JOIN va_bill_policy_areas pa
                      ON pa.bill_number = v.bill_id AND pa.session = v.session
                    WHERE v.voter_name  = ?
                      AND pa.policy_area = ?
                      AND v.is_procedural = 0
                ),
                party_majority AS (
                    SELECT v.bill_id, v.session,
                        CASE
                          WHEN SUM(CASE WHEN v.option='yes' THEN 1 ELSE 0 END) >=
                               SUM(CASE WHEN v.option='no'  THEN 1 ELSE 0 END)
                          THEN 'yes' ELSE 'no'
                        END AS majority
                    FROM va_legislator_recent_votes v
                    JOIN va_name_index ni ON ni.votes_table_name = v.voter_name
                    JOIN my_bills mb ON mb.bill_id = v.bill_id AND mb.session = v.session
                    WHERE ni.party = ?
                      AND v.is_procedural = 0
                    GROUP BY v.bill_id, v.session
                ),
                my_votes AS (
                    SELECT v.bill_id, v.session, v.option
                    FROM va_legislator_recent_votes v
                    JOIN va_bill_policy_areas pa
                      ON pa.bill_number = v.bill_id AND pa.session = v.session
                    WHERE v.voter_name  = ?
                      AND pa.policy_area = ?
                      AND v.is_procedural = 0
                )
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN mv.option = pm.majority THEN 1 ELSE 0 END) AS loyal
                FROM my_votes mv
                JOIN party_majority pm
                  ON pm.bill_id = mv.bill_id AND pm.session = mv.session
                """,
                (voter_name, policy_area, party, voter_name, policy_area),
            ).fetchone()

        else:  # Path B — title keyword
            pat = f"%{kw}%"

            vote_counts = db.execute(
                """
                SELECT v.option, COUNT(*) AS n
                FROM va_legislator_recent_votes v
                JOIN va_bills b ON b.bill_number = v.bill_id AND b.session = v.session
                WHERE v.voter_name = ?
                  AND lower(b.title) LIKE ?
                  AND v.is_procedural = 0
                GROUP BY v.option
                """,
                (voter_name, pat),
            ).fetchall()

            flip_rows = db.execute(
                """
                SELECT v.bill_id,
                       COUNT(DISTINCT v.session) AS sessions,
                       COUNT(DISTINCT v.option)  AS distinct_opts
                FROM va_legislator_recent_votes v
                JOIN va_bills b ON b.bill_number = v.bill_id AND b.session = v.session
                WHERE v.voter_name = ?
                  AND lower(b.title) LIKE ?
                  AND v.is_procedural = 0
                GROUP BY v.bill_id
                HAVING sessions > 1
                """,
                (voter_name, pat),
            ).fetchall()

            loyalty_row = db.execute(
                """
                WITH my_bills AS (
                    SELECT DISTINCT v.bill_id, v.session
                    FROM va_legislator_recent_votes v
                    JOIN va_bills b ON b.bill_number = v.bill_id AND b.session = v.session
                    WHERE v.voter_name = ?
                      AND lower(b.title) LIKE ?
                      AND v.is_procedural = 0
                ),
                party_majority AS (
                    SELECT v.bill_id, v.session,
                        CASE
                          WHEN SUM(CASE WHEN v.option='yes' THEN 1 ELSE 0 END) >=
                               SUM(CASE WHEN v.option='no'  THEN 1 ELSE 0 END)
                          THEN 'yes' ELSE 'no'
                        END AS majority
                    FROM va_legislator_recent_votes v
                    JOIN va_name_index ni ON ni.votes_table_name = v.voter_name
                    JOIN my_bills mb ON mb.bill_id = v.bill_id AND mb.session = v.session
                    WHERE ni.party = ?
                      AND v.is_procedural = 0
                    GROUP BY v.bill_id, v.session
                ),
                my_votes AS (
                    SELECT v.bill_id, v.session, v.option
                    FROM va_legislator_recent_votes v
                    JOIN va_bills b ON b.bill_number = v.bill_id AND b.session = v.session
                    WHERE v.voter_name = ?
                      AND lower(b.title) LIKE ?
                      AND v.is_procedural = 0
                )
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN mv.option = pm.majority THEN 1 ELSE 0 END) AS loyal
                FROM my_votes mv
                JOIN party_majority pm
                  ON pm.bill_id = mv.bill_id AND pm.session = mv.session
                """,
                (voter_name, pat, party, voter_name, pat),
            ).fetchone()

    except Exception as exc:
        logger.error("get_vote_consistency error for %r: %s", canonical_name, exc)
        return _empty_consistency(source)

    counts = {r[0]: r[1] for r in vote_counts}
    yes_n  = counts.get("yes", 0)
    total  = sum(counts.values())
    yes_rate = (yes_n / total) if total > 0 else None

    if loyalty_row and loyalty_row[0] and loyalty_row[0] > 0:
        party_loyalty_rate: float | None = loyalty_row[1] / loyalty_row[0]
    else:
        party_loyalty_rate = None

    multi   = len(flip_rows)
    flipped = sum(1 for r in flip_rows if r[2] > 1)
    flip_rate = (flipped / multi) if multi > 0 else 0.0

    logger.info(
        "get_vote_consistency: %r policy=%r source=%r total=%d yes_rate=%s",
        canonical_name, policy_area, source, total,
        f"{yes_rate:.2%}" if yes_rate is not None else "None",
    )
    return {
        "total_votes":        total,
        "yes_rate":           yes_rate,
        "party_loyalty_rate": party_loyalty_rate,
        "flip_rate":          flip_rate,
        "sufficient_data":    total >= MIN_VOTE_COUNT,
        "classification_source": source,
    }


# ── 2b. Donor concentration ──────────────────────────────────────────────────

def get_donor_concentration(
    db: sqlite3.Connection, canonical_name: str, policy_area: str
) -> list[dict]:
    """
    Returns top 5 donor employers for this legislator.

    Note: va_cf_schedule_a has no column that links contributions to policy areas.
    classification_source is always SOURCE_NONE. The policy_area argument is passed
    through to the result for LLM context but does not filter the query.

    Each dict: {donor_industry, total_amount, pct_of_total, classification_source}
    Returns empty list (never None) if no data.
    """
    name_row = _resolve_name(db, canonical_name)
    if not name_row or not name_row["cf_table_name"]:
        logger.warning("get_donor_concentration: no cf_table_name for %r", canonical_name)
        return []

    cf_name = name_row["cf_table_name"]

    try:
        rows = db.execute(
            """
            SELECT
                COALESCE(NULLIF(trim(employer), ''), NULLIF(trim(occupation), ''), 'Unknown')
                    AS industry,
                SUM(amount)  AS total_amount,
                COUNT(*)     AS txn_count
            FROM va_cf_schedule_a
            WHERE candidate_name = ?
              AND amount > 0
            GROUP BY industry
            ORDER BY total_amount DESC
            LIMIT 5
            """,
            (cf_name,),
        ).fetchall()
    except Exception as exc:
        logger.error("get_donor_concentration error for %r: %s", canonical_name, exc)
        return []

    if not rows:
        return []

    grand_total = sum(r[1] for r in rows)
    result = [
        {
            "donor_industry":  row[0],
            "total_amount":    round(row[1], 2),
            "pct_of_total":    round(row[1] / grand_total, 4) if grand_total else 0.0,
            "classification_source": SOURCE_NONE,
        }
        for row in rows
    ]
    logger.info(
        "get_donor_concentration: %r cf_name=%r — %d industries",
        canonical_name, cf_name, len(result),
    )
    return result


# ── 2c. Donor timing signal ──────────────────────────────────────────────────

def get_donor_timing(
    db: sqlite3.Connection, canonical_name: str, policy_area: str
) -> dict | None:
    """
    Whether contributions arrive before votes on policy-area bills.

    Joins va_cf_schedule_a × va_legislator_recent_votes, filtering to contribution
    dates within ±365 days of a vote. Positive avg_lead_days = money arrives before vote.

    Returns:
      avg_lead_days       float | None
      contribution_count  int
      money_precedes_votes bool
      sufficient_data     bool  (False when contribution_count < 20)
      classification_source str
    """
    name_row = _resolve_name(db, canonical_name)
    if not name_row:
        return None

    voter_name = name_row["votes_table_name"]
    cf_name    = name_row["cf_table_name"]
    source, kw = _policy_filter_path(db, policy_area)

    if not voter_name or not cf_name:
        missing = "votes_table_name" if not voter_name else "cf_table_name"
        logger.warning("get_donor_timing: missing %s for %r", missing, canonical_name)
        return _empty_timing(source)

    try:
        if source == SOURCE_CLASSIFIED:
            row = db.execute(
                """
                WITH legislator_cf AS (
                    SELECT transaction_date
                    FROM va_cf_schedule_a
                    WHERE candidate_name = ?
                      AND transaction_date IS NOT NULL
                ),
                legislator_votes AS (
                    SELECT v.vote_date
                    FROM va_legislator_recent_votes v
                    JOIN va_bill_policy_areas pa
                      ON pa.bill_number = v.bill_id AND pa.session = v.session
                    WHERE v.voter_name   = ?
                      AND pa.policy_area = ?
                      AND v.is_procedural = 0
                      AND v.vote_date IS NOT NULL
                )
                SELECT
                    AVG(julianday(lv.vote_date) - julianday(lc.transaction_date)) AS avg_lead,
                    COUNT(*) AS n
                FROM legislator_cf lc, legislator_votes lv
                WHERE ABS(julianday(lv.vote_date) - julianday(lc.transaction_date)) <= 365
                """,
                (cf_name, voter_name, policy_area),
            ).fetchone()
        else:
            pat = f"%{kw}%"
            row = db.execute(
                """
                WITH legislator_cf AS (
                    SELECT transaction_date
                    FROM va_cf_schedule_a
                    WHERE candidate_name = ?
                      AND transaction_date IS NOT NULL
                ),
                legislator_votes AS (
                    SELECT v.vote_date
                    FROM va_legislator_recent_votes v
                    JOIN va_bills b ON b.bill_number = v.bill_id AND b.session = v.session
                    WHERE v.voter_name  = ?
                      AND lower(b.title) LIKE ?
                      AND v.is_procedural = 0
                      AND v.vote_date IS NOT NULL
                )
                SELECT
                    AVG(julianday(lv.vote_date) - julianday(lc.transaction_date)) AS avg_lead,
                    COUNT(*) AS n
                FROM legislator_cf lc, legislator_votes lv
                WHERE ABS(julianday(lv.vote_date) - julianday(lc.transaction_date)) <= 365
                """,
                (cf_name, voter_name, pat),
            ).fetchone()

    except Exception as exc:
        logger.error("get_donor_timing error for %r: %s", canonical_name, exc)
        return _empty_timing(source)

    if not row or not row[1]:
        return _empty_timing(source)

    avg_lead: float | None = row[0]
    count: int = row[1]

    logger.info(
        "get_donor_timing: %r policy=%r source=%r avg_lead=%s n=%d",
        canonical_name, policy_area, source,
        f"{avg_lead:.1f}" if avg_lead is not None else "None", count,
    )
    return {
        "avg_lead_days":        round(avg_lead, 1) if avg_lead is not None else None,
        "contribution_count":   count,
        "money_precedes_votes": avg_lead is not None and avg_lead > 0,
        "sufficient_data":      count >= MIN_CONTRIBUTION_COUNT,
        "classification_source": source,
    }


# ── 2d. Historical policy-area outcomes ─────────────────────────────────────

def get_policy_area_outcomes(
    db: sqlite3.Connection, policy_area: str, sessions: int = 5
) -> dict:
    """
    Base rates for bills in this policy area over the last N sessions.

    Returns:
      pass_rate       float | None
      avg_yes_votes   float | None  (avg yes-vote count per bill floor vote)
      partisan_split  float | None  (0=bipartisan ↔ 1=party-line)
      total_bills     int
      classification_source str
    """
    source, kw = _policy_filter_path(db, policy_area)

    try:
        session_rows = db.execute(
            "SELECT DISTINCT session FROM va_bills ORDER BY session DESC LIMIT ?",
            (sessions,),
        ).fetchall()
        recent_sessions = [r[0] for r in session_rows]
    except Exception as exc:
        logger.error("get_policy_area_outcomes: session fetch failed: %s", exc)
        return _empty_outcomes(source)

    if not recent_sessions:
        return _empty_outcomes(source)

    sess_ph = ",".join("?" * len(recent_sessions))

    try:
        if source == SOURCE_CLASSIFIED:
            bill_rows = db.execute(
                f"""
                SELECT b.bill_number, b.session, b.status
                FROM va_bills b
                JOIN va_bill_policy_areas pa
                  ON pa.bill_number = b.bill_number AND pa.session = b.session
                WHERE pa.policy_area = ?
                  AND b.session IN ({sess_ph})
                """,
                [policy_area, *recent_sessions],
            ).fetchall()

            avg_yes_row = db.execute(
                f"""
                SELECT AVG(yes_cnt) FROM (
                    SELECT v.bill_id, v.session, COUNT(*) AS yes_cnt
                    FROM va_legislator_recent_votes v
                    JOIN va_bill_policy_areas pa
                      ON pa.bill_number = v.bill_id AND pa.session = v.session
                    WHERE pa.policy_area = ?
                      AND v.session IN ({sess_ph})
                      AND v.option = 'yes'
                      AND v.is_procedural = 0
                    GROUP BY v.bill_id, v.session
                )
                """,
                [policy_area, *recent_sessions],
            ).fetchone()

            party_rows = db.execute(
                f"""
                SELECT v.bill_id, v.session, ni.party,
                       AVG(CASE WHEN v.option='yes' THEN 1.0 ELSE 0.0 END) AS yes_rate
                FROM va_legislator_recent_votes v
                JOIN va_name_index ni ON ni.votes_table_name = v.voter_name
                JOIN va_bill_policy_areas pa
                  ON pa.bill_number = v.bill_id AND pa.session = v.session
                WHERE pa.policy_area = ?
                  AND v.session IN ({sess_ph})
                  AND v.is_procedural = 0
                  AND ni.party IN ('Democratic', 'Republican')
                GROUP BY v.bill_id, v.session, ni.party
                """,
                [policy_area, *recent_sessions],
            ).fetchall()

        else:  # Path B
            pat = f"%{kw}%"

            bill_rows = db.execute(
                f"""
                SELECT b.bill_number, b.session, b.status
                FROM va_bills b
                WHERE lower(b.title) LIKE ?
                  AND b.session IN ({sess_ph})
                """,
                [pat, *recent_sessions],
            ).fetchall()

            avg_yes_row = db.execute(
                f"""
                SELECT AVG(yes_cnt) FROM (
                    SELECT v.bill_id, v.session, COUNT(*) AS yes_cnt
                    FROM va_legislator_recent_votes v
                    JOIN va_bills b ON b.bill_number = v.bill_id AND b.session = v.session
                    WHERE lower(b.title) LIKE ?
                      AND v.session IN ({sess_ph})
                      AND v.option = 'yes'
                      AND v.is_procedural = 0
                    GROUP BY v.bill_id, v.session
                )
                """,
                [pat, *recent_sessions],
            ).fetchone()

            party_rows = db.execute(
                f"""
                SELECT v.bill_id, v.session, ni.party,
                       AVG(CASE WHEN v.option='yes' THEN 1.0 ELSE 0.0 END) AS yes_rate
                FROM va_legislator_recent_votes v
                JOIN va_name_index ni ON ni.votes_table_name = v.voter_name
                JOIN va_bills b ON b.bill_number = v.bill_id AND b.session = v.session
                WHERE lower(b.title) LIKE ?
                  AND v.session IN ({sess_ph})
                  AND v.is_procedural = 0
                  AND ni.party IN ('Democratic', 'Republican')
                GROUP BY v.bill_id, v.session, ni.party
                """,
                [pat, *recent_sessions],
            ).fetchall()

    except Exception as exc:
        logger.error("get_policy_area_outcomes error: %s", exc)
        return _empty_outcomes(source)

    total_bills = len(bill_rows)
    if total_bills == 0:
        return _empty_outcomes(source)

    passed = sum(
        1 for r in bill_rows
        if any(tok in (r[2] or "").lower() for tok in _PASS_TOKENS)
    )
    pass_rate: float | None = passed / total_bills

    avg_yes: float | None = avg_yes_row[0] if avg_yes_row and avg_yes_row[0] else None

    # Partisan split: avg |dem_yes_rate − rep_yes_rate| across bills with both parties
    bill_party: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in party_rows:
        bill_party[(r[0], r[1])][r[2]] = r[3]

    splits = [
        abs(v.get("Democratic", 0.5) - v.get("Republican", 0.5))
        for v in bill_party.values()
        if "Democratic" in v and "Republican" in v
    ]
    partisan_split: float | None = (sum(splits) / len(splits)) if splits else None

    logger.info(
        "get_policy_area_outcomes: policy=%r source=%r bills=%d pass_rate=%s",
        policy_area, source, total_bills,
        f"{pass_rate:.2%}" if pass_rate is not None else "None",
    )
    return {
        "pass_rate":       round(pass_rate, 4)      if pass_rate      is not None else None,
        "avg_yes_votes":   round(avg_yes, 1)         if avg_yes        is not None else None,
        "partisan_split":  round(partisan_split, 4)  if partisan_split is not None else None,
        "total_bills":     total_bills,
        "classification_source": source,
    }


# ── 2e. Conflict-of-interest score ──────────────────────────────────────────

# coi_score formula: 50 + alignment_delta × (top_sector_share/100) × 2, clamped 0–100
# >50 = money and votes aligned; <50 = counter-signal (votes against donor sector)
_COI_COUNTER_DELTA_THRESHOLD  = -5.0   # alignment_delta below this triggers counter-signal
_COI_COUNTER_SHARE_THRESHOLD  = 15.0   # top_sector_share must also exceed this

def get_coi_score(
    db: sqlite3.Connection,
    canonical_name: str,
    policy_area: str | None = None,
) -> dict | None:
    """
    Look up pre-computed CoI (conflict-of-interest) score from donor_vote_alignment.

    Source: donor_vote_alignment table (150 rows, pre-computed by build_dominion_analysis.py).
    Joined via va_name_index.votes_table_name = donor_vote_alignment.name.

    If policy_area is provided and matches a sector in by_sector_json (>=5 votes),
    sector_matched=True and sector_specific_yes_rate gives a targeted yes-rate for
    that policy area — more precise than the aggregate top-sector signal.

    Returns:
      coi_score                float 0–100  (50=neutral; >50=aligned; <50=counter-signal)
      top_donor_industry       str | None
      top_donor_amount         float | None
      yes_rate_in_top_industry float | None  (sector_yes_rate as 0–1)
      is_counter_signal        bool
      party_median_gap         float | None  (sector_yes_rate minus party avg)
      counter_signal_note      str | None    (populated only when is_counter_signal=True)
      sector_matched           bool          (True when policy_area hit by_sector_json)
      sector_specific_yes_rate float | None  (0–1; None when sector_matched=False)
      sector_specific_total    int | None    (vote count for that sector)
    Returns None if legislator is not in donor_vote_alignment.
    """
    name_row = _resolve_name(db, canonical_name)
    if not name_row:
        return None

    dv_name = name_row.get("dv_table_name")
    party   = name_row["party"]

    if not dv_name:
        logger.info("get_coi_score: no dv_table_name for %r — rebuild name index", canonical_name)
        return None

    try:
        dv_row = db.execute(
            """
            SELECT top_donor_sector, top_sector_amt, top_sector_share,
                   sector_yes_rate, other_yes_rate, alignment_delta, total_raised,
                   by_sector_json
            FROM donor_vote_alignment
            WHERE name = ?
            """,
            (dv_name,),
        ).fetchone()
    except Exception as exc:
        logger.error("get_coi_score: DB error for %r: %s", canonical_name, exc)
        return None

    if not dv_row:
        logger.info("get_coi_score: %r (dv_name=%r) not in donor_vote_alignment", canonical_name, dv_name)
        return None

    top_sector, top_amt, share, s_yes, o_yes, delta, total_raised, by_sector_raw = dv_row

    # Normalise None fields that can appear when a legislator has no sector votes
    delta = delta or 0.0
    share = share or 0.0
    s_yes = s_yes or 0.0

    raw_score = 50.0 + delta * (share / 100.0) * 2.0
    coi_score = round(min(100.0, max(0.0, raw_score)), 1)

    is_counter = (
        delta < _COI_COUNTER_DELTA_THRESHOLD
        and share >= _COI_COUNTER_SHARE_THRESHOLD
    )

    # Party peer median for same sector
    party_median_gap: float | None = None
    if party and top_sector:
        try:
            med_row = db.execute(
                """
                SELECT AVG(dv2.sector_yes_rate)
                FROM donor_vote_alignment dv2
                JOIN va_name_index ni ON ni.dv_table_name = dv2.name
                WHERE ni.party = ?
                  AND dv2.top_donor_sector = ?
                  AND dv2.sector_yes_rate IS NOT NULL
                """,
                (party, top_sector),
            ).fetchone()
            if med_row and med_row[0] is not None:
                party_median_gap = round(s_yes - med_row[0], 2)
        except Exception as exc:
            logger.warning("get_coi_score: party median query failed: %s", exc)

    counter_note: str | None = None
    if is_counter:
        counter_note = (
            f"CONFLICTING SIGNAL DETECTED — {canonical_name} receives "
            f"{share:.1f}% of fundraising from {top_sector or 'top sector'} "
            f"but votes {abs(delta):.1f}pp below the sector average."
        )

    # Sector-specific yes rate when policy_area matches a by_sector_json entry
    sector_matched           = False
    sector_specific_yes_rate: float | None = None
    sector_specific_total:   int | None   = None
    if policy_area and by_sector_raw:
        try:
            for entry in json.loads(by_sector_raw):
                if entry.get("sector") == policy_area and entry.get("total", 0) >= 5:
                    sector_specific_yes_rate = round(entry["yes_rate"] / 100.0, 4)
                    sector_specific_total    = entry["total"]
                    sector_matched           = True
                    break
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("get_coi_score: by_sector_json parse error for %r: %s", canonical_name, exc)

    logger.info(
        "get_coi_score: %r sector=%r delta=%+.1f share=%.1f%% score=%.1f counter=%s sector_match=%s",
        canonical_name, top_sector, delta, share, coi_score, is_counter, sector_matched,
    )
    return {
        "coi_score":               coi_score,
        "top_donor_industry":      top_sector,
        "top_donor_amount":        round(float(top_amt), 2) if top_amt is not None else None,
        "yes_rate_in_top_industry": round(s_yes / 100.0, 4),
        "is_counter_signal":       is_counter,
        "party_median_gap":        party_median_gap,
        "counter_signal_note":     counter_note,
        "sector_matched":          sector_matched,
        "sector_specific_yes_rate": sector_specific_yes_rate,
        "sector_specific_total":   sector_specific_total,
    }


# ── 2f. Utility / Dominion funding gap ──────────────────────────────────────

_BASE_DIR          = Path(__file__).resolve().parent.parent.parent
_DOMINION_CACHE    = _BASE_DIR / "data" / "donor_map_cache.json"
_dominion_per_bill: list[dict] | None = None  # module-level lazy cache


def _load_dominion_per_bill() -> list[dict]:
    global _dominion_per_bill
    if _dominion_per_bill is not None:
        return _dominion_per_bill
    if not _DOMINION_CACHE.exists():
        logger.warning("_load_dominion_per_bill: cache not found at %s", _DOMINION_CACHE)
        _dominion_per_bill = []
        return _dominion_per_bill
    try:
        with _DOMINION_CACHE.open(encoding="utf-8") as f:
            raw = json.load(f)
        _dominion_per_bill = raw.get("state", {}).get("dominion_analysis", {}).get("per_bill", [])
    except Exception as exc:
        logger.error("_load_dominion_per_bill: failed to load cache: %s", exc)
        _dominion_per_bill = []
    return _dominion_per_bill


def get_utility_funding_gap(
    db: sqlite3.Connection, canonical_name: str, bill_number: str, session: str
) -> dict | None:
    """
    For energy/utility bills tracked in the Dominion analysis: return the funding gap ratio.

    ratio = avg Dominion funding for YES voters / avg Dominion funding for NO voters.
    A ratio of 19.8 means YES voters received ~20x more Dominion money than NO voters.

    Source: data/donor_map_cache.json → state.dominion_analysis.per_bill (10 tracked bills).
    The per_bill list is keyed by bill_id only (no session), so session is accepted for
    API consistency but not used in the lookup.

    Returns None if the bill is not in the tracked set.
    Returns dict:
      bill_id        str
      description    str
      ratio          float    (YES-voter avg / NO-voter avg Dominion funding)
      avg_yes_dom    float    (avg Dominion $ for YES voters)
      avg_no_dom     float    (avg Dominion $ for NO voters)
    """
    per_bill = _load_dominion_per_bill()
    if not per_bill:
        return None

    needle = bill_number.strip().upper()
    for entry in per_bill:
        if entry.get("bill_id", "").strip().upper() == needle:
            logger.info(
                "get_utility_funding_gap: %r matched bill=%r ratio=%.1f",
                canonical_name, entry["bill_id"], entry.get("ratio", 0),
            )
            return {
                "bill_id":     entry["bill_id"],
                "description": entry.get("description", ""),
                "ratio":       entry.get("ratio"),
                "avg_yes_dom": entry.get("avg_yes_dom"),
                "avg_no_dom":  entry.get("avg_no_dom"),
            }

    logger.debug(
        "get_utility_funding_gap: bill=%r not in Dominion tracked set", bill_number
    )
    return None
