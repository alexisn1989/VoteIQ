"""
truth_test_suite.py

Regression testing harness for VoteIQ ground-truth Q&A pairs.
Loads test cases from a YAML file, runs each through the live pipeline,
scores results, and exits non-zero if overall accuracy falls below threshold.

Usage:
    python truth_test_suite.py                         # uses truth_tests.yaml, threshold 0.90
    python truth_test_suite.py --yaml my_tests.yaml --threshold 0.85
    python truth_test_suite.py --output report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

try:
    from rapidfuzz import fuzz as _fuzz
    _fuzzy_ratio = lambda a, b: _fuzz.partial_ratio(a.lower(), b.lower()) / 100.0
except ImportError:
    _fuzzy_ratio = lambda a, b: 1.0 if a.lower() in b.lower() else 0.0

_BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))

_ABSTAIN_SIGNALS = (
    "i don't know", "i don't have", "not available", "no data", "insufficient",
    "cannot find", "no records", "outside the scope", "not included",
    "not currently", "i cannot", "i'm unable", "no information",
    "couldn't find", "data not", "does not include", "do not contain",
    "does not have", "do not have",
)


@dataclass
class TestCase:
    query: str
    expected_entity: str
    expected_sql_table: str
    expected_answer_contains: list[str]
    expected_abstain: bool
    expected_hallucination_risk_max: float


@dataclass
class TestResult:
    query: str
    expected_entity: str
    expected_sql_table: str
    sql_correct: bool
    entity_resolved: bool
    answer_alignment: float
    abstain_correct: bool
    hallucination_risk_ok: bool
    passed: bool
    score: float
    actual_answer: str
    duration_ms: float


class TruthTestRunner:
    def __init__(self, yaml_path: str | Path = "truth_tests.yaml", threshold: float = 0.90):
        self.yaml_path = Path(yaml_path)
        self.threshold = threshold
        self.test_cases: list[TestCase] = []

    def load(self) -> None:
        if _yaml is None:
            raise ImportError("PyYAML is required: pip install pyyaml")
        with open(self.yaml_path, encoding="utf-8") as f:
            raw = _yaml.safe_load(f)
        for item in (raw or {}).get("tests", []):
            self.test_cases.append(TestCase(
                query=item["query"],
                expected_entity=item.get("expected_entity", ""),
                expected_sql_table=item.get("expected_sql_table", ""),
                expected_answer_contains=item.get("expected_answer_contains", []),
                expected_abstain=bool(item.get("expected_abstain", False)),
                expected_hallucination_risk_max=float(item.get("expected_hallucination_risk_max", 1.0)),
            ))

    def _score(self, tc: TestCase, answer: str, duration_ms: float) -> TestResult:
        ans_lower = (answer or "").lower()
        # Abstains lead the answer ("Quick Answer: I don't have..."); substantive
        # answers often carry mid-text caveats ("FEC data does not include
        # employer for some donors") that must not count as abstaining, so only
        # scan the opening of the reply.
        did_abstain = any(sig in ans_lower[:400] for sig in _ABSTAIN_SIGNALS)

        # sql_correct: proxy via abstain detection — if we have expected_sql_table
        # and did NOT abstain, assume SQL was used. If we expected abstain and got it, correct.
        sql_correct = (not tc.expected_abstain and not did_abstain) or \
                      (tc.expected_abstain and did_abstain) or \
                      (not tc.expected_abstain and not did_abstain)

        # entity_resolved: fuzzy match of expected entity in answer
        if tc.expected_entity:
            ratio = _fuzzy_ratio(tc.expected_entity, answer)
            entity_resolved = ratio >= 0.7
        else:
            entity_resolved = True

        # answer_alignment: fraction of expected strings found in answer
        if tc.expected_answer_contains:
            hits = sum(1 for s in tc.expected_answer_contains if s.lower() in ans_lower)
            answer_alignment = hits / len(tc.expected_answer_contains)
        else:
            answer_alignment = 1.0

        # abstain_correct
        abstain_correct = did_abstain == tc.expected_abstain

        # hallucination_risk: overconfident-phrase heuristic only. An earlier
        # version also penalized answer length relative to the expected-strings
        # length, but expected strings are short anchors ("Rouse"), so every
        # normal markdown answer scored ~1.0 risk — calibration against
        # production on 2026-07-01 failed 15/16 healthy answers on that term.
        _confident = ("definitely", "certainly", "absolutely", "always", "never",
                      "guarantee", "exactly", "precisely", "undoubtedly", "clearly")
        confident_hits = sum(1 for p in _confident if p in ans_lower)
        hallucination_risk = min(confident_hits * 0.08, 1.0)
        hallucination_risk_ok = hallucination_risk <= tc.expected_hallucination_risk_max

        passed = (
            sql_correct
            and entity_resolved
            and answer_alignment >= 0.5
            and abstain_correct
            and hallucination_risk_ok
        )
        score = (
            float(sql_correct)
            + float(entity_resolved)
            + answer_alignment
            + float(abstain_correct)
            + float(hallucination_risk_ok)
        ) / 5.0

        return TestResult(
            query=tc.query,
            expected_entity=tc.expected_entity,
            expected_sql_table=tc.expected_sql_table,
            sql_correct=sql_correct,
            entity_resolved=entity_resolved,
            answer_alignment=answer_alignment,
            abstain_correct=abstain_correct,
            hallucination_risk_ok=hallucination_risk_ok,
            passed=passed,
            score=score,
            actual_answer=answer[:500],
            duration_ms=round(duration_ms, 1),
        )

    def run(self, run_query: Callable[[str], str]) -> dict:
        results: list[TestResult] = []
        for tc in self.test_cases:
            t0 = time.monotonic()
            try:
                answer = run_query(tc.query)
            except Exception as exc:
                answer = f"ERROR: {exc}"
            results.append(self._score(tc, answer, (time.monotonic() - t0) * 1000))

        passed = [r for r in results if r.passed]
        overall_accuracy = len(passed) / len(results) if results else 0.0

        return {
            "total": len(results),
            "passed": len(passed),
            "failed": len(results) - len(passed),
            "overall_accuracy": round(overall_accuracy, 4),
            "threshold": self.threshold,
            "meets_threshold": overall_accuracy >= self.threshold,
            "results": [asdict(r) for r in results],
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="VoteIQ truth regression suite")
    ap.add_argument("--yaml", default="truth_tests.yaml")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--output", default=None, help="Write JSON report to path")
    ap.add_argument("--url", default=None,
                    help="Run against a deployed service over HTTP (e.g. https://voteiq.io) "
                         "instead of the in-process pipeline. Used by the nightly CI job.")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="Seconds between HTTP queries — /api/bills-chat is rate-limited "
                         "to 20/minute (default: 4.0)")
    args = ap.parse_args()

    runner = TruthTestRunner(args.yaml, args.threshold)
    runner.load()
    print(f"Loaded {len(runner.test_cases)} test cases from {args.yaml}", file=sys.stderr)

    if args.url:
        # HTTP mode: exercise the real deployed pipeline (Render disk data +
        # production Claude calls). Needs no local DB or API keys.
        import urllib.request as _ur
        base = args.url.rstrip("/")
        print(f"Running against {base}/api/bills-chat", file=sys.stderr)

        def run_query(q: str) -> str:
            payload = json.dumps(
                {"messages": [{"role": "user", "content": q}]}
            ).encode("utf-8")
            last_exc: Exception | None = None
            for attempt in range(2):  # one retry — transient SSL/connect hiccups
                request = _ur.Request(
                    f"{base}/api/bills-chat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with _ur.urlopen(request, timeout=180) as resp:
                        reply = json.loads(
                            resp.read().decode("utf-8", errors="replace")
                        ).get("reply", "")
                    time.sleep(args.delay)
                    return reply
                except Exception as exc:
                    last_exc = exc
                    time.sleep(10)
            raise last_exc
    else:
        # In-process mode: bills_chat takes (request, req) since the slowapi
        # rate limiter was added, so it can no longer be called as a bare
        # function — go through the ASGI app instead. Requires the full local
        # environment (polls.db + ANTHROPIC_API_KEY); import errors are fatal
        # on purpose so a broken pipeline reads as broken, not as 0% accuracy.
        from fastapi.testclient import TestClient
        import main as _main
        _client = TestClient(_main.app)

        def run_query(q: str) -> str:
            resp = _client.post(
                "/api/bills-chat",
                json={"messages": [{"role": "user", "content": q}]},
            )
            resp.raise_for_status()
            return resp.json().get("reply", "")

    report = runner.run(run_query)
    out_json = json.dumps(report, indent=2)
    print(out_json)

    if args.output:
        out_path = (
            DATA_DIR / args.output
            if not Path(args.output).is_absolute()
            else Path(args.output)
        )
        out_path.write_text(out_json, encoding="utf-8")

    if not report["meets_threshold"]:
        print(
            json.dumps({
                "event": "truth_suite_fail",
                "accuracy": report["overall_accuracy"],
                "threshold": args.threshold,
                "failed_count": report["failed"],
            }),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
