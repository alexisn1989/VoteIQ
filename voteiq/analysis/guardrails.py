"""
guardrails.py
Post-generation checks applied to every Claude output before
it reaches the user. Two modes:
  report — strict, flags banned terms as violations
  chat   — lenient, flags as warnings only
"""
from __future__ import annotations
from dataclasses import dataclass, field

# ── CONSTANTS ─────────────────────────────────────────────────────────────────

BANNED_VERBS = [
    "bought", "controlled", "owned", "payoff", "corrupt",
    "quid pro quo", "bribery", "influence peddling", "bought off",
]

SAFE_VERBS = [
    "overlap", "alignment", "pattern", "concentration", "association",
]

FALLBACK_RESPONSE = (
    "VoteIQ was unable to generate a report at this time. "
    "The underlying data may be incomplete or unavailable. "
    "Please try again or contact errors@voteiq.io."
)

# ── RESULT ────────────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    text:       str
    violations: list[str] = field(default_factory=list)
    warnings:   list[str] = field(default_factory=list)


# ── CORE CHECKS ───────────────────────────────────────────────────────────────

def _check_banned_terms(text: str, mode: str) -> tuple[list, list]:
    violations, warnings = [], []
    lower = text.lower()
    for term in BANNED_VERBS:
        if term in lower:
            if mode == "report":
                violations.append(f"Banned term: '{term}'")
            else:
                warnings.append(f"Cautionary term in chat: '{term}'")
    return violations, warnings


def _check_data_claims(text: str, raw_data: dict, mode: str) -> list[str]:
    """
    Warn if the output makes claims about data categories
    that are absent from raw_data.

    raw_data expected keys (all optional):
      votes, bills, defections, sectors, geography, committees
    """
    warnings = []
    lower = text.lower()

    if "vote" in lower and not raw_data.get("votes"):
        warnings.append("Voting claims present but raw_data['votes'] is empty")

    if "bill" in lower and not raw_data.get("bills"):
        warnings.append("Bill claims present but raw_data['bills'] is empty")

    if "donor" in lower and not raw_data.get("sectors"):
        warnings.append("Donor claims present but raw_data['sectors'] is empty")

    if "defect" in lower and not raw_data.get("defections"):
        warnings.append("Defection claims present but raw_data['defections'] is empty")

    return warnings


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def run_guardrails(output: str,
                   config,
                   raw_data: dict,
                   mode: str = "report") -> GuardrailResult:
    """
    Run all guardrail checks on a Claude output string.

    mode:
      "report" — banned terms are violations (logged as ERROR)
      "chat"   — banned terms are warnings only (more lenient)

    raw_data keys used for claim validation:
      votes, bills, defections, sectors, geography, committees
    """
    if not output:
        return GuardrailResult(
            text=FALLBACK_RESPONSE,
            violations=["Empty output from model"],
        )

    all_violations: list[str] = []
    all_warnings:   list[str] = []

    v, w = _check_banned_terms(output, mode)
    all_violations.extend(v)
    all_warnings.extend(w)

    claim_warnings = _check_data_claims(output, raw_data, mode)
    all_warnings.extend(claim_warnings)

    return GuardrailResult(
        text=output,
        violations=all_violations,
        warnings=all_warnings,
    )


def safe_output(result: GuardrailResult) -> str:
    """
    Return the report text.
    Appends a flagging notice if guardrail violations were found.
    """
    if result.violations:
        return (
            result.text
            + "\n\n⚠ This report was automatically flagged for review. "
            "Contact errors@voteiq.io with questions."
        )
    return result.text


def flag_incomplete(data: list | dict | None, label: str) -> str | None:
    """Return a warning string if data is empty or None, else None."""
    if not data:
        return f"⚠ {label}: no data available — conclusions in this section are omitted."
    return None
