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
    """Return a campaign_finance_summary row for the given name.

    Tries in order:
    1. Exact case-insensitive match.
    2. First-token + last-token LIKE match (handles middle initials, e.g.
       "Aaron Rouse" → "Aaron R. Rouse").  Only used when this resolves
       to a single row, to avoid cross-legislator mis-attribution.
    """
    if not name:
        return None
    _SELECT = (
        "SELECT name, source, chamber, district, party, latest_cycle, "
        "total_raised, top_sector, top_sector_pct, by_sector_json, "
        "top_donors_json, overall_va_pct "
        "FROM campaign_finance_summary"
    )
    try:
        conn = sqlite3.connect(_POLLS_DB)
        conn.row_factory = sqlite3.Row

        # 1. Exact match
        src_clause = " AND source = ?" if source else ""
        params: list = [name] + ([source] if source else [])
        row = conn.execute(
            f"{_SELECT} WHERE lower(name) = lower(?){src_clause} LIMIT 1",
            params,
        ).fetchone()
        if row:
            conn.close()
            return dict(row)

        # 2. First + last token fallback (handles middle initials)
        tokens = name.strip().split()
        if len(tokens) >= 2:
            first, last = tokens[0].lower(), tokens[-1].lower()
            src_clause2 = " AND source = ?" if source else ""
            params2: list = [f"%{first}%", f"%{last}%"] + ([source] if source else [])
            rows = conn.execute(
                f"{_SELECT} WHERE lower(name) LIKE ? AND lower(name) LIKE ?{src_clause2}",
                params2,
            ).fetchall()
            if len(rows) == 1:
                conn.close()
                return dict(rows[0])

        conn.close()
        return None
    except Exception:
        return None


# FEC individual-contribution occupation labels that are NOT industry sectors.
# When these appear as top_sector, the model should not treat them as industries.
_FEC_OCCUPATION_LABELS = {
    "retired", "self-employed", "self employed", "other", "homemaker",
    "not employed", "unemployed", "student", "information requested",
    "none", "n/a", "na",
}


def _top_industry_sector(top_sector: str | None, by_sector_json: str | None) -> tuple[str | None, bool]:
    """Return (sector_label, is_occupation_label).

    If top_sector is a FEC occupation label (not an industry), scan by_sector_json
    for the highest-dollar real industry sector and return that instead, with
    is_occupation_label=True so callers can add a clarifying note.
    """
    if not top_sector:
        return None, False
    if top_sector.lower().strip() not in _FEC_OCCUPATION_LABELS:
        return top_sector, False
    # top_sector is an occupation label — find the first real industry sector
    try:
        sectors = json.loads(by_sector_json or "[]")
        for s in sectors:
            label = (s.get("sector") or "").strip()
            if label and label.lower() not in _FEC_OCCUPATION_LABELS:
                return label, True
    except Exception:
        pass
    return top_sector, True  # fallback: return original with flag


def _get_donor_type_breakdown(candidate_name: str) -> dict | None:
    """Query va_cf_schedule_a for individual vs org/PAC + small/large donor stats."""
    if not candidate_name:
        return None
    parts = candidate_name.lower().split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    try:
        conn = sqlite3.connect(_POLLS_DB, timeout=15)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                is_individual,
                COUNT(*)                                                   AS txns,
                ROUND(SUM(amount), 0)                                      AS total,
                ROUND(AVG(amount), 0)                                      AS avg_amt,
                SUM(CASE WHEN amount <  250 THEN 1 ELSE 0 END)            AS small_n,
                ROUND(SUM(CASE WHEN amount <  250 THEN amount ELSE 0 END), 0) AS small_total,
                SUM(CASE WHEN amount >= 10000 THEN 1 ELSE 0 END)           AS large_n,
                ROUND(SUM(CASE WHEN amount >= 10000 THEN amount ELSE 0 END), 0) AS large_total
            FROM va_cf_schedule_a
            WHERE lower(candidate_name) LIKE ? AND lower(candidate_name) LIKE ?
              AND amount > 0
            GROUP BY is_individual
            """,
            (f"%{first}%", f"%{last}%"),
        ).fetchall()
        conn.close()
        if not rows:
            return None
        result: dict = {"individual": {}, "org_pac": {}}
        grand_total = 0.0
        for r in rows:
            d = dict(r)
            grand_total += d["total"] or 0
            key = "individual" if r["is_individual"] else "org_pac"
            result[key] = d
        result["grand_total"] = grand_total
        return result
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
        industry_sector, is_occ = _top_industry_sector(
            cf["top_sector"], cf.get("by_sector_json")
        )
        pct = cf.get("top_sector_pct")
        pct_str = f" ({pct}% of total)" if pct else ""
        if is_occ:
            if industry_sector and industry_sector != cf["top_sector"]:
                lines.append(f"Top industry donor sector: {industry_sector}")
        else:
            lines.append(f"Top donor sector: {industry_sector}{pct_str}")
    if cf.get("overall_va_pct") is not None:
        lines.append(f"Share from Virginia donors: {cf['overall_va_pct']}%")
    try:
        sectors = [
            s for s in json.loads(cf.get("by_sector_json") or "[]")
            if (s.get("sector") or "").lower().strip() not in _FEC_OCCUPATION_LABELS
        ][:6]
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
    # Donor type breakdown — VA SBE only (individual vs org/PAC, small vs large)
    if cf.get("source") == "va_sbe":
        breakdown = _get_donor_type_breakdown(cf.get("name", ""))
        if breakdown:
            gt = breakdown["grand_total"] or 1
            ind = breakdown.get("individual", {})
            org = breakdown.get("org_pac", {})
            ind_total = ind.get("total") or 0
            org_total = org.get("total") or 0
            lines.append("Donor type breakdown:")
            if ind_total:
                lines.append(
                    f"  Individual donors: {ind_total/gt*100:.0f}% of total "
                    f"(${ind_total:,.0f} across {ind.get('txns',0):,} donations, "
                    f"avg ${ind.get('avg_amt',0):,.0f})"
                )
                small_n   = ind.get("small_n", 0)
                small_tot = ind.get("small_total", 0)
                large_n   = ind.get("large_n", 0)
                large_tot = ind.get("large_total", 0)
                if small_tot:
                    lines.append(
                        f"    Small donors (<$250): {small_tot/gt*100:.1f}% of total "
                        f"(${small_tot:,.0f}, {small_n:,} donations)"
                    )
                if large_tot:
                    lines.append(
                        f"    Large individual donors ($10k+): {large_tot/gt*100:.1f}% of total "
                        f"(${large_tot:,.0f}, {large_n:,} donations)"
                    )
            if org_total:
                lines.append(
                    f"  Org/PAC donors: {org_total/gt*100:.0f}% of total "
                    f"(${org_total:,.0f} across {org.get('txns',0):,} contributions, "
                    f"avg ${org.get('avg_amt',0):,.0f})"
                )
                large_n   = org.get("large_n", 0)
                large_tot = org.get("large_total", 0)
                if large_tot:
                    lines.append(
                        f"    Large org/PAC contributions ($10k+): {large_tot/gt*100:.1f}% of total "
                        f"(${large_tot:,.0f}, {large_n:,} contributions)"
                    )
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
