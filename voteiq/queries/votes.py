import sqlite3
from voteiq.db import federal, virginia


def get_federal_loyalty(bioguide_id: str) -> list:
    return federal.query("""
        SELECT congress,
               SUM(CASE
                   WHEN member_vote IN ('Yea','Yes') AND result LIKE 'Pass%' THEN 1
                   WHEN member_vote IN ('Nay','No')  AND result LIKE 'Fail%' THEN 1
                   ELSE 0 END)      AS loyal_votes,
               COUNT(*)             AS total_votes,
               ROUND(SUM(CASE
                   WHEN member_vote IN ('Yea','Yes') AND result LIKE 'Pass%' THEN 1
                   WHEN member_vote IN ('Nay','No')  AND result LIKE 'Fail%' THEN 1
                   ELSE 0 END) * 100.0 / MAX(COUNT(*), 1), 1) AS loyalty_pct
        FROM congress_votes
        WHERE bioguide_id = ?
        GROUP BY congress
        ORDER BY congress ASC
    """, (bioguide_id,))


def get_state_loyalty(lis_id: str) -> list:
    try:
        return virginia.query("""
        WITH bill_majority AS (
            SELECT bill_number, session,
                   CASE WHEN SUM(CASE WHEN vote = 'YES' THEN 1 ELSE 0 END) >
                             SUM(CASE WHEN vote = 'NO'  THEN 1 ELSE 0 END)
                        THEN 'YES' ELSE 'NO' END AS majority_vote
            FROM va_votes
            GROUP BY bill_number, session
        )
        SELECT v.session,
               SUM(CASE WHEN v.vote = m.majority_vote THEN 1 ELSE 0 END) AS loyal_votes,
               COUNT(*)                                                    AS total_votes,
               ROUND(SUM(CASE WHEN v.vote = m.majority_vote THEN 1 ELSE 0 END)
                     * 100.0 / MAX(COUNT(*), 1), 1)                       AS loyalty_pct
        FROM va_votes v
        JOIN bill_majority m
          ON m.bill_number = v.bill_number AND m.session = v.session
        WHERE v.legislator_id = ?
        GROUP BY v.session
        ORDER BY v.session ASC
        """, (lis_id,))
    except sqlite3.OperationalError:
        return []
