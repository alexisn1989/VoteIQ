"""Governor action money analyst endpoints."""
from __future__ import annotations

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
