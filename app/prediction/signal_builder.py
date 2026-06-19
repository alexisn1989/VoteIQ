"""
app/prediction/signal_builder.py

Assembles all feature signals for a single (legislator, policy_area, bill) request.
Called by predictor.py before the Claude inference call.

Confidence elevation rule:
  If coi_score is present (legislator is in donor_vote_alignment),
  donor signals are treated as corroborated even when donor_timing.sufficient_data=False.

Counter-signal rule:
  If coi_score.is_counter_signal=True, counter_signal_note is a HARD INSTRUCTION
  that must be verbatim in the LLM prompt — never softened or omitted.
"""
from __future__ import annotations

import logging
import sqlite3

from app.prediction.feature_queries import (
    get_coi_score,
    get_donor_concentration,
    get_donor_timing,
    get_policy_area_outcomes,
    get_utility_funding_gap,
    get_vote_consistency,
)

logger = logging.getLogger(__name__)


def _compute_confidence(
    vote_c: dict | None,
    donor_timing: dict | None,
    coi: dict | None,
) -> str:
    """
    high   — vote data sufficient AND (donor timing sufficient OR coi_score present)
    medium — one of the above conditions met
    low    — neither vote data nor donor signal is reliable
    """
    has_votes  = bool(vote_c and vote_c.get("sufficient_data"))
    has_timing = bool(donor_timing and donor_timing.get("sufficient_data"))
    has_coi    = coi is not None

    # CoI score from donor_vote_alignment corroborates donor signal even when
    # contribution-vote pair count is below the MIN_CONTRIBUTION_COUNT threshold.
    donor_signal_valid = has_timing or has_coi

    if has_votes and donor_signal_valid:
        return "high"
    if has_votes or donor_signal_valid:
        return "medium"
    return "low"


def build_signals(
    db: sqlite3.Connection,
    canonical_name: str,
    policy_area: str,
    bill_number: str | None = None,
    session: str | None = None,
) -> dict:
    """
    Fetch and assemble all prediction signals for one legislator + bill request.

    Returns a dict consumed by predictor.py:
      legislator            str
      policy_area           str
      bill_number           str | None
      session               str | None
      vote_consistency      dict | None
      donor_concentration   list[dict]
      donor_timing          dict | None
      policy_area_outcomes  dict
      coi_score             dict | None
      utility_funding_gap   dict | None   (populated only for energy/utility bills)
      confidence_level      str           ("high" | "medium" | "low")
      confidence_elevated   bool          (True when CoI raised confidence above donor_timing alone)
      counter_signal_active bool
      counter_signal_note   str | None    (HARD LLM instruction — never omit when True)
    """
    logger.info(
        "build_signals: legislator=%r policy=%r bill=%r session=%r",
        canonical_name, policy_area, bill_number, session,
    )

    # ── 1. Fetch all signals ─────────────────────────────────────────────────
    vote_c    = get_vote_consistency(db, canonical_name, policy_area)
    donor_con = get_donor_concentration(db, canonical_name, policy_area)
    timing    = get_donor_timing(db, canonical_name, policy_area)
    outcomes  = get_policy_area_outcomes(db, policy_area)
    coi       = get_coi_score(db, canonical_name)
    gap       = (
        get_utility_funding_gap(db, canonical_name, bill_number, session)
        if bill_number and session
        else None
    )

    # ── 2. Confidence elevation ──────────────────────────────────────────────
    has_timing_sufficient = bool(timing and timing.get("sufficient_data"))
    has_coi               = coi is not None
    confidence_elevated   = has_coi and not has_timing_sufficient

    confidence = _compute_confidence(vote_c, timing, coi)

    # ── 3. Counter-signal propagation ───────────────────────────────────────
    counter_active = bool(coi and coi.get("is_counter_signal"))
    counter_note   = coi.get("counter_signal_note") if coi else None

    logger.info(
        "build_signals: confidence=%r elevated=%s counter=%s",
        confidence, confidence_elevated, counter_active,
    )

    return {
        "legislator":           canonical_name,
        "policy_area":          policy_area,
        "bill_number":          bill_number,
        "session":              session,
        "vote_consistency":     vote_c,
        "donor_concentration":  donor_con,
        "donor_timing":         timing,
        "policy_area_outcomes": outcomes,
        "coi_score":            coi,
        "utility_funding_gap":  gap,
        "confidence_level":     confidence,
        "confidence_elevated":  confidence_elevated,
        "counter_signal_active": counter_active,
        "counter_signal_note":   counter_note,
    }
