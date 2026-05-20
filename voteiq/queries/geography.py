import sqlite3
from voteiq.db import virginia


def get_federal_geography(bioguide_id: str, cycle: int = 2024) -> list:
    # fec_industry_totals has no geographic breakdown
    # fec_individual_contributions has city/state but no bioguide_id crosswalk yet
    return []


def get_state_geography(lis_id: str, cycle: str = "2023") -> list:
    try:
        return virginia.query("""
            SELECT city,
                   state,
                   COUNT(*)     AS donations,
                   SUM(amount)  AS total,
                   CASE
                       WHEN state != 'VA' THEN 'Out of State'
                       ELSE 'In State'
                   END AS origin
            FROM va_sbe_contributions
            WHERE legislator_id = ?
              AND cycle = ?
            GROUP BY city, state, origin
            ORDER BY total DESC
            LIMIT 25
        """, (lis_id, cycle))
    except sqlite3.OperationalError:
        return []
