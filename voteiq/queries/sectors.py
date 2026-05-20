import sqlite3
from voteiq.db import federal, virginia


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
