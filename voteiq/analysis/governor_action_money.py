"""
Governor-action donor-sector analysis.

These helpers describe public-record overlaps between donor sectors and
bill outcomes. They do not infer motive, reward, pressure, influence, or
causation.
"""
from __future__ import annotations

import sqlite3

from voteiq.db import virginia


def get_governor_action_sector_totals(
    cycle: str = "2026",
    session: str = "2026",
) -> list:
    try:
        return virginia.query("""
            SELECT
                ga.action_label,
                dst.sector,
                SUM(dst.total_amount) AS total_amount,
                SUM(dst.donor_count) AS donor_count
            FROM governor_actions ga
            LEFT JOIN donor_sector_totals dst
                ON ga.sponsor_lis_id = dst.legislator_id
                AND dst.cycle = ?
            WHERE ga.session = ?
            GROUP BY ga.action_label, dst.sector
            ORDER BY ga.action_label, total_amount DESC
        """, (cycle, session))
    except sqlite3.OperationalError:
        return []


def get_governor_action_money_patterns(session: str, finance_cycle: str) -> list:
    """Return money patterns per governor action type."""
    try:
        return virginia.query("""
            SELECT
                ga.action_label,
                ga.action,
                ga.raw_status,
                ga.sponsor_lis_id,
                ga.sponsor_name,
                ga.sponsor_party,
                dst.sector,
                SUM(dst.total_amount) AS total_amount,
                SUM(dst.donor_count) AS donor_count,
                ROUND(SUM(dst.total_amount)/NULLIF(SUM(dst.donor_count),0), 2) AS avg_donation
            FROM governor_actions ga
            LEFT JOIN donor_sector_totals dst
                ON ga.sponsor_lis_id = dst.legislator_id
                AND dst.cycle = ?
            WHERE ga.session = ?
            GROUP BY ga.action_label, ga.action, ga.raw_status, ga.sponsor_lis_id, ga.sponsor_name, ga.sponsor_party, dst.sector
            ORDER BY ga.action_label, total_amount DESC
        """, (finance_cycle, session))
    except sqlite3.OperationalError:
        return []


def get_governor_action_top_sponsors(
    session: str,
    finance_cycle: str,
    limit_per_action: int = 10,
) -> dict:
    """Return top sponsors by governor action, with each sponsor's leading donor sector."""
    try:
        rows = virginia.query("""
            WITH sponsor_action_counts AS (
                SELECT
                    ga.action,
                    ga.action_label,
                    ga.sponsor_lis_id,
                    ga.sponsor_name,
                    ga.sponsor_party,
                    COUNT(*) AS bill_count
                FROM governor_actions ga
                WHERE ga.session = ?
                  AND ga.sponsor_lis_id IS NOT NULL
                GROUP BY
                    ga.action,
                    ga.action_label,
                    ga.sponsor_lis_id,
                    ga.sponsor_name,
                    ga.sponsor_party
            ),
            sponsor_top_sector AS (
                SELECT
                    dst.legislator_id,
                    dst.sector,
                    dst.total_amount,
                    ROW_NUMBER() OVER (
                        PARTITION BY dst.legislator_id
                        ORDER BY dst.total_amount DESC
                    ) AS rn
                FROM donor_sector_totals dst
                WHERE dst.cycle = ?
            ),
            ranked AS (
                SELECT
                    sac.action,
                    sac.action_label,
                    sac.sponsor_lis_id,
                    sac.sponsor_name,
                    sac.sponsor_party,
                    COALESCE(sts.sector, 'Unknown') AS sector,
                    sac.bill_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY sac.action
                        ORDER BY sac.bill_count DESC, sac.sponsor_name
                    ) AS action_rank
                FROM sponsor_action_counts sac
                LEFT JOIN sponsor_top_sector sts
                    ON sac.sponsor_lis_id = sts.legislator_id
                    AND sts.rn = 1
            )
            SELECT
                action,
                action_label,
                sponsor_lis_id,
                sponsor_name,
                sponsor_party,
                sector,
                bill_count
            FROM ranked
            WHERE action_rank <= ?
            ORDER BY action_label, bill_count DESC, sponsor_name
        """, (session, finance_cycle, limit_per_action))
    except sqlite3.OperationalError:
        return {}

    sections = {}
    for (
        action,
        action_label,
        sponsor_lis_id,
        sponsor_name,
        sponsor_party,
        sector,
        bill_count,
    ) in rows:
        sections.setdefault(action, {
            "action_label": action_label,
            "sponsors": [],
        })
        sections[action]["sponsors"].append({
            "sponsor_lis_id": sponsor_lis_id,
            "sponsor_name": sponsor_name,
            "sponsor_party": sponsor_party,
            "sector": sector,
            "bill_count": bill_count,
        })

    return sections


def get_governor_action_voteiq_finding(
    session: str = "2026",
    finance_cycle: str = "2026",
) -> str:
    """Return a one-sentence VoteIQ finding about governor-action donor sectors."""
    rows = get_governor_action_sector_totals(
        cycle=finance_cycle,
        session=session,
    )

    sectors_by_action: dict[str, list[tuple[str, float]]] = {}
    for action_label, sector, total_amount, _donor_count in rows:
        if not action_label or not sector or total_amount is None:
            continue
        sectors_by_action.setdefault(action_label, []).append((sector, float(total_amount or 0)))

    def top_sectors(label: str, n: int = 2) -> str:
        sectors = sorted(
            sectors_by_action.get(label, []),
            key=lambda item: item[1],
            reverse=True,
        )[:n]
        if not sectors:
            return "no clearly dominant sectors in the current data"
        return " and ".join(sector for sector, _ in sectors)

    signed = top_sectors("Signed into law")
    vetoed = top_sectors("Vetoed")
    pocket = top_sectors("Pocket veto")
    amended = top_sectors("Amended / returned")
    overridden = top_sectors("Veto overridden")
    sustained = top_sectors("Veto sustained")
    pending = top_sectors("Pending governor action")
    cycle_note = (
        f" Finance cycle {finance_cycle} is compared with legislative session {session}."
        if str(finance_cycle) != str(session)
        else ""
    )

    return (
        "Public records show signed bills were most commonly sponsored by legislators "
        f"whose donor portfolios were concentrated in {signed}; vetoed bills showed "
        f"sector patterns around {vetoed}; pocket veto bills showed {pocket}; amended "
        f"or returned bills showed {amended}; overridden vetoes showed {overridden}; "
        f"sustained vetoes showed {sustained}; and pending actions showed {pending}."
        f"{cycle_note} These are donor-sector and bill-outcome overlaps only; they do "
        "not show motive, reward, pressure, influence, or causation."
    )


def get_governor_action_analysis_guardrails(
    session: str = "2026",
    finance_cycle: str = "2026",
) -> list[str]:
    """Return wording rules for governor-action donor-sector analysis."""
    rules = [
        "Do not infer motive, reward, pressure, influence, or causation.",
        "Only describe public-record overlaps between donor sectors and bill outcomes.",
        "Separate pocket veto from normal veto.",
        "Include signed, veto, pocket veto, amended, overridden, sustained, and pending categories.",
        "Highlight donor concentration and sector trends.",
        "State that governor action counts are based on current bill-status records.",
        "Note that pocket veto and pending action counts require action-history data and may not appear in latest-status snapshots.",
    ]
    if str(finance_cycle) != str(session):
        rules.append(
            f"Note that finance cycle {finance_cycle} is being compared with legislative session {session}."
        )
    return rules


def build_governor_action_money_payload(
    *,
    action_counts: list,
    money_rows: list,
    sponsor_rows,
    session: str,
    finance_cycle: str,
    governor: str,
) -> dict:
    """Build the structured payload used by the Governor Action Money Analyst."""
    formatted_money_rows = [
        format_governor_action_money_row(row)
        for row in money_rows
    ]

    return {
        "session": session,
        "finance_cycle": finance_cycle,
        "governor": governor,
        "summary_source": "current bill-status records",
        "data_limit_note": (
            "Governor Action Summary is based on current bill-status records. "
            "Pocket veto and pending action counts require action-history data "
            "and may not appear in latest-status snapshots."
        ),
        "finance_cycle_matches_session": str(finance_cycle) == str(session),
        "action_counts": action_counts,
        "money_patterns": money_rows,
        "formatted_money_patterns": formatted_money_rows,
        "top_sponsors_by_action": sponsor_rows,
        "guardrails": get_governor_action_analysis_guardrails(
            session=session,
            finance_cycle=finance_cycle,
        ),
        "required_note": "Correlation does not imply causation.",
    }


def format_governor_action_money_row(row: dict | tuple) -> dict:
    """Return a UI-friendly governor-action money pattern row."""
    if isinstance(row, dict):
        action_label = row.get("action_label")
        action = row.get("action")
        raw_status = row.get("raw_status")
        sponsor_name = row.get("sponsor_name")
        sector = row.get("sector")
        total_amount = row.get("total_amount")
        donor_count = row.get("donor_count")
        avg_donation = row.get("avg_donation")
    else:
        action_label = row[0] if len(row) > 0 else None
        action = row[1] if len(row) > 1 else None
        raw_status = row[2] if len(row) > 2 else None
        sponsor_name = row[4] if len(row) > 4 else None
        sector = row[6] if len(row) > 6 else None
        total_amount = row[7] if len(row) > 7 else None
        donor_count = row[8] if len(row) > 8 else None
        avg_donation = row[9] if len(row) > 9 else None

    total_amount = float(total_amount or 0)
    donor_count = int(donor_count or 0)
    avg_donation = float(avg_donation or 0)

    return {
        "action_label": action_label,
        "action": action,
        "raw_status": raw_status,
        "sponsor_name": sponsor_name,
        "sector": sector,
        "total_amount": total_amount,
        "donor_count": donor_count,
        "avg_donation": avg_donation,
        "display_lines": [
            action_label or "",
            raw_status or "",
            sponsor_name or "",
            sector or "",
            f"${total_amount:,.0f}",
            f"{donor_count:,} donor{'s' if donor_count != 1 else ''}",
            f"${avg_donation:,.2f} avg",
        ],
    }
