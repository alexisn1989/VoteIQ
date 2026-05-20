import sqlite3
from voteiq.db import federal, virginia


def get_federal_committees(bioguide_id: str) -> list[str]:
    try:
        rows = federal.query("""
            SELECT committee_name
            FROM congress_member_committees
            WHERE bioguide_id = ?
            ORDER BY committee_name
        """, (bioguide_id,))
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


def get_state_committees(lis_id: str) -> list[str]:
    try:
        rows = virginia.query("""
            SELECT committee_name
            FROM va_member_committees
            WHERE lis_id = ?
            ORDER BY committee_name
        """, (lis_id,))
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []
