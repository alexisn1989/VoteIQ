# db/federal.py
# Federal database connections and query runners.
# All federal data queries live here.
# Imports config from finance_rules — never hardcodes.

import sqlite3
import os
from pathlib import Path
from typing import Optional
from config.finance_rules import FEDERAL_CONFIG


# ── CONNECTION ────────────────────────────────────────────────────────────────

def get_connection():
    """
    Return SQLite connection to polls.db.
    Uses FEDERAL_CONFIG.db_path as source of truth.
    """
    db_path = FEDERAL_CONFIG.db_path

    if not os.path.exists(db_path):
        # polls.db lives next to the project root
        db_path = str(Path(__file__).resolve().parent.parent / "polls.db")

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Federal database not found at '{db_path}'. "
            f"Run the FEC pipeline first."
        )

    conn = sqlite3.connect(
        db_path,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def query(sql: str, params: tuple = ()) -> list:
    """
    Execute a SELECT query against federal DB.
    Returns list of Row objects (accessible by name).
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


# ── SECTOR QUERIES ────────────────────────────────────────────────────────────

def get_sector_totals(candidate_id: str,
                      cycle: int = 2024) -> list:
    """
    Sector totals for a federal candidate.
    candidate_id is FEC format (e.g. H2VA02064).
    Returns: sector, total, donor_count, avg_donation
    """
    return query("""
        SELECT
            sector,
            total_amount                                    AS total,
            donor_count,
            ROUND(total_amount / MAX(donor_count, 1), 2)   AS avg_donation
        FROM candidate_sector_totals
        WHERE candidate_id = ?
          AND cycle = ?
        ORDER BY total_amount DESC
    """, (candidate_id, cycle))


def get_top_employers(candidate_id: str,
                      cycle: int = 2024,
                      limit: int = 20) -> list:
    """
    Top employer names from FEC Schedule A filings.
    Returns: employer, total, donor_count, avg_donation
    """
    return query("""
        SELECT
            employer_clean                              AS employer,
            employer_sector                             AS sector,
            SUM(contribution_receipt_amount)            AS total,
            COUNT(*)                                    AS donor_count,
            AVG(contribution_receipt_amount)            AS avg_donation
        FROM fec_individual_contributions
        WHERE candidate_id = ?
          AND cycle = ?
          AND employer_clean IS NOT NULL
          AND employer_clean != ''
        GROUP BY employer_clean, employer_sector
        ORDER BY total DESC
        LIMIT ?
    """, (candidate_id, cycle, limit))


# ── DONOR SHIFT QUERIES ───────────────────────────────────────────────────────

def get_donor_shift(candidate_id: str) -> list:
    """
    Multi-cycle donor profile.
    Returns: cycle, sector, total,
             donor_count, avg_donation
    """
    return query("""
        SELECT
            cycle,
            sector,
            total_amount                                    AS total,
            donor_count,
            ROUND(total_amount / MAX(donor_count, 1), 2)   AS avg_donation
        FROM candidate_sector_totals
        WHERE candidate_id = ?
        ORDER BY cycle ASC, total_amount DESC
    """, (candidate_id,))


# ── GEOGRAPHY QUERIES ─────────────────────────────────────────────────────────

def get_donor_geography(candidate_id: str,
                         cycle: int = 2024) -> list:
    """
    Geographic breakdown of federal donor base.
    FEC Schedule A includes contributor city/state.
    Returns: city, state, donations, total, origin
    """
    return query("""
        SELECT
            contributor_city                        AS city,
            contributor_state                       AS state,
            COUNT(*)                                AS donations,
            SUM(contribution_receipt_amount)        AS total,
            CASE
                WHEN contributor_state != 'VA'
                    THEN 'Out of State'
                ELSE 'In State'
            END                                     AS origin
        FROM fec_individual_contributions
        WHERE candidate_id = ?
          AND cycle = ?
          AND contributor_city IS NOT NULL
        GROUP BY contributor_city, contributor_state, origin
        ORDER BY total DESC
        LIMIT 25
    """, (candidate_id, cycle))


# ── VOTING QUERIES ────────────────────────────────────────────────────────────

def get_votes(bioguide_id: str,
              congress: int = 119) -> list:
    """
    Voting record for a federal member.
    Returns: bill, member_vote, vote_date, chamber, result
    """
    return query("""
        SELECT
            bill,
            member_vote,
            vote_date,
            chamber,
            result,
            vote_number,
            congress
        FROM congress_votes
        WHERE bioguide_id = ?
          AND congress = ?
        ORDER BY vote_date DESC
    """, (bioguide_id, congress))


def get_party_defections(bioguide_id: str,
                          congress: int = 119) -> list:
    """
    Votes where member broke with party majority.
    Joins congress_votes with party_majority_votes
    if available; otherwise computes inline.
    Returns: bill, member_vote, party_majority, result
    """
    return query("""
        SELECT
            cv.bill,
            cv.member_vote,
            cv.result,
            cv.vote_date,
            cv.vote_number,
            cm.party
        FROM congress_votes cv
        JOIN congress_members cm
            ON cm.bioguide_id = cv.bioguide_id
        WHERE cv.bioguide_id = ?
          AND cv.congress = ?
          AND (
            (cm.party = 'R' AND cv.member_vote = 'Nay'
             AND cv.result LIKE '%Passed%')
            OR
            (cm.party = 'R' AND cv.member_vote = 'Yea'
             AND cv.result LIKE '%Failed%')
            OR
            (cm.party = 'D' AND cv.member_vote = 'Yea'
             AND cv.result LIKE '%Passed%'
             AND cv.congress = 119)
            OR
            (cm.party = 'D' AND cv.member_vote = 'Nay'
             AND cv.result LIKE '%Failed%'
             AND cv.congress = 119)
          )
        ORDER BY cv.vote_date DESC
    """, (bioguide_id, congress))


def get_loyalty_scores(bioguide_id: str) -> list:
    """
    Party loyalty score per congress.
    Returns: congress, loyal_votes,
             total_votes, loyalty_pct
    """
    return query("""
        SELECT
            cv.congress,
            COUNT(*) FILTER (
                WHERE (cm.party = 'R'
                       AND cv.member_vote = 'Yea'
                       AND cv.result LIKE '%Passed%')
                   OR (cm.party = 'R'
                       AND cv.member_vote = 'Nay'
                       AND cv.result LIKE '%Failed%')
                   OR (cm.party = 'D'
                       AND cv.member_vote = 'Nay'
                       AND cv.result LIKE '%Passed%')
                   OR (cm.party = 'D'
                       AND cv.member_vote = 'Yea'
                       AND cv.result LIKE '%Failed%')
            )                   AS loyal_votes,
            COUNT(*)            AS total_votes,
            ROUND(
                COUNT(*) FILTER (
                    WHERE (cm.party = 'R'
                           AND cv.member_vote = 'Yea'
                           AND cv.result LIKE '%Passed%')
                       OR (cm.party = 'R'
                           AND cv.member_vote = 'Nay'
                           AND cv.result LIKE '%Failed%')
                       OR (cm.party = 'D'
                           AND cv.member_vote = 'Nay'
                           AND cv.result LIKE '%Passed%')
                       OR (cm.party = 'D'
                           AND cv.member_vote = 'Yea'
                           AND cv.result LIKE '%Failed%')
                ) * 100.0 / NULLIF(COUNT(*), 0), 1
            )                   AS loyalty_pct
        FROM congress_votes cv
        JOIN congress_members cm
            ON cm.bioguide_id = cv.bioguide_id
        WHERE cv.bioguide_id = ?
        GROUP BY cv.congress
        ORDER BY cv.congress ASC
    """, (bioguide_id,))


# ── BILL QUERIES ──────────────────────────────────────────────────────────────

def get_sponsored_bills(bioguide_id: str,
                         congress: int = 119) -> list:
    """
    Bills sponsored by a federal member.
    Returns: bill_number, bill_type, title,
             introduced_date, policy_area, status
    """
    return query("""
        SELECT
            bill_number,
            bill_type,
            title,
            introduced_date,
            policy_area,
            subjects,
            status
        FROM federal_bills
        WHERE bioguide_id = ?
          AND congress = ?
        ORDER BY introduced_date DESC
    """, (bioguide_id, congress))


def get_effectiveness_stats(bioguide_id: str,
                             congress: int = 119) -> dict:
    """
    Legislative effectiveness for a federal member.
    Returns dict with introduced, passed,
    pass_rate, and subject coverage.
    """
    bills = query("""
        SELECT
            COUNT(*)                                AS introduced,
            COUNT(*) FILTER (
                WHERE status LIKE '%Became Law%'
                OR status LIKE '%Passed%'
                OR status LIKE '%Signed%'
            )                                       AS passed,
            COUNT(DISTINCT policy_area)             AS subject_areas
        FROM federal_bills
        WHERE bioguide_id = ?
          AND congress = ?
    """, (bioguide_id, congress))

    if not bills:
        return {
            "bills_introduced": 0,
            "bills_passed":     0,
            "pass_rate":        0.0,
            "avg_cosponsors":   0.0,
            "bipartisan_bills": 0,
        }

    row = bills[0]
    introduced = row["introduced"] or 0
    passed     = row["passed"] or 0

    return {
        "bills_introduced": introduced,
        "bills_passed":     passed,
        "pass_rate":        (passed / introduced * 100)
                            if introduced else 0.0,
        "avg_cosponsors":   0.0,   # not in federal_bills schema yet
        "bipartisan_bills": 0,     # not in federal_bills schema yet
    }


# ── MEMBER LOOKUP ─────────────────────────────────────────────────────────────

def get_member(bioguide_id: str) -> Optional[dict]:
    """
    Look up a federal member by bioguide ID.
    Returns dict or None if not found.
    """
    rows = query("""
        SELECT
            bioguide_id,
            name,
            party,
            state,
            chamber,
            district
        FROM congress_members
        WHERE bioguide_id = ?
        LIMIT 1
    """, (bioguide_id,))

    if not rows:
        return None

    row = rows[0]
    return {
        "bioguide_id": row["bioguide_id"],
        "name":        row["name"],
        "party":       row["party"],
        "state":       row["state"],
        "chamber":     row["chamber"],
        "district":    row["district"],
    }


def get_member_by_name(name: str) -> Optional[dict]:
    """
    Look up a federal member by name (fuzzy).
    Returns dict or None if not found.
    """
    rows = query("""
        SELECT
            bioguide_id,
            name,
            party,
            state,
            chamber,
            district
        FROM congress_members
        WHERE name LIKE ?
        LIMIT 1
    """, (f"%{name}%",))

    if not rows:
        return None

    row = rows[0]
    return {
        "bioguide_id": row["bioguide_id"],
        "name":        row["name"],
        "party":       row["party"],
        "state":       row["state"],
        "chamber":     row["chamber"],
        "district":    row["district"],
    }


def get_fec_id(bioguide_id: str) -> Optional[str]:
    """Resolve bioguide_id → FEC candidate_id via bridge."""
    rows = query("""
        SELECT fec_candidate_id
        FROM bioguide_fec_bridge
        WHERE bioguide_id = ?
        LIMIT 1
    """, (bioguide_id,))
    return rows[0]["fec_candidate_id"] if rows else None


def get_bioguide_id(candidate_id: str) -> Optional[str]:
    """Resolve FEC candidate_id → bioguide_id via bridge."""
    rows = query("""
        SELECT bioguide_id
        FROM bioguide_fec_bridge
        WHERE fec_candidate_id = ?
        LIMIT 1
    """, (candidate_id,))
    return rows[0]["bioguide_id"] if rows else None


# ── INDUSTRY NETWORK ──────────────────────────────────────────────────────────

def get_industry_network(sector: str,
                          cycle: int = 2024,
                          limit: int = 20) -> list:
    """
    All Virginia federal members ranked by sector funding.
    Returns: name, party, state, chamber,
             total, donor_count, avg_donation
    """
    return query("""
        SELECT
            m.name,
            m.party,
            m.state,
            m.chamber,
            f.total_amount                                  AS total,
            f.donor_count,
            ROUND(f.total_amount / MAX(f.donor_count, 1), 2) AS avg_donation
        FROM candidate_sector_totals f
        JOIN bioguide_fec_bridge bridge
            ON bridge.fec_candidate_id = f.candidate_id
        JOIN congress_members m
            ON m.bioguide_id = bridge.bioguide_id
        WHERE f.sector = ?
          AND f.cycle = ?
        ORDER BY f.total_amount DESC
        LIMIT ?
    """, (sector, cycle, limit))


# ── TIMING QUERIES ───────────────────────────────────────────────────────────

def get_donation_timing(
    bioguide_id: str,
    cycle:       int = 2024,
    congress:    int = 119,
    days_window: int = 30,
) -> list:
    """
    Donations within days_window of a recorded vote.
    Classified as pre-vote, post-vote, or same-day.
    Timing proximity is a pattern only — not evidence
    of coordination or improper influence.

    Returns: contributor_name, contributor_employer,
             employer_sector, amount, donation_date,
             bill_number, vote, vote_date,
             days_from_vote, timing_type,
             bill_title, policy_area
    """
    days_window = max(1, min(int(days_window), 180))

    return query("""
        SELECT
            c.contributor_name,
            c.contributor_employer,
            COALESCE(c.employer_sector, 'Unclassified')
                                                AS employer_sector,
            c.amount,
            c.contribution_date                 AS donation_date,
            v.bill,
            v.member_vote                       AS vote,
            v.vote_date,
            CAST(
                julianday(c.contribution_date) -
                julianday(v.vote_date)
                AS INTEGER
            )                                   AS days_from_vote,
            CASE
                WHEN julianday(c.contribution_date) -
                     julianday(v.vote_date) BETWEEN -? AND -1
                    THEN 'Pre-vote donation'
                WHEN julianday(c.contribution_date) -
                     julianday(v.vote_date) BETWEEN 1 AND ?
                    THEN 'Post-vote donation'
                WHEN julianday(c.contribution_date) =
                     julianday(v.vote_date)
                    THEN 'Same-day donation'
            END                                 AS timing_type,
            b.title                             AS bill_title,
            b.policy_area
        FROM fec_individual_contributions c
        JOIN bioguide_fec_bridge bridge
            ON bridge.fec_candidate_id = c.candidate_id
        JOIN congress_votes v
            ON v.bioguide_id = bridge.bioguide_id
        LEFT JOIN federal_bills b
            ON  b.bill_number = v.bill
            AND b.congress    = v.congress
        WHERE bridge.bioguide_id = ?
          AND c.cycle            = ?
          AND v.congress         = ?
          AND c.contribution_date IS NOT NULL
          AND v.vote_date         IS NOT NULL
          AND ABS(
                julianday(c.contribution_date) -
                julianday(v.vote_date)
              ) <= ?
        ORDER BY
            ABS(days_from_vote) ASC,
            c.amount DESC
        LIMIT 500
    """, (
        days_window,
        days_window,
        bioguide_id,
        cycle,
        congress,
        days_window,
    ))


# ── SHARED DONOR QUERIES ─────────────────────────────────────────────────────

def get_shared_donors_named(
    bioguide_ids:   list,
    cycle:          int = 2024,
    min_candidates: int = 2,
) -> list:
    """
    Shared donor query with normalized name/employer keys,
    candidate names resolved, date range, and three-tier
    overlap classification.

    Shows individual donors who gave to multiple selected
    candidates. Indicates donor-network overlap — not
    coordination or improper influence.

    Returns: donor_key, contributor_name, employer_key,
             contributor_employer, employer_sector,
             total_given, candidates_funded, candidates,
             avg_donation, total_transactions,
             first_contribution, last_contribution,
             overlap_type
    """
    if not bioguide_ids or len(bioguide_ids) < 2:
        return []

    # Deduplicate preserving order
    bioguide_ids = list(dict.fromkeys(bioguide_ids))

    min_candidates = max(2, min(min_candidates, len(bioguide_ids)))

    total_selected = len(bioguide_ids)
    placeholders   = ",".join("?" for _ in bioguide_ids)

    return query(f"""
        SELECT
            UPPER(TRIM(c.contributor_name))
                                                AS donor_key,
            MIN(c.contributor_name)             AS contributor_name,
            UPPER(TRIM(COALESCE(c.contributor_employer, '')))
                                                AS employer_key,
            MIN(c.contributor_employer)         AS contributor_employer,
            COALESCE(c.employer_sector, 'Unclassified')
                                                AS employer_sector,
            ROUND(SUM(c.amount), 2)             AS total_given,
            COUNT(DISTINCT bridge.bioguide_id)  AS candidates_funded,
            GROUP_CONCAT(DISTINCT m.name)       AS candidates,
            ROUND(AVG(c.amount), 2)             AS avg_donation,
            COUNT(*)                            AS total_transactions,
            MIN(c.contribution_date)            AS first_contribution,
            MAX(c.contribution_date)            AS last_contribution,
            CASE
                WHEN COUNT(DISTINCT bridge.bioguide_id) = {total_selected}
                    THEN 'Network donor across all'
                WHEN COUNT(DISTINCT bridge.bioguide_id) >= 3
                    THEN 'High-overlap donor'
                WHEN COUNT(DISTINCT bridge.bioguide_id) = 2
                    THEN 'Shared donor'
            END                                 AS overlap_type
        FROM fec_individual_contributions c
        JOIN bioguide_fec_bridge bridge
            ON bridge.fec_candidate_id = c.candidate_id
        JOIN congress_members m
            ON m.bioguide_id = bridge.bioguide_id
        WHERE bridge.bioguide_id IN ({placeholders})
          AND c.cycle = ?
          AND c.contributor_name IS NOT NULL
          AND TRIM(c.contributor_name) != ''
        GROUP BY
            donor_key,
            employer_key,
            COALESCE(c.employer_sector, 'Unclassified')
        HAVING COUNT(DISTINCT bridge.bioguide_id) >= ?
        ORDER BY
            candidates_funded DESC,
            total_given       DESC
    """, (*bioguide_ids, cycle, min_candidates))


# ── VOTE ALIGNMENT MATRIX ────────────────────────────────────────────────────

def get_vote_alignment_matrix(
    bioguide_ids: list,
    congress:     int = 119,
) -> list:
    """
    Pairwise vote agreement rate for a group of members.
    Only counts votes where both members cast Yea or Nay.
    Uses upper-triangle join (A < B) to avoid duplicate pairs.

    Returns: member_a, member_b, name_a, name_b,
             total_shared_votes, agreed_votes, agreement_pct
    """
    if not bioguide_ids or len(bioguide_ids) < 2:
        return []

    bioguide_ids = list(dict.fromkeys(bioguide_ids))
    placeholders = ",".join("?" for _ in bioguide_ids)

    return query(f"""
        SELECT
            v1.bioguide_id                              AS member_a,
            v2.bioguide_id                              AS member_b,
            m1.name                                     AS name_a,
            m2.name                                     AS name_b,
            COUNT(*)                                    AS total_shared_votes,
            SUM(CASE WHEN v1.member_vote = v2.member_vote
                     THEN 1 ELSE 0 END)                 AS agreed_votes,
            ROUND(
                SUM(CASE WHEN v1.member_vote = v2.member_vote
                         THEN 1.0 ELSE 0.0 END)
                * 100.0 / NULLIF(COUNT(*), 0), 1
            )                                           AS agreement_pct
        FROM congress_votes v1
        JOIN congress_votes v2
            ON  v2.bill        = v1.bill
            AND v2.congress    = v1.congress
            AND v2.bioguide_id > v1.bioguide_id
        JOIN congress_members m1
            ON m1.bioguide_id = v1.bioguide_id
        JOIN congress_members m2
            ON m2.bioguide_id = v2.bioguide_id
        WHERE v1.bioguide_id IN ({placeholders})
          AND v2.bioguide_id IN ({placeholders})
          AND v1.congress = ?
          AND v1.member_vote IN ('Yea', 'Nay')
          AND v2.member_vote IN ('Yea', 'Nay')
        GROUP BY v1.bioguide_id, v2.bioguide_id
        HAVING total_shared_votes >= 5
        ORDER BY agreement_pct DESC
    """, (*bioguide_ids, *bioguide_ids, congress))


# ── DATA AVAILABILITY CHECK ───────────────────────────────────────────────────

def check_data_available(bioguide_id: str,
                          cycle: int = 2024) -> dict:
    """
    Check what data is available for a federal member
    before running analyst.
    Used by chat endpoint for default responses.
    """
    member    = get_member(bioguide_id)
    fec_id    = get_fec_id(bioguide_id)
    sectors   = get_sector_totals(fec_id, cycle) if fec_id else []
    votes     = get_votes(bioguide_id)
    bills     = get_sponsored_bills(bioguide_id)

    return {
        "member":          member,
        "fec_id":          fec_id,
        "has_finance":     len(sectors) > 0,
        "has_votes":       len(votes) > 0,
        "has_bills":       len(bills) > 0,
        "finance_sectors": len(sectors),
        "vote_count":      len(votes),
        "bill_count":      len(bills),
        "cycle":           cycle,
    }
