from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voteiq.services.database_context import build_database_context
from voteiq.services.truth import extract_facts, verify_claims


CANONICAL_QUERIES = [
    "How did Delegate Aaron Rouse vote on SB249?",
    "How did Delegate X vote on HB774?",
    "What is total donations for Senator Louise Lucas?",
    "What is total donations for Senator Y?",
    "Did HB123 pass?",
    "Did SB582 pass?",
    "How many yes and no votes were recorded for HB1258?",
    "Did Jennifer Kiggans vote yes or no on the defense bill?",
    "How much did Abigail Spanberger raise in campaign donations?",
    "Did Governor Spanberger veto the energy bill?",
]


def _first_sql_line(context: str) -> str:
    for line in (context or "").splitlines():
        if line.startswith("- ") and "=" in line:
            return line[2:].strip()
    return ""


def _deterministic_answer(query: str, sql_results: str) -> str:
    """Tiny local stand-in for final response generation in truth regressions.

    The answer may only restate structured facts that appear verbatim in the
    SQL context. If no usable SQL line exists, it must abstain without adding
    numeric, vote, or outcome claims.
    """
    row = _first_sql_line(sql_results)
    if not row:
        return "No SQL-backed structured fact is available for this query."

    bill = re.search(r"\b(?:HB|SB|HJ|SJ|HR|SR|HJR|SJR)\s*-?\s*\d{1,5}\b", row, re.I)
    money = re.search(r"\$[\d,]+(?:\.\d+)?", row)
    vote = re.search(r"\b(voted|vote|position|option|result)=?([^;\n]*\b(?:yes|no|yea|nay)\b)", row, re.I)
    outcome = re.search(r"\b(status|outcome|result|last_action)=?([^;\n]*\b(?:passed|failed)\b[^;\n]*)", row, re.I)
    count = re.search(
        r"\b(\d{1,4})\s*(?:yes|yea|ayes|y)\b.{0,40}?\b(\d{1,4})\s*(?:no|nay|n)\b",
        row,
        re.I,
    )

    if count and bill:
        return f"{bill.group(0).upper()} recorded {count.group(1)} yes and {count.group(2)} no."
    if vote:
        return f"The SQL result says {vote.group(0)}."
    if outcome and bill:
        return f"{bill.group(0).upper()} {outcome.group(2).strip()}."
    if money:
        return f"The SQL result includes {money.group(0)}."
    return "SQL returned rows, but no simple structured truth claim is emitted by this regression harness."


def run_full_pipeline(query: str) -> dict:
    sql_results = build_database_context(query, max_chars=4000)
    final_answer = _deterministic_answer(query, sql_results)
    claims = extract_facts(final_answer)
    verification = verify_claims(claims, sql_results)
    return {
        "query": query,
        "sql_results": sql_results,
        "final_answer": final_answer,
        "claims": claims,
        "verification": verification,
    }


def test_truth_regression_suite_has_no_sql_mismatch_claims():
    failures = []
    for query in CANONICAL_QUERIES:
        result = run_full_pipeline(query)
        mismatches = [item for item in result["verification"] if not item["verified"]]
        if mismatches:
            failures.append(
                {
                    "query": query,
                    "answer": result["final_answer"],
                    "claims": result["claims"],
                    "mismatches": mismatches,
                }
            )

    assert not failures, failures


def test_hallucinated_structured_fact_fails_verification():
    sql_results = build_database_context("Did HB123 pass?", max_chars=4000)
    hallucinated_answer = "HB123 passed 99 yes and 1 no with $999,999 in donations."
    claims = extract_facts(hallucinated_answer)
    verification = verify_claims(claims, sql_results)

    assert claims
    assert any(not item["verified"] for item in verification)
