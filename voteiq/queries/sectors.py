import json
import os
import sqlite3
from pathlib import Path

from voteiq.db import federal, virginia
from voteiq.analysis.governor_action_money import (
    get_governor_action_analysis_guardrails,
    get_governor_action_money_patterns,
    get_governor_action_sector_totals,
    get_governor_action_top_sponsors,
    get_governor_action_voteiq_finding,
)

_BASE_DIR = Path(__file__).resolve().parents[2]
_data_dir = os.getenv("DATA_DIR", str(_BASE_DIR))
_POLLS_DB = os.path.join(_data_dir if os.path.isdir(_data_dir) else str(_BASE_DIR), "polls.db")


def get_finance_summary_from_cache(name: str, source: str | None = None) -> dict | None:
    """Exact-name row from the campaign_finance_summary cache table.

    Exact (case-insensitive) match only — fuzzy surname matching caused
    cross-legislator attribution elsewhere in the codebase, so a miss
    returns None and the caller simply omits the finance block.
    """
    if not name:
        return None
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row
        sql = ("SELECT name, source, chamber, district, party, latest_cycle, "
               "total_raised, top_sector, top_sector_pct, by_sector_json, "
               "top_donors_json, overall_va_pct "
               "FROM campaign_finance_summary WHERE lower(name) = lower(?)")
        params: list = [name]
        if source:
            sql += " AND source = ?"
            params.append(source)
        row = conn.execute(sql + " LIMIT 1", params).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def format_finance_summary_for_chat(cf: dict, source_tables: list | None = None) -> str:
    """Render a cached finance summary as a descriptive chat-context block."""
    src_label = "FEC" if cf.get("source") == "fec" else "Virginia SBE"
    lines = [f"[Campaign Finance Summary — {cf.get('name')} ({src_label})]"]
    total = cf.get("total_raised")
    if total:
        cycle = cf.get("latest_cycle") or ""
        cyc = f" (through {cycle})" if cycle else ""
        lines.append(f"Total raised: ${float(total):,.0f}{cyc}")
    if cf.get("top_sector"):
        pct = cf.get("top_sector_pct")
        pct_str = f" ({pct}% of total)" if pct else ""
        lines.append(f"Top donor sector: {cf['top_sector']}{pct_str}")
    if cf.get("overall_va_pct") is not None:
        lines.append(f"Share from Virginia donors: {cf['overall_va_pct']}%")
    try:
        sectors = json.loads(cf.get("by_sector_json") or "[]")[:6]
        if sectors:
            lines.append("By sector:")
            for s in sectors:
                lines.append(f"  {s.get('sector')}: ${float(s.get('total') or 0):,.0f}")
    except Exception:
        pass
    try:
        donors = json.loads(cf.get("top_donors_json") or "[]")[:5]
        if donors:
            lines.append("Top donors:")
            for d in donors:
                nm = d.get("contributor_name") or d.get("name") or ""
                amt = d.get("total") or d.get("amount") or 0
                lines.append(f"  {nm}: ${float(amt):,.0f}")
    except Exception:
        pass
    if source_tables:
        lines.append(f"Source tables: {', '.join(source_tables)}")
    return "\n".join(lines)


def get_federal_sector_totals(candidate_id: str, cycle: int = 2024) -> list:
    return federal.query("""
        SELECT sector,
               total_amount,
               donor_count,
               ROUND(total_amount / MAX(donor_count, 1), 2) AS avg_donation
        FROM candidate_sector_totals
        WHERE candidate_id = ?
          AND cycle = ?
        ORDER BY total_amount DESC
    """, (candidate_id, cycle))


def get_sector_totals(candidate_id: str, cycle: int = 2024) -> list:
    """Compatibility alias for federal candidate sector totals."""
    return get_federal_sector_totals(candidate_id, cycle)


def get_shared_donors_named(
    candidate_ids: list[str],
    cycle: int = 2024,
    min_candidates: int = 2,
) -> list:
    """
    Return donors shared across multiple federal FEC candidate IDs.

    This is candidate-ID based, so it works for presidential candidates that
    do not have a bioguide bridge row.
    """
    if not candidate_ids or len(candidate_ids) < 2:
        return []

    candidate_ids = list(dict.fromkeys(candidate_ids))
    min_candidates = max(2, min(int(min_candidates), len(candidate_ids)))
    placeholders = ",".join("?" for _ in candidate_ids)

    return federal.query(f"""
        SELECT
            UPPER(TRIM(contributor_name)) AS donor_key,
            MIN(contributor_name) AS contributor_name,
            UPPER(TRIM(COALESCE(contributor_employer, ''))) AS employer_key,
            MIN(contributor_employer) AS contributor_employer,
            COALESCE(employer_sector, 'Unclassified') AS employer_sector,
            ROUND(SUM(amount), 2) AS total_given,
            COUNT(DISTINCT candidate_id) AS candidates_funded,
            GROUP_CONCAT(DISTINCT candidate_id) AS candidates,
            ROUND(AVG(amount), 2) AS avg_donation,
            COUNT(*) AS total_transactions,
            MIN(contribution_date) AS first_contribution,
            MAX(contribution_date) AS last_contribution,
            CASE
                WHEN COUNT(DISTINCT candidate_id) = ?
                    THEN 'Network donor across all'
                WHEN COUNT(DISTINCT candidate_id) >= 3
                    THEN 'High-overlap donor'
                WHEN COUNT(DISTINCT candidate_id) = 2
                    THEN 'Shared donor'
            END AS overlap_type
        FROM fec_individual_contributions
        WHERE candidate_id IN ({placeholders})
          AND cycle = ?
          AND contributor_name IS NOT NULL
          AND TRIM(contributor_name) != ''
        GROUP BY
            donor_key,
            employer_key,
            COALESCE(employer_sector, 'Unclassified')
        HAVING COUNT(DISTINCT candidate_id) >= ?
        ORDER BY candidates_funded DESC, total_given DESC
    """, (len(candidate_ids), *candidate_ids, cycle, min_candidates))


def get_state_sector_totals(lis_id: str, cycle: str = "2023") -> list:
    try:
        return virginia.query("""
            SELECT sector,
                   SUM(amount)         AS total,
                   COUNT(*)            AS donors,
                   AVG(amount)         AS avg_donation
            FROM va_sbe_contributions
            WHERE legislator_id = ?
              AND cycle = ?
            GROUP BY sector
            ORDER BY total DESC
        """, (lis_id, cycle))
    except sqlite3.OperationalError:
        return []


def get_federal_industry_network(sector: str, cycle: int = 2024) -> list:
    return federal.query("""
        SELECT m.name,
               m.party,
               m.state,
               m.chamber,
               f.total_amount,
               f.donor_count,
               ROUND(f.total_amount / MAX(f.donor_count, 1), 2) AS avg_donation
        FROM candidate_sector_totals f
        JOIN bioguide_fec_bridge bridge ON bridge.fec_candidate_id = f.candidate_id
        JOIN congress_members m ON m.bioguide_id = bridge.bioguide_id
        WHERE f.sector = ?
          AND f.cycle = ?
        ORDER BY f.total_amount DESC
        LIMIT 20
    """, (sector, cycle))


def get_state_industry_network(sector: str, cycle: str = "2023") -> list:
    try:
        return virginia.query("""
            SELECT l.name,
                   l.party,
                   l.district,
                   l.chamber,
                   SUM(c.amount)  AS total,
                   COUNT(*)       AS donors,
                   AVG(c.amount)  AS avg_donation
            FROM va_sbe_contributions c
            JOIN legislators l ON c.legislator_id = l.lis_id
            WHERE c.sector = ?
              AND c.cycle = ?
            GROUP BY l.name, l.party, l.district, l.chamber
            ORDER BY total DESC
            LIMIT 20
        """, (sector, cycle))
    except sqlite3.OperationalError:
        return []
