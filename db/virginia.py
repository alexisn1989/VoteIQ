# db/virginia.py
# Virginia state database connections and query runners.
# All state data queries live here.
# Imports config from finance_rules — never hardcodes.

import sqlite3
import os
from typing import Optional
from config.finance_rules import VIRGINIA_CONFIG


# ── CONNECTION ────────────────────────────────────────────────────────────────

def get_connection():
    """
    Return SQLite connection to Virginia
    legislative intelligence database.
    Uses VIRGINIA_CONFIG.db_path as source of truth.
    """
    db_path = VIRGINIA_CONFIG.db_path

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Virginia database not found at '{db_path}'. "
            f"Run the VA SBE pipeline first."
        )

    conn = sqlite3.connect(
        db_path,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: tuple = ()) -> list:
    """
    Execute a SELECT query against Virginia DB.
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

def get_sector_totals(lis_id: str,
                      cycle: str = "2023") -> list:
    """
    Sector totals for a Virginia legislator.
    Returns: sector, total, donor_count, avg_donation
    """
    return query("""
        SELECT
            sector,
            SUM(amount)     AS total,
            COUNT(*)        AS donor_count,
            AVG(amount)     AS avg_donation
        FROM donor_sector_totals
        WHERE legislator_id = ?
        AND cycle = ?
        GROUP BY sector
        ORDER BY total DESC
    """, (lis_id, cycle))


def get_top_employers(lis_id: str,
                      cycle: str = "2023",
                      limit: int = 20) -> list:
    """
    Top employers in Virginia donor base.
    VA SBE OccupationOrTypeOfBusiness field
    is richer than FEC employer data.
    Returns: employer, occupation, total,
             donor_count, avg_donation
    """
    return query("""
        SELECT
            NameOfEmployer          AS employer,
            OccupationOrTypeOfBusiness AS occupation,
            SUM(Amount)             AS total,
            COUNT(*)                AS donor_count,
            AVG(Amount)             AS avg_donation
        FROM va_sbe_contributions
        WHERE legislator_id = ?
        AND cycle = ?
        AND NameOfEmployer IS NOT NULL
        AND NameOfEmployer != ''
        GROUP BY NameOfEmployer,
                 OccupationOrTypeOfBusiness
        ORDER BY total DESC
        LIMIT ?
    """, (lis_id, cycle, limit))


def get_mega_donors(lis_id: str,
                    cycle: str = "2023",
                    threshold: float = 10_000) -> list:
    """
    Individual donors above mega-donor threshold.
    Virginia-specific — no contribution limits mean
    single donors can give unlimited amounts.
    Returns: name, employer, total, transactions
    """
    return query("""
        SELECT
            ContributorName         AS name,
            NameOfEmployer          AS employer,
            OccupationOrTypeOfBusiness AS occupation,
            SUM(Amount)             AS total,
            COUNT(*)                AS transactions,
            CASE
                WHEN SUM(Amount) >= 50000
                    THEN 'Major Institutional'
                WHEN SUM(Amount) >= 10000
                    THEN 'Mega-donor'
                ELSE 'Large Donor'
            END                     AS donor_tier
        FROM va_sbe_contributions
        WHERE legislator_id = ?
        AND cycle = ?
        GROUP BY ContributorName,
                 NameOfEmployer,
                 OccupationOrTypeOfBusiness
        HAVING SUM(Amount) >= ?
        ORDER BY total DESC
    """, (lis_id, cycle, threshold))


def get_party_committee_donors(lis_id: str,
                                cycle: str = "2023") -> list:
    """
    Party caucus committee contributions.
    In Virginia these are often the largest
    single donors in competitive races.
    Returns: committee_name, total, transactions
    """
    return query("""
        SELECT
            ContributorName         AS committee_name,
            SUM(Amount)             AS total,
            COUNT(*)                AS transactions
        FROM va_sbe_contributions
        WHERE legislator_id = ?
        AND cycle = ?
        AND (
            ContributorName LIKE '%caucus%'
            OR ContributorName LIKE '%democratic%'
            OR ContributorName LIKE '%republican%'
            OR ContributorName LIKE '%party%'
            OR OccupationOrTypeOfBusiness
                LIKE '%political%'
        )
        GROUP BY ContributorName
        ORDER BY total DESC
    """, (lis_id, cycle))


# ── DONOR SHIFT QUERIES ───────────────────────────────────────────────────────

def get_donor_shift(lis_id: str) -> list:
    """
    Multi-cycle donor profile — 25yr career arc.
    Most powerful query in the Virginia dataset.
    Returns: cycle, sector, total,
             donor_count, avg_donation
    """
    return query("""
        SELECT
            cycle,
            sector,
            SUM(amount)     AS total,
            COUNT(*)        AS donor_count,
            AVG(amount)     AS avg_donation
        FROM donor_sector_totals
        WHERE legislator_id = ?
        GROUP BY cycle, sector
        ORDER BY cycle ASC, total DESC
    """, (lis_id,))


# ── GEOGRAPHY QUERIES ─────────────────────────────────────────────────────────

def get_donor_geography(lis_id: str,
                         cycle: str = "2023") -> list:
    """
    Geographic breakdown of Virginia donor base.
    Returns: city, state, donations, total, origin
    """
    return query("""
        SELECT
            city,
            state,
            COUNT(*)        AS donations,
            SUM(amount)     AS total,
            CASE
                WHEN state != 'VA'
                    THEN 'Out of State'
                ELSE 'In State'
            END             AS origin
        FROM va_sbe_contributions
        WHERE legislator_id = ?
        AND cycle = ?
        AND city IS NOT NULL
        GROUP BY city, state, origin
        ORDER BY total DESC
        LIMIT 25
    """, (lis_id, cycle))


# ── VOTING QUERIES ────────────────────────────────────────────────────────────

def get_votes(lis_id: str,
              session: str = "2024") -> list:
    """
    Voting record for a Virginia legislator.
    Returns: bill_id, vote, vote_date
    """
    return query("""
        SELECT
            bill_id,
            vote,
            vote_date
        FROM va_votes
        WHERE lis_id = ?
        AND session = ?
        ORDER BY vote_date DESC
    """, (lis_id, session))


def get_party_defections(lis_id: str,
                          session: str = "2024") -> list:
    """
    Votes where legislator broke with party majority.
    Returns: bill_id, member_vote,
             party_majority, bill_title
    """
    return query("""
        SELECT
            v.bill_id,
            v.vote              AS member_vote,
            p.party_majority,
            b.title             AS bill_title,
            b.description
        FROM va_votes v
        JOIN party_vote_record p
            ON v.bill_id = p.bill_id
            AND v.session = p.session
        LEFT JOIN va_bills b
            ON v.bill_id = b.bill_id
        WHERE v.lis_id = ?
        AND v.session = ?
        AND v.vote != p.party_majority
        ORDER BY v.vote_date DESC
    """, (lis_id, session))


def get_loyalty_scores(lis_id: str) -> list:
    """
    Party loyalty score per session.
    Returns: session, loyal_votes,
             total_votes, loyalty_pct
    """
    return query("""
        SELECT
            v.session,
            COUNT(*) FILTER (
                WHERE v.vote = p.party_majority
            )               AS loyal_votes,
            COUNT(*)        AS total_votes,
            ROUND(
                COUNT(*) FILTER (
                    WHERE v.vote = p.party_majority
                ) * 100.0 /
                NULLIF(COUNT(*), 0), 1
            )               AS loyalty_pct
        FROM va_votes v
        JOIN party_vote_record p
            ON v.bill_id = p.bill_id
            AND v.session = p.session
        WHERE v.lis_id = ?
        GROUP BY v.session
        ORDER BY v.session ASC
    """, (lis_id,))


# ── BILL QUERIES ──────────────────────────────────────────────────────────────

def get_sponsored_bills(lis_id: str,
                         session: str = "2024") -> list:
    """
    Bills sponsored by a Virginia legislator.
    Returns: bill_id, title, description,
             introduced_date, status
    """
    return query("""
        SELECT
            bill_id,
            title,
            description,
            introduced_date,
            status
        FROM va_bills
        WHERE introduced_by = ?
        AND session = ?
        ORDER BY introduced_date DESC
    """, (lis_id, session))


def get_effectiveness_stats(lis_id: str,
                             session: str = "2024") -> dict:
    """
    Legislative effectiveness for Virginia legislator.
    Returns dict with introduced, passed,
    pass_rate, bipartisan counts.
    """
    bills = query("""
        SELECT
            COUNT(*)                AS introduced,
            COUNT(*) FILTER (
                WHERE status LIKE '%passed%'
                OR status LIKE '%enacted%'
                OR status LIKE '%signed%'
            )                       AS passed,
            AVG(cosponsor_count)    AS avg_cosponsors,
            COUNT(*) FILTER (
                WHERE cosponsor_count > 0
            )                       AS has_cosponsors
        FROM va_bills
        WHERE introduced_by = ?
        AND session = ?
    """, (lis_id, session))

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
        "avg_cosponsors":   row["avg_cosponsors"] or 0.0,
        "bipartisan_bills": row["has_cosponsors"] or 0,
    }


# ── LEGISLATOR LOOKUP ─────────────────────────────────────────────────────────

def get_legislator(lis_id: str) -> Optional[dict]:
    """
    Look up a Virginia legislator by LIS ID.
    Returns dict or None if not found.
    """
    rows = query("""
        SELECT
            lis_id,
            name,
            party,
            chamber,
            district,
            active
        FROM legislators
        WHERE lis_id = ?
        LIMIT 1
    """, (lis_id,))

    if not rows:
        return None

    row = rows[0]
    return {
        "lis_id":   row["lis_id"],
        "name":     row["name"],
        "party":    row["party"],
        "chamber":  row["chamber"],
        "district": row["district"],
        "active":   row["active"],
    }


def get_legislator_by_name(name: str) -> Optional[dict]:
    """
    Look up a Virginia legislator by name (fuzzy).
    Returns dict or None if not found.
    """
    rows = query("""
        SELECT
            lis_id,
            name,
            party,
            chamber,
            district,
            active
        FROM legislators
        WHERE name LIKE ?
        LIMIT 1
    """, (f"%{name}%",))

    if not rows:
        return None

    row = rows[0]
    return {
        "lis_id":   row["lis_id"],
        "name":     row["name"],
        "party":    row["party"],
        "chamber":  row["chamber"],
        "district": row["district"],
        "active":   row["active"],
    }


# ── INDUSTRY NETWORK ──────────────────────────────────────────────────────────

def get_industry_network(sector: str,
                          cycle: str = "2023",
                          limit: int = 20) -> list:
    """
    All Virginia legislators ranked by sector funding.
    Returns: name, party, district, chamber,
             total, donor_count, avg_donation
    """
    return query("""
        SELECT
            l.name,
            l.party,
            l.district,
            l.chamber,
            SUM(d.total_amount) AS total,
            SUM(d.donor_count)  AS donor_count,
            AVG(d.total_amount /
                NULLIF(d.donor_count, 0)
            )                   AS avg_donation
        FROM donor_sector_totals d
        JOIN legislators l
            ON d.legislator_id = l.lis_id
        WHERE d.sector = ?
        AND d.cycle = ?
        GROUP BY l.name, l.party,
                 l.district, l.chamber
        ORDER BY total DESC
        LIMIT ?
    """, (sector, cycle, limit))


# ── DATA AVAILABILITY CHECK ───────────────────────────────────────────────────

def check_data_available(lis_id: str,
                          cycle: str = "2023") -> dict:
    """
    Check what data is available for a legislator
    before running analyst.
    Used by chat endpoint for default responses.
    """
    legislator = get_legislator(lis_id)
    sectors    = get_sector_totals(lis_id, cycle)
    votes      = get_votes(lis_id)
    bills      = get_sponsored_bills(lis_id)
    mega       = get_mega_donors(lis_id, cycle)
    party_cmte = get_party_committee_donors(lis_id, cycle)

    return {
        "legislator":        legislator,
        "has_finance":       len(sectors) > 0,
        "has_votes":         len(votes) > 0,
        "has_bills":         len(bills) > 0,
        "has_mega_donors":   len(mega) > 0,
        "has_party_money":   len(party_cmte) > 0,
        "finance_sectors":   len(sectors),
        "vote_count":        len(votes),
        "bill_count":        len(bills),
        "mega_donor_count":  len(mega),
        "cycle":             cycle,
    }
