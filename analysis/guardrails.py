# analysis/guardrails.py
# Scans every Claude output before it reaches the user.
# Catches forbidden language, ensures required phrases,
# and flags data quality issues automatically.
# Legal protection layer for VoteIQ.

import re
import logging
from dataclasses import dataclass, field
from config.finance_rules import (
    FORBIDDEN_TERMS,
    REQUIRED_PHRASES,
    get_disclaimer,
    LevelConfig,
)

logger = logging.getLogger(__name__)


# ── GUARDRAIL RESULT ──────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    """
    Result of a guardrail check.
    passed:          True if output is safe to show
    warnings:        non-blocking issues found
    violations:      blocking issues that must be fixed
    cleaned_output:  output after cleaning applied
    original_output: output before any cleaning
    """
    passed:          bool
    violations:      list = field(default_factory=list)
    warnings:        list = field(default_factory=list)
    cleaned_output:  str  = ""
    original_output: str  = ""


# ── CAUSAL LANGUAGE PATTERNS ──────────────────────────────────────────────────
# Auto-replaced before forbidden language check.
# Clean first, validate after.

CAUSAL_PATTERNS = [
    # Pattern, replacement
    (r"\bdonations? (?:caused|drove|influenced|bought)\b",
     "donation patterns overlap with the policy area of"),

    (r"\bbought (?:by|with)\b",
     "received industry-linked money from"),

    (r"\bin (?:the pocket|service) of\b",
     "donation patterns overlap with the policy area of"),

    (r"\bpaid (?:for|off) by\b",
     "received funding from"),

    (r"\bvoted (?:for|against) (?:their|his|her) donors\b",
     "voting pattern overlaps with the policy area of "
     "top donor sectors"),

    (r"\brepay(?:ing|ed)? (?:their|his|her) donors\b",
     "voting pattern overlaps with the policy area of "
     "top donor sectors"),

    (r"\bin exchange for\b",
     "in a cycle where donation patterns overlap with "
     "the policy area of"),

    (r"\bquid pro quo\b",
     "donation patterns overlap with the policy area of"),

    (r"\bconflict of interest\b",
     "committee-donor sector overlap worth noting"),

    (r"\bcorrupt(?:ion|ly)?\b",
     "pattern worth further review"),

    (r"\bimproper(?:ly)?\b",
     "notable"),

    (r"\billegal(?:ly)?\b",
     "worth verifying against applicable law"),
]


# ── PARTISAN LANGUAGE PATTERNS ────────────────────────────────────────────────

PARTISAN_PATTERNS = [
    r"\b(?:radical|extreme|far-right|far-left)\b",
    r"\b(?:socialist|fascist|communist|extremist)\b",
    r"\b(?:RINO|DINO|establishment hack)\b",
    r"\b(?:tax and spend|tax-and-spend)\b",
    r"\b(?:America Last|America First agenda)\b",
    r"\bdeep state\b",
    r"\bswamp\b",
    r"\bwoke\b",
]


# ── CAUSAL LANGUAGE CHECK ─────────────────────────────────────────────────────

def check_causal_language(output: str) -> tuple[list, str]:
    """
    Scan for causal language patterns.
    Auto-replaces with safer alternatives.
    Called FIRST before any other checks.
    Returns: (warnings, cleaned_output)
    """
    warnings = []
    cleaned  = output

    for pattern, replacement in CAUSAL_PATTERNS:
        matches = re.findall(
            pattern, cleaned, flags=re.IGNORECASE
        )
        if matches:
            for match in matches:
                warnings.append({
                    "type":          "causal_language",
                    "found":         match,
                    "replaced_with": replacement,
                    "message": (
                        f"Causal language '{match}' "
                        f"replaced with '{replacement}'"
                    )
                })
                logger.info(
                    f"Guardrail: replaced '{match}' "
                    f"with '{replacement}'"
                )

            cleaned = re.sub(
                pattern,
                replacement,
                cleaned,
                flags=re.IGNORECASE
            )

    return warnings, cleaned


# ── FORBIDDEN LANGUAGE CHECK ──────────────────────────────────────────────────

def check_forbidden_language(output: str) -> list:
    """
    Scan cleaned output for forbidden terms.
    Called AFTER causal language auto-replace.
    Returns list of blocking violations.
    """
    output_lower = output.lower()
    violations   = []

    for term in FORBIDDEN_TERMS:
        if term.lower() in output_lower:
            violations.append({
                "type":    "forbidden_language",
                "term":    term,
                "message": f"Forbidden term found: '{term}'"
            })
            logger.warning(
                f"Guardrail violation: "
                f"forbidden term '{term}'"
            )

    return violations


# ── REQUIRED PHRASE CHECK ─────────────────────────────────────────────────────

def check_required_phrases(output: str) -> list:
    """
    Verify all REQUIRED_PHRASES appear in output.
    Uses REQUIRED_PHRASES constant as single
    source of truth — never hardcoded strings.
    Returns list of missing phrase warnings.
    """
    output_lower = output.lower()
    warnings     = []

    for phrase in REQUIRED_PHRASES:
        if phrase.lower() not in output_lower:
            warnings.append({
                "type":    "missing_required_phrase",
                "phrase":  phrase,
                "message": f"Required phrase missing: '{phrase}'"
            })
            logger.info(
                f"Guardrail warning: "
                f"missing phrase '{phrase}'"
            )

    return warnings


# ── PARTISAN LANGUAGE CHECK ───────────────────────────────────────────────────

def check_partisan_language(output: str) -> list:
    """
    Scan cleaned output for partisan language.
    Called AFTER causal language auto-replace.
    Returns list of blocking violations.
    """
    violations = []

    for pattern in PARTISAN_PATTERNS:
        matches = re.findall(
            pattern, output, flags=re.IGNORECASE
        )
        if matches:
            for match in matches:
                violations.append({
                    "type":    "partisan_language",
                    "term":    match,
                    "message": (
                        f"Partisan language "
                        f"found: '{match}'"
                    )
                })
                logger.warning(
                    f"Guardrail violation: "
                    f"partisan term '{match}'"
                )

    return violations


# ── DATA QUALITY CHECK ────────────────────────────────────────────────────────

def check_data_quality(output: str,
                        raw_data: dict) -> list:
    """
    Flag data quality issues before output
    reaches the user.
    Handles both sqlite3.Row and tuple formats.
    Returns list of warnings.
    """
    warnings = []
    sectors  = raw_data.get("sector_totals", [])

    if not sectors:
        warnings.append({
            "type":    "no_finance_data",
            "message": (
                "No campaign finance data available "
                "for this member and cycle."
            )
        })
        return warnings

    # Handle both sqlite3.Row and tuple
    def get_sector(row):
        return (
            row["sector"]
            if hasattr(row, "keys")
            else row[0]
        ) or ""

    def get_total(row):
        return float((
            row["total"]
            if hasattr(row, "keys")
            else row[1]
        ) or 0)

    total_dollars = sum(
        get_total(row) for row in sectors
    )

    unclassified_dollars = sum(
        get_total(row) for row in sectors
        if get_sector(row).lower()
        in ("other", "unclassified")
    )

    if total_dollars > 0:
        unclassified_pct = (
            unclassified_dollars / total_dollars * 100
        )
        if unclassified_pct >= 50:
            warnings.append({
                "type":    "high_unclassified",
                "message": (
                    f"{unclassified_pct:.0f}% of contributions "
                    f"are unclassified by sector. "
                    f"Sector analysis may be incomplete."
                )
            })

    # Missing vote data
    votes = raw_data.get("votes", [])
    if not votes:
        warnings.append({
            "type":    "no_vote_data",
            "message": (
                "No voting record data available "
                "for this member and session."
            )
        })

    # Missing bill data
    bills = raw_data.get("bills", [])
    if not bills:
        warnings.append({
            "type":    "no_bill_data",
            "message": (
                "No bill sponsorship data available "
                "for this member and session."
            )
        })

    return warnings


# ── DISCLAIMER ENFORCER ───────────────────────────────────────────────────────

def ensure_disclaimer(output: str,
                       config: LevelConfig) -> str:
    """
    Ensure required phrases AND VoteIQ disclaimer
    both appear in output.
    Appends full disclaimer if either is missing.
    """
    output_lower = output.lower()

    has_required_phrases = all(
        phrase.lower() in output_lower
        for phrase in REQUIRED_PHRASES
    )
    has_voteiq_disclaimer = (
        "voteiq displays publicly available data only"
        in output_lower
    )

    if has_required_phrases and has_voteiq_disclaimer:
        return output

    return (
        output.strip()
        + "\n\n"
        + get_disclaimer(config).strip()
    )


# ── DATA QUALITY NOTICE BUILDER ───────────────────────────────────────────────

def build_data_quality_notice(warnings: list) -> str:
    """
    Build data quality notice section
    from warning list.
    Appended to output when data gaps exist.
    """
    if not warnings:
        return ""

    lines = ["## Data Limits"]
    for w in warnings:
        lines.append(f"- {w['message']}")

    return "\n".join(lines)


# ── MAIN GUARDRAIL RUNNER ─────────────────────────────────────────────────────

def run_guardrails(output: str,
                   config: LevelConfig,
                   raw_data: dict = None) -> GuardrailResult:
    """
    Run all guardrail checks on Claude output.

    ORDER:
    1. Auto-clean causal language first
    2. Check forbidden language on cleaned output
    3. Check partisan language on cleaned output
    4. Check required phrases
    5. Append data quality notices
    6. Ensure disclaimer present

    Returns GuardrailResult with cleaned output.

    Usage:
        result = run_guardrails(
            output   = claude_response,
            config   = get_config("federal"),
            raw_data = raw_data_dict
        )
        return safe_output(result)
    """
    all_violations = []
    all_warnings   = []
    cleaned        = output

    # 1. Auto-clean causal language FIRST
    causal_warnings, cleaned = check_causal_language(cleaned)
    all_warnings.extend(causal_warnings)

    # 2. Check forbidden language on cleaned output
    forbidden_violations = check_forbidden_language(cleaned)
    all_violations.extend(forbidden_violations)

    # 3. Check partisan language on cleaned output
    partisan_violations = check_partisan_language(cleaned)
    all_violations.extend(partisan_violations)

    # 4. Check required phrases
    phrase_warnings = check_required_phrases(cleaned)
    all_warnings.extend(phrase_warnings)

    # 5. Data quality notices
    if raw_data:
        quality_warnings = check_data_quality(
            cleaned, raw_data
        )
        all_warnings.extend(quality_warnings)

        quality_notice = build_data_quality_notice(
            quality_warnings
        )
        if quality_notice:
            cleaned = (
                cleaned.strip()
                + "\n\n"
                + quality_notice
            )

    # 6. Ensure disclaimer present
    cleaned = ensure_disclaimer(cleaned, config)

    passed = len(all_violations) == 0

    if not passed:
        logger.error(
            f"Guardrail FAILED: "
            f"{len(all_violations)} violations"
        )
    elif all_warnings:
        logger.info(
            f"Guardrail PASSED with "
            f"{len(all_warnings)} warnings"
        )
    else:
        logger.info("Guardrail PASSED cleanly")

    return GuardrailResult(
        passed          = passed,
        violations      = all_violations,
        warnings        = all_warnings,
        cleaned_output  = cleaned,
        original_output = output,
    )


# ── FALLBACK RESPONSE ─────────────────────────────────────────────────────────

FALLBACK_RESPONSE = """
VoteIQ was unable to generate a report
for this request.

This may occur when:
- Requested data is unavailable for this
  member or cycle
- A data quality issue was detected
- The analysis could not be completed
  with available data

Please try:
- A different cycle year
- A different legislator
- A more specific question

For assistance: alexisnieuwenhuys89@gmail.com

---
Correlation does not imply causation.
VoteIQ displays publicly available data only.
"""


def safe_output(result: GuardrailResult) -> str:
    """
    Return safe output for user display.
    Returns cleaned output if passed,
    fallback response if failed.
    """
    if result.passed:
        return result.cleaned_output
    return FALLBACK_RESPONSE


# ── QUICK CHECK ───────────────────────────────────────────────────────────────

def is_safe(output: str) -> bool:
    """
    Quick boolean safety check.
    Used for lightweight pre-screening.
    Applies causal cleaning first then validates.

    Usage:
        if not is_safe(output):
            return FALLBACK_RESPONSE
    """
    _, cleaned = check_causal_language(output)
    cleaned_lower = cleaned.lower()

    for term in FORBIDDEN_TERMS:
        if term.lower() in cleaned_lower:
            return False

    for pattern in PARTISAN_PATTERNS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return False

    return True
