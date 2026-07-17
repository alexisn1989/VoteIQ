"""
app/prediction/validate.py

Ground-truth validation of the vote prediction feature against known 2025 outcomes.

Method:
  - Test set: contested 2025 bills (30-70% yes rate, ≥50 voters, non-procedural)
  - Signals built from full DB (includes 2025 data — minor leakage, noted in output)
  - Two predictors compared:
      1. Signal rule  — no API cost; uses vote_consistency.yes_rate threshold
      2. Claude full  — calls predictor.py on a sampled subset
  - Baseline: always predict YES

Usage:
    python app/prediction/validate.py                   # signal-rule only (free)
    python app/prediction/validate.py --claude          # + Claude on 50 sampled pairs
    python app/prediction/validate.py --claude --n 30   # Claude on 30 pairs
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.prediction.signal_builder import (
    _INVERTED_SIGNAL_AREAS,
    _LOW_RELIABILITY_AREAS,
    build_signals,
)

POLLS_DB = BASE_DIR / "polls.db"
TEST_SESSION = "2025"
CONTESTED_MIN_YES = 30
CONTESTED_MAX_YES = 70
MIN_VOTERS = 50
MAX_BILLS = 40
LEGS_PER_BILL = 8          # sample per bill for Claude pass (cost control)
DEFAULT_CLAUDE_N = 50


# ── Helpers ───────────────────────────────────────────────────────────────────

def _actual_label(option: str) -> str:
    return "YES" if option == "yes" else "NO"


def _signal_rule(signals: dict) -> str:
    """Simple threshold on vote_consistency.yes_rate — no API call."""
    vc = signals.get("vote_consistency") or {}
    rate = vc.get("yes_rate")
    if rate is None:
        return "INSUFFICIENT_DATA"
    policy_area = (signals.get("policy_area") or "").upper()
    if policy_area in _INVERTED_SIGNAL_AREAS:
        # vote_consistency is an inverted signal here (validation: 0-4% accuracy) — always abstain
        return "UNCERTAIN"
    if policy_area in _LOW_RELIABILITY_AREAS:
        # Validation showed these areas score below baseline — require extreme yes_rate
        if rate >= 0.80:
            return "YES"
        if rate <= 0.20:
            return "NO"
        return "UNCERTAIN"
    if rate >= 0.60:
        return "YES"
    if rate <= 0.40:
        return "NO"
    return "UNCERTAIN"


def _verdict_matches(verdict: str, actual: str) -> bool | None:
    """True=correct, False=wrong, None=abstain (UNCERTAIN/INSUFFICIENT_DATA)."""
    if verdict in ("UNCERTAIN", "INSUFFICIENT_DATA"):
        return None
    predicted_yes = verdict in ("YES", "LIKELY_YES")
    actual_yes = actual == "YES"
    return predicted_yes == actual_yes


# ── Data loading ──────────────────────────────────────────────────────────────

def load_contested_bills(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(f"""
        SELECT
            v.bill_id,
            b.title,
            pa.policy_area,
            COUNT(*) total_votes,
            SUM(CASE WHEN v.option='yes' THEN 1 ELSE 0 END) yes_count,
            ROUND(100.0*SUM(CASE WHEN v.option='yes' THEN 1 ELSE 0 END)/COUNT(*),1) yes_pct
        FROM va_legislator_recent_votes v
        JOIN va_bills b
            ON b.bill_number = SUBSTR(v.bill_id, 1, INSTR(v.bill_id||'_','_')-1)
            AND b.session = v.session
        LEFT JOIN va_bill_policy_areas pa
            ON pa.bill_number = b.bill_number AND pa.session = b.session
        WHERE v.session='{TEST_SESSION}' AND v.option IN ('yes','no') AND v.is_procedural=0
        GROUP BY v.bill_id
        HAVING total_votes >= {MIN_VOTERS}
           AND yes_pct BETWEEN {CONTESTED_MIN_YES} AND {CONTESTED_MAX_YES}
        ORDER BY ABS(yes_pct - 50)
        LIMIT {MAX_BILLS}
    """).fetchall()
    return [
        {"bill_id": r[0], "title": r[1], "policy_area": r[2] or "OTHER",
         "total_votes": r[3], "yes_count": r[4], "yes_pct": r[5]}
        for r in rows
    ]


def load_actual_votes(conn: sqlite3.Connection, bill_id: str) -> list[dict]:
    """Return all actual yes/no votes for a bill with canonical name resolution."""
    rows = conn.execute("""
        SELECT v.voter_name, v.option, ni.canonical_name, ni.party, ni.chamber
        FROM va_legislator_recent_votes v
        JOIN va_name_index ni ON ni.votes_table_name = v.voter_name
        WHERE v.bill_id = ? AND v.session = ? AND v.option IN ('yes','no')
    """, (bill_id, TEST_SESSION)).fetchall()
    return [
        {"voter_name": r[0], "option": r[1], "canonical_name": r[2],
         "party": r[3] or "Unknown", "chamber": r[4] or "Unknown"}
        for r in rows
    ]


# ── Signal-rule validation (free) ────────────────────────────────────────────

def run_signal_rule(conn: sqlite3.Connection, bills: list[dict]) -> list[dict]:
    results = []
    total_pairs = sum(len(load_actual_votes(conn, b["bill_id"])) for b in bills)
    print(f"\nSignal-rule pass: {len(bills)} bills, ~{total_pairs} pairs")
    print("Building signals", end="", flush=True)

    for bill in bills:
        actual_votes = load_actual_votes(conn, bill["bill_id"])
        for av in actual_votes:
            signals = build_signals(
                conn,
                av["canonical_name"],
                bill["policy_area"],
                bill_number=bill["bill_id"].split("_")[0],
                session=TEST_SESSION,
            )
            verdict = _signal_rule(signals)
            correct = _verdict_matches(verdict, _actual_label(av["option"]))
            results.append({
                "bill_id":        bill["bill_id"],
                "policy_area":    bill["policy_area"],
                "canonical_name": av["canonical_name"],
                "party":          av["party"],
                "chamber":        av["chamber"],
                "actual":         _actual_label(av["option"]),
                "verdict":        verdict,
                "correct":        correct,
                "yes_rate":       (signals.get("vote_consistency") or {}).get("yes_rate"),
                "confidence":     signals.get("confidence_level"),
            })
        print(".", end="", flush=True)

    print(" done")
    return results


# ── Claude validation (API) ───────────────────────────────────────────────────

def run_claude_sample(conn: sqlite3.Connection, bills: list[dict], n: int) -> list[dict]:
    from dotenv import load_dotenv
    load_dotenv()
    from app.prediction.predictor import predict_vote

    # Build candidate pairs stratified by bill
    pool: list[tuple[dict, dict]] = []
    for bill in bills:
        votes = load_actual_votes(conn, bill["bill_id"])
        # Stratify: half YES voters, half NO voters
        yes_v = [v for v in votes if v["option"] == "yes"]
        no_v  = [v for v in votes if v["option"] == "no"]
        sample_size = min(LEGS_PER_BILL, len(yes_v) + len(no_v))
        each = sample_size // 2
        sampled = random.sample(yes_v, min(each, len(yes_v))) + \
                  random.sample(no_v,  min(each, len(no_v)))
        for v in sampled:
            pool.append((bill, v))

    random.shuffle(pool)
    pairs = pool[:n]
    print(f"\nClaude pass: {len(pairs)} pairs (sampled from {len(pool)} candidates)")

    results = []
    for i, (bill, av) in enumerate(pairs, 1):
        signals = build_signals(
            conn,
            av["canonical_name"],
            bill["policy_area"],
            bill_number=bill["bill_id"].split("_")[0],
            session=TEST_SESSION,
        )
        pred = predict_vote(signals, bill["title"], "")
        verdict = pred.get("verdict", "INSUFFICIENT_DATA")
        correct = _verdict_matches(verdict, _actual_label(av["option"]))
        results.append({
            "bill_id":        bill["bill_id"],
            "policy_area":    bill["policy_area"],
            "canonical_name": av["canonical_name"],
            "party":          av["party"],
            "actual":         _actual_label(av["option"]),
            "verdict":        verdict,
            "correct":        correct,
            "confidence":     signals.get("confidence_level"),
            "reasoning":      pred.get("reasoning", "")[:120],
        })
        status = "✓" if correct is True else ("✗" if correct is False else "–")
        print(f"  [{i:>3}/{len(pairs)}] {status} {av['canonical_name']:<28} "
              f"{bill['bill_id']:<12} actual={_actual_label(av['option'])} "
              f"predicted={verdict}")

    return results


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(results: list[dict], label: str) -> None:
    total     = len(results)
    predicted = [r for r in results if r["correct"] is not None]
    correct   = [r for r in predicted if r["correct"]]
    abstained = total - len(predicted)

    if not predicted:
        print(f"\n{label}: no predictions made")
        return

    accuracy  = 100 * len(correct) / len(predicted)
    coverage  = 100 * len(predicted) / total

    # Baseline: always YES on contested bills ≈ ~50% (they're 30-70% yes rate)
    yes_actual = sum(1 for r in results if r["actual"] == "YES")
    baseline   = 100 * yes_actual / total

    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    print(f"  Pairs evaluated : {total}")
    print(f"  Predicted       : {len(predicted)} ({coverage:.0f}% coverage)")
    print(f"  Abstained       : {abstained}")
    print(f"  Accuracy        : {accuracy:.1f}%  (baseline always-YES: {baseline:.1f}%)")
    print(f"  Lift over base  : {accuracy - baseline:+.1f}pp")

    # By party
    print(f"\n  By party:")
    for party in sorted({r["party"] for r in results}):
        p_results = [r for r in results if r["party"] == party and r["correct"] is not None]
        if p_results:
            acc = 100 * sum(1 for r in p_results if r["correct"]) / len(p_results)
            print(f"    {party:<18} {acc:>5.1f}%  (n={len(p_results)})")

    # By policy area
    print(f"\n  By policy area:")
    for area in sorted({r["policy_area"] for r in results}):
        a_results = [r for r in results if r["policy_area"] == area and r["correct"] is not None]
        if len(a_results) >= 5:
            acc = 100 * sum(1 for r in a_results if r["correct"]) / len(a_results)
            print(f"    {area:<22} {acc:>5.1f}%  (n={len(a_results)})")

    # Verdict distribution
    from collections import Counter
    dist = Counter(r["verdict"] for r in results)
    print(f"\n  Verdict distribution: {dict(dist)}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Validate vote predictor against 2025 outcomes")
    parser.add_argument("--claude", action="store_true", help="Run Claude predictor on a sample")
    parser.add_argument("--n", type=int, default=DEFAULT_CLAUDE_N,
                        help=f"Pairs for Claude pass (default {DEFAULT_CLAUDE_N})")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    random.seed(args.seed)
    conn = sqlite3.connect(str(POLLS_DB))
    conn.row_factory = sqlite3.Row

    print(f"Test set: {TEST_SESSION} contested bills "
          f"({CONTESTED_MIN_YES}-{CONTESTED_MAX_YES}% yes rate, >={MIN_VOTERS} voters)")
    print("NOTE: signals use full DB including 2025 data — minor leakage in vote_consistency")

    bills = load_contested_bills(conn)
    print(f"Loaded {len(bills)} contested bills across "
          f"{len({b['policy_area'] for b in bills})} policy areas")

    # Signal-rule pass (always runs)
    rule_results = run_signal_rule(conn, bills)
    _score(rule_results, "SIGNAL-RULE PREDICTOR (vote_consistency threshold)")

    # Claude pass (optional)
    if args.claude:
        claude_results = run_claude_sample(conn, bills, args.n)
        _score(claude_results, f"CLAUDE PREDICTOR (n={len(claude_results)})")

    conn.close()


if __name__ == "__main__":
    main()
