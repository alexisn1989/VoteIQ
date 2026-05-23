"""
queries/timing.py
Donation-vote timing proximity queries for Virginia legislators.

These queries show temporal proximity between contributions
and recorded votes within a configurable day window.

They do NOT show coordination, influence, motive, reward,
or causation — only timing overlap in public records.

Date format note:
  va_sbe_contributions.transaction_date  YYYY-MM-DD (ISO — stored by build pipeline)
  va_votes.vote_date                     YYYY-MM-DD (ISO)
  Both are ISO so julianday() works directly.
"""
import sqlite3
from voteiq.db import virginia

# transaction_date is stored as ISO (YYYY-MM-DD) by build_va_state_finance.py
_ISO_DATE = "c.transaction_date"


def get_donation_timing(
    lis_id:      str,
    cycle:       str = "2024",
    session:     str = "2024",
    days_window: int = 30,
) -> list:
    """
    Return individual donation-vote pairs where the contribution
    falls within days_window days before or after a recorded vote.

    This shows timing proximity only.
    It does not show coordination, influence, motive, reward, or causation.

    Rows: (contributor_name, employer, sector, amount, donation_date,
           bill_number, vote, vote_date, days_from_vote, timing_type,
           bill_title, description)
    """
    days_window = max(1, min(int(days_window), 180))

    sql = f"""
        SELECT
            c.contributor_name,
            c.employer                          AS contributor_employer,
            COALESCE(c.sector, 'Unclassified') AS employer_sector,
            c.amount,
            c.transaction_date                  AS donation_date,

            v.bill_number,
            v.vote,
            v.vote_date,

            CAST(
                julianday({_ISO_DATE}) -
                julianday(v.vote_date)
                AS INTEGER
            )                                   AS days_from_vote,

            CASE
                WHEN julianday({_ISO_DATE}) - julianday(v.vote_date)
                     BETWEEN -? AND -1
                    THEN 'Pre-vote donation'
                WHEN julianday({_ISO_DATE}) - julianday(v.vote_date)
                     BETWEEN 1 AND ?
                    THEN 'Post-vote donation'
                WHEN julianday({_ISO_DATE}) = julianday(v.vote_date)
                    THEN 'Same-day donation'
            END                                 AS timing_type,

            b.title                             AS bill_title,
            b.description

        FROM va_sbe_contributions c

        JOIN va_votes v
            ON c.legislator_id = v.legislator_id

        LEFT JOIN va_bills b
            ON  v.bill_number = b.bill_number
            AND v.session     = b.session

        WHERE c.legislator_id    = ?
          AND c.cycle            = ?
          AND v.session          = ?
          AND c.transaction_date IS NOT NULL
          AND v.vote_date        IS NOT NULL
          AND ABS(
                julianday({_ISO_DATE}) -
                julianday(v.vote_date)
              ) <= ?

        ORDER BY
            ABS(days_from_vote) ASC,
            c.amount DESC
    """

    try:
        return virginia.query(sql, (
            days_window,    # BETWEEN -? AND -1  (pre-vote)
            days_window,    # BETWEEN 1 AND ?    (post-vote)
            lis_id,
            cycle,
            session,
            days_window,    # ABS(...) <= ?
        ))
    except sqlite3.OperationalError:
        return []


def get_timing_summary(
    lis_id:      str,
    cycle:       str = "2024",
    session:     str = "2024",
    days_window: int = 30,
) -> dict:
    """
    Pre-vote / post-vote / same-day donation summary by sector.
    Parameterized — no SQL injection risk.

    Returns:
        {
          "pre_vote":    {sector: {"total": float, "count": int}, ...},
          "post_vote":   {sector: {"total": float, "count": int}, ...},
          "same_day":    {sector: {"total": float, "count": int}, ...},
          "days_window": int,
        }
    """
    if not lis_id:
        return {"pre_vote": {}, "post_vote": {}, "same_day": {}, "days_window": days_window}

    days_window = max(1, min(int(days_window), 180))

    sql = f"""
        SELECT
            COALESCE(c.sector, 'Unclassified') AS sector,
            CASE
                WHEN julianday({_ISO_DATE}) - julianday(v.vote_date)
                     BETWEEN -? AND -1
                    THEN 'Pre-vote'
                WHEN julianday({_ISO_DATE}) - julianday(v.vote_date)
                     BETWEEN 1 AND ?
                    THEN 'Post-vote'
                ELSE 'Same-day'
            END                                 AS timing_window,
            COUNT(*)                            AS donation_count,
            ROUND(SUM(c.amount), 2)             AS total_amount,
            ROUND(AVG(c.amount), 2)             AS avg_amount

        FROM va_sbe_contributions c

        JOIN va_votes v
            ON c.legislator_id = v.legislator_id

        WHERE c.legislator_id    = ?
          AND c.cycle            = ?
          AND v.session          = ?
          AND c.transaction_date IS NOT NULL
          AND v.vote_date        IS NOT NULL
          AND ABS(
                julianday({_ISO_DATE}) -
                julianday(v.vote_date)
              ) <= ?

        GROUP BY sector, timing_window
        HAVING total_amount > 0
        ORDER BY total_amount DESC
    """

    try:
        rows = virginia.query(sql, (
            days_window,
            days_window,
            lis_id,
            cycle,
            session,
            days_window,
        ))
    except sqlite3.OperationalError:
        rows = []

    pre_vote:  dict = {}
    post_vote: dict = {}
    same_day:  dict = {}

    for row in rows:
        sector, window, count, total, _ = row[0], row[1], row[2], row[3], row[4]
        total = float(total or 0)
        count = int(count or 0)
        bucket = (
            pre_vote  if window == "Pre-vote"  else
            post_vote if window == "Post-vote" else
            same_day
        )
        bucket[sector] = {"total": total, "count": count}

    return {
        "pre_vote":    pre_vote,
        "post_vote":   post_vote,
        "same_day":    same_day,
        "days_window": days_window,
    }


def get_governor_action_timing_overlap(
    session: str = "2026",
    cycle: str = "2026",
    days_window: int = 30,
) -> list:
    """
    Return donations within +/- days_window of a governor action date.

    This shows timing overlap in public records only.
    Correlation does not imply causation.

    Rows: (action_label, action, bill_number, action_date, sponsor_name,
           sponsor_party, sector, total_amount, donor_count, timing_window)
    """
    days_window = max(1, min(int(days_window), 180))

    sql = f"""
        SELECT
            ga.action_label,
            ga.action,
            ga.bill_number,
            ga.action_date,
            ga.sponsor_name,
            ga.sponsor_party,
            COALESCE(c.sector, 'Unclassified') AS sector,
            ROUND(SUM(c.amount), 2) AS total_amount,
            COUNT(*) AS donor_count,
            CASE
                WHEN julianday(c.transaction_date) - julianday(ga.action_date)
                     BETWEEN -? AND -1
                    THEN 'Pre-action donation'
                WHEN julianday(c.transaction_date) - julianday(ga.action_date)
                     BETWEEN 1 AND ?
                    THEN 'Post-action donation'
                ELSE 'Same-day donation'
            END AS timing_window
        FROM governor_actions ga
        JOIN va_sbe_contributions c
            ON c.legislator_id = ga.sponsor_lis_id
        WHERE ga.session = ?
          AND c.cycle = ?
          AND ga.action_date IS NOT NULL
          AND ga.action_date != ''
          AND c.transaction_date IS NOT NULL
          AND ABS(julianday(c.transaction_date) - julianday(ga.action_date)) <= ?
        GROUP BY
            ga.action_label,
            ga.action,
            ga.bill_number,
            ga.action_date,
            ga.sponsor_name,
            ga.sponsor_party,
            sector,
            timing_window
        ORDER BY ga.action_label, ABS(julianday(MAX(c.transaction_date)) - julianday(ga.action_date)), total_amount DESC
    """

    try:
        return virginia.query(sql, (
            days_window,
            days_window,
            session,
            cycle,
            days_window,
        ))
    except sqlite3.OperationalError:
        return []
