"""Governor action money analyst endpoints."""
from __future__ import annotations

import os
import sqlite3
import traceback

from fastapi import APIRouter, HTTPException, Query

from voteiq.queries.governor_actions import (
    get_governor_action_counts,
    get_governor_action_money_patterns,
    get_top_sponsors_by_action,
)
from voteiq.services.governor_actions import (
    build_governor_action_money_analysis,
    list_governor_action_bills,
)

router = APIRouter(tags=["governor"])


@router.get("/api/governor/actions/summary")
def governor_action_summary(
    session: str = Query(default="2026"),
    finance_cycle: str = Query(default="2026"),
    governor: str = Query(default="Spanberger"),
    limit_per_action: int = Query(default=10, ge=1, le=50),
):
    """Return structured governor-action counts, money patterns, and top sponsors."""
    return {
        "status": "success",
        "session": session,
        "finance_cycle": finance_cycle,
        "governor": governor,
        "action_counts": get_governor_action_counts(
            session=session,
            governor=governor,
        ),
        "money_patterns": get_governor_action_money_patterns(
            session=session,
            finance_cycle=finance_cycle,
            governor=governor,
        ),
        "top_sponsors_by_action": get_top_sponsors_by_action(
            session=session,
            governor=governor,
            finance_cycle=finance_cycle,
            limit_per_action=limit_per_action,
        ),
    }


@router.get("/api/governor/actions/bills")
def governor_action_bills(
    session: str = Query(default="2026"),
    governor: str = Query(default="Spanberger"),
    action_label: str | None = Query(default=None),
):
    """List governor-action bills, optionally filtered by standardized action label."""
    return list_governor_action_bills(
        session=session,
        governor=governor,
        action_label=action_label,
    )


@router.get("/api/governor/db-check")
def governor_db_check():
    """Diagnose production DB state for governor_actions and governor_executive_orders."""
    data_dir = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(data_dir, "polls.db")
    result: dict = {
        "db_path": db_path,
        "db_exists": os.path.exists(db_path),
        "tables": [],
        "governor_actions": {},
        "governor_executive_orders": {},
        "signed_sample": [],
        "schema_columns": [],
        "error": None,
    }
    if not result["db_exists"]:
        return result
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        result["tables"] = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]

        if "governor_actions" in result["tables"]:
            result["schema_columns"] = [
                r[1] for r in conn.execute(
                    "PRAGMA table_info(governor_actions)"
                ).fetchall()
            ]
            result["governor_actions"] = {
                row["action"]: row["cnt"]
                for row in conn.execute("""
                    SELECT action,
                           COUNT(*) AS cnt
                    FROM governor_actions
                    WHERE lower(governor) LIKE '%spanberger%'
                    GROUP BY action
                    ORDER BY cnt DESC
                """).fetchall()
            }
            result["governor_actions"]["_distinct_governors"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT governor FROM governor_actions LIMIT 10"
                ).fetchall()
            ]
            result["governor_actions"]["_distinct_sessions"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT session FROM governor_actions ORDER BY session DESC LIMIT 5"
                ).fetchall()
            ]
            # Try the exact signed query used in the chat overview
            try:
                rows = conn.execute("""
                    SELECT bill_number, title, action_date, chapter_number, source_url
                    FROM governor_actions
                    WHERE session = '2026'
                      AND lower(governor) LIKE '%spanberger%'
                      AND action = 'signed'
                    ORDER BY action_date DESC, bill_number
                    LIMIT 5
                """).fetchall()
                result["signed_sample"] = [dict(r) for r in rows]
                result["signed_query"] = "OK"
            except Exception as e:
                result["signed_query"] = f"ERROR: {e}"
                # Try without chapter_number
                try:
                    rows = conn.execute("""
                        SELECT bill_number, title, action_date, NULL AS chapter_number, source_url
                        FROM governor_actions
                        WHERE session = '2026'
                          AND lower(governor) LIKE '%spanberger%'
                          AND action = 'signed'
                        ORDER BY action_date DESC, bill_number
                        LIMIT 5
                    """).fetchall()
                    result["signed_sample"] = [dict(r) for r in rows]
                    result["signed_query_fallback"] = "OK"
                except Exception as e2:
                    result["signed_query_fallback"] = f"ERROR: {e2}"

        if "governor_executive_orders" in result["tables"]:
            eo_count = conn.execute(
                "SELECT COUNT(*) FROM governor_executive_orders"
            ).fetchone()[0]
            result["governor_executive_orders"] = {
                "total_rows": eo_count,
                "sample": [
                    dict(r) for r in conn.execute("""
                        SELECT order_number, title, governor, signed_date
                        FROM governor_executive_orders
                        ORDER BY signed_date DESC
                        LIMIT 3
                    """).fetchall()
                ],
            }
        else:
            result["governor_executive_orders"] = {"total_rows": 0, "sample": []}

        conn.close()
    except Exception:
        result["error"] = traceback.format_exc()

    return result


@router.post("/api/governor/actions/money-analysis")
def governor_action_money_analysis(
    session: str = Query(default="2026"),
    finance_cycle: str = Query(default="2026"),
    governor: str = Query(default="Spanberger"),
    voice: str = Query(default="pro"),
):
    """Build the Governor Action Money Analyst payload and Claude narrative."""
    try:
        return build_governor_action_money_analysis(
            session=session,
            finance_cycle=finance_cycle,
            governor=governor,
            voice=voice,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
