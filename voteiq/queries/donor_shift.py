import sqlite3
from voteiq.db import federal, virginia


def get_federal_donor_shift(candidate_id: str) -> list:
    return federal.query("""
        SELECT cycle,
               sector,
               total_amount,
               donor_count,
               ROUND(total_amount / MAX(donor_count, 1), 2) AS avg_donation
        FROM candidate_sector_totals
        WHERE candidate_id = ?
        ORDER BY cycle ASC, total_amount DESC
    """, (candidate_id,))


def get_state_donor_shift(lis_id: str) -> list:
    try:
        return virginia.query("""
            SELECT cycle,
                   sector,
                   SUM(amount)  AS total,
                   COUNT(*)     AS donors,
                   AVG(amount)  AS avg_donation
            FROM va_sbe_contributions
            WHERE legislator_id = ?
            GROUP BY cycle, sector
            ORDER BY cycle ASC, total DESC
        """, (lis_id,))
    except sqlite3.OperationalError:
        return []
