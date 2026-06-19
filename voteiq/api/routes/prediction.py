"""
Vote prediction endpoint.

POST /api/predict-vote
  — Accepts a legislator name, policy area, and optional bill info.
  — Assembles pre-computed signals (vote history, donor timing, CoI score,
    utility funding gap) then calls Claude for a structured verdict.

Latency note: donor_timing query runs ~0.3s with indexes in place.
Full signal assembly + Claude inference is typically 3–7s.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["prediction"])

_BASE_DIR = Path(__file__).resolve().parents[3]
_DATA_DIR = os.getenv("DATA_DIR", str(_BASE_DIR))
_POLLS_DB = os.path.join(_DATA_DIR, "polls.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_POLLS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── Request / response models ─────────────────────────────────────────────────

class PredictVoteRequest(BaseModel):
    canonical_name: str = Field(
        ...,
        description="Legislator canonical name — must match va_name_index.canonical_name exactly",
        examples=["Aaron Rouse"],
    )
    policy_area: str = Field(
        ...,
        description=(
            "One of: EDUCATION, HEALTHCARE, TAXATION, ENVIRONMENT, CRIMINAL_JUSTICE, "
            "ELECTIONS, TRANSPORTATION, ENERGY, HOUSING, LABOR, SOCIAL_SERVICES, "
            "AGRICULTURE, BUDGET, OTHER"
        ),
        examples=["ENERGY"],
    )
    bill_number: Optional[str] = Field(
        None,
        description="Bill number (e.g. HB84) — enables Dominion utility funding gap lookup",
        examples=["HB84"],
    )
    session: Optional[str] = Field(
        None,
        description="Legislative session (e.g. 2024)",
        examples=["2024"],
    )
    bill_title: Optional[str] = Field(
        None,
        description="Human-readable bill title for LLM context",
    )
    bill_description: Optional[str] = Field(
        None,
        description="Short bill description for LLM context",
    )


class SignalQuality(BaseModel):
    confidence_level: str
    confidence_elevated: bool
    counter_signal_active: bool


class PredictVoteResponse(BaseModel):
    verdict: str
    confidence: str
    reasoning: str
    data_gaps: list[str]
    counter_signal_warning: Optional[str]
    signal_quality: SignalQuality


# ── Endpoint ──────────────────────────────────────────────────────────────────

VALID_POLICY_AREAS = {
    "EDUCATION", "HEALTHCARE", "TAXATION", "ENVIRONMENT", "CRIMINAL_JUSTICE",
    "ELECTIONS", "TRANSPORTATION", "ENERGY", "HOUSING", "LABOR",
    "SOCIAL_SERVICES", "AGRICULTURE", "BUDGET", "OTHER",
}


@router.post("/api/predict-vote", response_model=PredictVoteResponse)
def predict_vote_endpoint(req: PredictVoteRequest) -> PredictVoteResponse:
    """
    Predict how a Virginia legislator will vote on a bill in a given policy area.

    Signal sources (all pre-computed from polls.db):
    - vote_consistency     — historical yes/no rates on policy-area bills
    - donor_concentration  — top employer/industry donors
    - donor_timing         — days between contributions and policy-area votes
    - policy_area_outcomes — base-rate pass/fail statistics
    - coi_score            — conflict-of-interest alignment score (when available)
    - utility_funding_gap  — Dominion funding gap for tracked energy bills (when applicable)

    Returns a structured verdict with reasoning and any active counter-signal warning.
    """
    if req.policy_area not in VALID_POLICY_AREAS:
        raise HTTPException(
            status_code=422,
            detail=f"policy_area must be one of: {sorted(VALID_POLICY_AREAS)}",
        )

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Prediction service unavailable: ANTHROPIC_API_KEY not configured",
        )

    from app.prediction.signal_builder import build_signals
    from app.prediction.predictor import predict_vote

    conn = _conn()
    try:
        # Verify legislator exists in name index before running expensive queries
        row = conn.execute(
            "SELECT canonical_name FROM va_name_index WHERE canonical_name = ?",
            (req.canonical_name,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Legislator {req.canonical_name!r} not found in va_name_index. "
                       "Use exact canonical name from the legislators table.",
            )

        signals = build_signals(
            db=conn,
            canonical_name=req.canonical_name,
            policy_area=req.policy_area,
            bill_number=req.bill_number,
            session=req.session,
        )
    finally:
        conn.close()

    result = predict_vote(
        signals=signals,
        bill_title=req.bill_title or "",
        bill_description=req.bill_description or "",
    )

    return PredictVoteResponse(
        verdict=result.get("verdict", "INSUFFICIENT_DATA"),
        confidence=result.get("confidence", "low"),
        reasoning=result.get("reasoning", ""),
        data_gaps=result.get("data_gaps", []),
        counter_signal_warning=result.get("counter_signal_warning"),
        signal_quality=SignalQuality(
            confidence_level=signals["confidence_level"],
            confidence_elevated=signals["confidence_elevated"],
            counter_signal_active=signals["counter_signal_active"],
        ),
    )
