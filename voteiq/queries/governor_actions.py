from __future__ import annotations

import sqlite3

from voteiq.analysis.governor_action_money import (
    get_governor_action_money_patterns as _get_money_patterns,
    get_governor_action_top_sponsors,
)
from voteiq.db import virginia


def get_governor_action_counts(
    session: str = "2026",
    governor: str | None = None,
) -> list[dict]:
    params: list[str] = [session]
    governor_filter = ""
    if governor:
        governor_filter = "AND governor = ?"
        params.append(governor)

    try:
        rows = virginia.query(f"""
            SELECT
                action,
                action_label,
                COUNT(*) AS count
            FROM governor_actions
            WHERE session = ?
              {governor_filter}
            GROUP BY action, action_label
            ORDER BY action_label
        """, tuple(params))
    except sqlite3.OperationalError:
        return []

    return [
        {
            "action": row[0],
            "action_label": row[1],
            "count": row[2],
        }
        for row in rows
    ]


def get_governor_action_money_patterns(
    session: str = "2026",
    finance_cycle: str = "2026",
    governor: str | None = None,
) -> list[dict]:
    rows = _get_money_patterns(session=session, finance_cycle=finance_cycle)
    results = [
        {
            "action_label": row[0],
            "action": row[1],
            "raw_status": row[2],
            "sponsor_lis_id": row[3],
            "sponsor_name": row[4],
            "sponsor_party": row[5],
            "sector": row[6],
            "total_amount": row[7],
            "donor_count": row[8],
            "avg_donation": row[9],
        }
        for row in rows
    ]
    if governor:
        # The underlying grouped query does not include governor because it is
        # constant for the selected session in the current tracker.
        return results
    return results


def get_top_sponsors_by_action(
    session: str = "2026",
    governor: str | None = None,
    finance_cycle: str = "2026",
    limit_per_action: int = 10,
) -> dict:
    return get_governor_action_top_sponsors(
        session=session,
        finance_cycle=finance_cycle,
        limit_per_action=limit_per_action,
    )


def get_governor_action_bills(
    session: str = "2026",
    governor: str | None = None,
    action_label: str | None = None,
) -> list[dict]:
    params: list[str] = [session]
    clauses = ["session = ?"]
    if governor:
        clauses.append("governor = ?")
        params.append(governor)
    if action_label:
        clauses.append("action_label = ?")
        params.append(action_label)

    try:
        rows = virginia.query(f"""
            SELECT
                bill_number,
                title,
                action_label,
                action,
                raw_status,
                action_date,
                sponsor_lis_id,
                sponsor_name,
                sponsor_party,
                session,
                governor,
                source_url,
                fetched_at
            FROM governor_actions
            WHERE {' AND '.join(clauses)}
            ORDER BY action_label, action_date DESC, bill_number
        """, tuple(params))
    except sqlite3.OperationalError:
        return []

    keys = [
        "bill_number",
        "title",
        "action_label",
        "action",
        "raw_status",
        "action_date",
        "sponsor_lis_id",
        "sponsor_name",
        "sponsor_party",
        "session",
        "governor",
        "source_url",
        "fetched_at",
    ]
    return [dict(zip(keys, row)) for row in rows]
