"""VoteIQ FastAPI application.

This is intentionally small: application setup belongs here, while routes live
under ``voteiq.api.routes`` and business logic moves into ``voteiq.services``.
"""
from __future__ import annotations

from pathlib import Path
import threading
import re

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from voteiq.api.router import api_router
from voteiq.api.routes.campaign_finance_integration import CampaignFinanceService, format_campaign_finance_response

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="VoteIQ")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(api_router)

# Initialize campaign finance service
_campaign_finance_service = CampaignFinanceService()


# ── Campaign Finance Context Functions ────────────────────────────────────────

def _fetch_spanberger_finance_context(user_query: str) -> str:
    """Fetch campaign finance context for Governor Spanberger if query mentions fundraising."""
    if not user_query:
        return ""

    query_lower = user_query.lower()
    finance_keywords = ["funding", "donor", "campaign", "fundrais", "contribut", "money", "pac", "raised"]
    spanberger_keywords = ["spanberger", "governor"]

    # Check if query mentions both finance and Spanberger
    has_finance = any(kw in query_lower for kw in finance_keywords)
    has_spanberger = any(kw in query_lower for kw in spanberger_keywords)

    if not (has_finance and has_spanberger):
        return ""

    try:
        data = _campaign_finance_service.get_campaign_finance("Abigail Spanberger")
        if data:
            # Get veto-donor correlation and governor actions
            correlation = _campaign_finance_service.get_veto_donor_correlation("Abigail Spanberger")
            gov_actions = _campaign_finance_service.get_governor_actions_summary("Spanberger")
            return format_campaign_finance_response(data, correlation=correlation, gov_actions=gov_actions)
    except Exception as e:
        print(f"Error fetching Spanberger campaign finance: {e}")

    return ""


def _fetch_state_campaign_finance_context(user_query: str, basic: bool = True) -> str:
    """Fetch campaign finance context for Virginia state officials if query mentions fundraising."""
    if not user_query:
        return ""

    query_lower = user_query.lower()
    finance_keywords = ["funding", "donor", "campaign", "fundrais", "contribut", "money", "pac", "raised", "sector"]

    # Check if query mentions finance
    has_finance = any(kw in query_lower for kw in finance_keywords)

    if not has_finance:
        return ""

    # Try to extract candidate name from query
    candidate_name = None

    # Simple pattern matching for candidate names
    virginia_officials = [
        "spanberger", "hashmi", "jay jones", "hanger", "vogel", "stanley", "deeds",
        "kiggans", "hope", "mcclure", "lopez", "helmer", "bulova"
    ]

    query_parts = query_lower.split()
    for official in virginia_officials:
        if official in query_lower:
            candidate_name = official.title()
            break

    if not candidate_name:
        return ""

    try:
        found_name = _campaign_finance_service.search_candidate(candidate_name)
        if found_name:
            data = _campaign_finance_service.get_campaign_finance(found_name)
            if data:
                # Get veto-donor correlation and governor actions if available
                correlation = _campaign_finance_service.get_veto_donor_correlation(found_name)
                gov_actions = _campaign_finance_service.get_governor_actions_summary(found_name)
                return format_campaign_finance_response(data, correlation=correlation, gov_actions=gov_actions)
    except Exception as e:
        print(f"Error fetching campaign finance for {candidate_name}: {e}")

    return ""


@app.on_event("startup")
async def _startup_governor_actions() -> None:
    def _task() -> None:
        try:
            import ingest_governor_actions

            n = ingest_governor_actions.run(sessions=["2025", "2026"])
            print(f"Governor actions: ensured {n} records.")
        except Exception as exc:
            print(f"Governor actions ingest skipped: {exc}")

    threading.Thread(target=_task, daemon=True).start()
