"""
app/prediction/predictor.py

Runs Claude inference over pre-assembled signals from signal_builder.build_signals().

Model routing:
  claude-haiku-4-5-20251001  — default (signals are pre-computed; reasoning is bounded)
  claude-sonnet-4-6          — escalated when counter_signal_active=True (conflicting
                               evidence requires higher-quality synthesis)

Input:  signals dict from build_signals() + optional bill metadata
Output: {
    verdict:               "YES"|"NO"|"LIKELY_YES"|"LIKELY_NO"|"INSUFFICIENT_DATA"
    confidence:            "high"|"medium"|"low"
    reasoning:             str  (3-5 sentences citing specific signal values)
    data_gaps:             list[str]
    counter_signal_warning: str | None  (verbatim counter_signal_note when active)
}
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Models ────────────────────────────────────────────────────────────────────
_HAIKU  = os.getenv("CLAUDE_HAIKU_MODEL",  "claude-haiku-4-5-20251001")
_SONNET = os.getenv("ANTHROPIC_MODEL",     "claude-sonnet-4-6")

_DEFAULT_MAX_TOKENS = 600


# ── System prompt (fixed; Anthropic prompt cache will hold this) ─────────────

PREDICTION_SYSTEM_PROMPT = """\
You are a nonpartisan legislative analyst for VoteIQ, a civic intelligence platform \
tracking the Virginia General Assembly.

TASK: Predict how the named legislator will vote on the described bill, using only \
the pre-computed signals supplied in the user message.

HARD RULES — follow exactly:
1. Reason only from the provided signals. Do NOT use external knowledge of the \
   legislator's public statements, biography, or known positions.
2. If a signal has sufficient_data=false, treat it as a data gap. Sparse data is \
   not evidence of a pattern.
3. "contribution_count" in donor timing means contribution-vote PAIRS (not unique \
   contributions). Do not overstate its precision.
4. If counter_signal_warning is present, it is a HARD INSTRUCTION. Reproduce it \
   verbatim in your reasoning field and weight it heavily in your verdict.
5. Verdict must be exactly one of: YES, NO, LIKELY_YES, LIKELY_NO, INSUFFICIENT_DATA.
   Use INSUFFICIENT_DATA only when confidence_level="low" AND no CoI score exists.
6. Output ONLY valid JSON — no markdown fences, no prose outside the JSON object.

OUTPUT SCHEMA (strict):
{
  "verdict":               "YES|NO|LIKELY_YES|LIKELY_NO|INSUFFICIENT_DATA",
  "confidence":            "high|medium|low",
  "reasoning":             "<3-5 sentences citing specific numeric signal values>",
  "data_gaps":             ["<signals that were absent or had sufficient_data=false>"],
  "counter_signal_warning": "<verbatim counter_signal_note, or null>"
}
"""


# ── Signal serialiser ─────────────────────────────────────────────────────────

def _fmt_vote_consistency(vc: dict | None) -> str:
    if not vc:
        return "vote_consistency: NOT AVAILABLE"
    return (
        f"vote_consistency:\n"
        f"  total_votes={vc['total_votes']}  yes_rate={vc['yes_rate']}  "
        f"party_loyalty_rate={vc['party_loyalty_rate']}  flip_rate={vc['flip_rate']}\n"
        f"  sufficient_data={vc['sufficient_data']}  "
        f"classification_source={vc['classification_source']!r}"
    )


def _fmt_donor_concentration(dc: list[dict]) -> str:
    if not dc:
        return "donor_concentration: NO DATA"
    lines = ["donor_concentration (top donors by employer):"]
    for row in dc:
        pct = round(row["pct_of_total"] * 100, 1)
        lines.append(
            f"  {row['donor_industry']!r:35s}  "
            f"${row['total_amount']:>12,.0f}  ({pct}% of top-5 total)"
        )
    return "\n".join(lines)


def _fmt_donor_timing(dt: dict | None) -> str:
    if not dt:
        return "donor_timing: NOT AVAILABLE"
    return (
        f"donor_timing:\n"
        f"  avg_lead_days={dt['avg_lead_days']}  "
        f"contribution_vote_pairs={dt['contribution_count']}\n"
        f"  money_precedes_votes={dt['money_precedes_votes']}  "
        f"sufficient_data={dt['sufficient_data']}"
    )


def _fmt_policy_outcomes(po: dict | None) -> str:
    if not po:
        return "policy_area_outcomes: NOT AVAILABLE"
    return (
        f"policy_area_outcomes:\n"
        f"  total_bills={po['total_bills']}  pass_rate={po['pass_rate']}  "
        f"avg_yes_votes={po['avg_yes_votes']}\n"
        f"  partisan_split={po['partisan_split']}  "
        f"classification_source={po['classification_source']!r}"
    )


def _fmt_coi(coi: dict | None) -> str:
    if not coi:
        return "coi_score: NOT IN DONOR ALIGNMENT TABLE (no CoI data)"
    return (
        f"coi_score:\n"
        f"  score={coi['coi_score']}/100  top_industry={coi['top_donor_industry']!r}\n"
        f"  top_amount=${coi['top_donor_amount']:,.0f}  "
        f"yes_rate_in_top_industry={coi['yes_rate_in_top_industry']}\n"
        f"  is_counter_signal={coi['is_counter_signal']}  "
        f"party_median_gap={coi['party_median_gap']}"
    )


def _fmt_utility_gap(gap: dict | None) -> str:
    if not gap:
        return "utility_funding_gap: bill not in Dominion-tracked set"
    return (
        f"utility_funding_gap:\n"
        f"  bill={gap['bill_id']!r}  ratio={gap['ratio']}x  "
        f"(YES voters received {gap['ratio']}x more Dominion funding than NO voters)\n"
        f"  avg_yes_dom=${gap['avg_yes_dom']:,.0f}  avg_no_dom=${gap['avg_no_dom']:,.0f}\n"
        f"  description={gap['description']!r}"
    )


def _build_user_message(
    signals: dict,
    bill_title: str,
    bill_description: str,
) -> str:
    legislator   = signals["legislator"]
    policy_area  = signals["policy_area"]
    bill_number  = signals.get("bill_number") or "unspecified"
    session      = signals.get("session") or "unspecified"
    conf_level   = signals.get("confidence_level", "low")
    conf_elevated = signals.get("confidence_elevated", False)
    counter_note  = signals.get("counter_signal_note")

    parts: list[str] = [
        f"LEGISLATOR: {legislator}",
        f"BILL: {bill_number} ({session})  POLICY AREA: {policy_area}",
    ]
    if bill_title:
        parts.append(f"BILL TITLE: {bill_title}")
    if bill_description:
        parts.append(f"BILL DESCRIPTION: {bill_description}")

    parts += [
        f"\nOVERALL SIGNAL QUALITY: confidence_level={conf_level!r}  "
        f"confidence_elevated_by_coi={conf_elevated}",
        "",
        _fmt_vote_consistency(signals.get("vote_consistency")),
        "",
        _fmt_donor_concentration(signals.get("donor_concentration") or []),
        "",
        _fmt_donor_timing(signals.get("donor_timing")),
        "",
        _fmt_policy_outcomes(signals.get("policy_area_outcomes")),
        "",
        _fmt_coi(signals.get("coi_score")),
        "",
        _fmt_utility_gap(signals.get("utility_funding_gap")),
    ]

    # Counter-signal hard instruction — must appear last so the model sees it
    # immediately before generating its response.
    if counter_note:
        parts += [
            "",
            "*** HARD INSTRUCTION — DO NOT SOFTEN OR OMIT ***",
            counter_note,
        ]

    return "\n".join(parts)


# ── Main entry point ──────────────────────────────────────────────────────────

def predict_vote(
    signals: dict,
    bill_title: str = "",
    bill_description: str = "",
) -> dict[str, Any]:
    """
    Run Claude inference over pre-assembled signals.

    Args:
        signals:          Output of signal_builder.build_signals()
        bill_title:       Human-readable bill title (optional, improves context)
        bill_description: Short bill description (optional)

    Returns:
        {verdict, confidence, reasoning, data_gaps, counter_signal_warning}
    """
    counter_active = signals.get("counter_signal_active", False)
    model = _SONNET if counter_active else _HAIKU

    user_message = _build_user_message(signals, bill_title, bill_description)

    logger.info(
        "predict_vote: legislator=%r bill=%r model=%s counter=%s",
        signals.get("legislator"), signals.get("bill_number"), model, counter_active,
    )

    try:
        from voteiq.api.claude import cached_system_prompt, get_claude_client
        client = get_claude_client()
        response = client.messages.create(
            model=model,
            max_tokens=_DEFAULT_MAX_TOKENS,
            system=cached_system_prompt(PREDICTION_SYSTEM_PROMPT),
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text
    except Exception as exc:
        logger.error("predict_vote: Claude call failed: %s", exc)
        return _fallback_response(signals, str(exc))

    return _parse_response(raw, signals)


def _parse_response(raw: str, signals: dict) -> dict[str, Any]:
    """Parse Claude's JSON response, falling back gracefully on malformed output."""
    text = raw.strip()
    # Strip markdown fences if the model included them despite instructions
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("predict_vote: non-JSON response from Claude, returning raw")
        return {
            "verdict":               "INSUFFICIENT_DATA",
            "confidence":            signals.get("confidence_level", "low"),
            "reasoning":             raw[:500],
            "data_gaps":             ["Claude returned non-JSON response"],
            "counter_signal_warning": signals.get("counter_signal_note"),
        }

    # Normalise keys that might be missing
    parsed.setdefault("data_gaps", [])
    parsed.setdefault("counter_signal_warning", signals.get("counter_signal_note"))

    valid_verdicts = {"YES", "NO", "LIKELY_YES", "LIKELY_NO", "INSUFFICIENT_DATA"}
    if parsed.get("verdict") not in valid_verdicts:
        parsed["verdict"] = "INSUFFICIENT_DATA"
        parsed.setdefault("data_gaps", []).append("verdict_parse_error")

    return parsed


def _fallback_response(signals: dict, error: str) -> dict[str, Any]:
    return {
        "verdict":               "INSUFFICIENT_DATA",
        "confidence":            "low",
        "reasoning":             f"Prediction service unavailable: {error}",
        "data_gaps":             ["api_error"],
        "counter_signal_warning": signals.get("counter_signal_note"),
    }
