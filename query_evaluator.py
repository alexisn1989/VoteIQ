"""
query_evaluator.py

Per-response scoring wrapper for VoteIQ pipeline output.
Designed to add <50 ms overhead; no DB calls at eval time.

Scores:
  sql_correct              — did SQL return rows for a factual query?
  entity_resolution_accuracy — resolved entity matches query intent (fuzzy)
  hallucination_risk       — float 0-1 (confident language + unsourced numbers)
  source_alignment         — "perfect" | "partial" | "unsupported"
  abstain_correct          — if system abstained, was it appropriate?

Usage:
    from query_evaluator import QueryEvaluator
    ev = QueryEvaluator()
    result = ev.evaluate(
        raw_query="How did Aaron Rouse vote on HB1?",
        sql_used="SELECT ...",
        rows_returned=[{"voter_name": "Aaron Rouse", "option": "yes"}],
        rag_chunks=[],
        final_answer="Aaron Rouse voted Yes on HB1.",
    )
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from rapidfuzz import fuzz as _fuzz
    _partial_ratio = lambda a, b: _fuzz.partial_ratio(a.lower(), b.lower()) / 100.0
except ImportError:
    _partial_ratio = lambda a, b: 1.0 if a.lower() in b.lower() else 0.0

_BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))
_DEFAULT_LOG = DATA_DIR / "eval_log.jsonl"

_ABSTAIN_SIGNALS = (
    "i don't know", "not available", "no data", "insufficient",
    "cannot find", "no records", "outside the scope", "not included",
    "not currently", "i cannot", "i'm unable", "no information",
    "couldn't find", "data not",
)

_CONFIDENT_PHRASES = (
    "definitely", "certainly", "absolutely", "always", "never",
    "guarantee", "guaranteed", "exactly", "precisely", "undoubtedly",
    "without doubt", "clearly", "obviously",
)

# Monetary amounts and large integers that could be hallucinated
_NUMBER_RE = re.compile(r"\$[\d,]+\.?\d*|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{5,}\b")

# Common question words to skip when extracting entity candidates
_QUESTION_STOP = frozenset({
    "how", "what", "who", "did", "does", "the", "are", "was", "were",
    "vote", "voted", "voting", "votes", "bill", "bills", "about", "which",
    "when", "where", "tell", "show", "give", "list", "much", "many",
    "have", "has", "been", "from", "for", "with", "that", "this",
})


@dataclass
class QueryEvalResult:
    raw_query: str
    sql_correct: bool
    entity_resolution_accuracy: float
    hallucination_risk: float
    source_alignment: str
    abstain_correct: bool
    duration_ms: float
    timestamp: str

    @property
    def passed(self) -> bool:
        return (
            self.sql_correct
            and self.entity_resolution_accuracy >= 0.5
            and self.hallucination_risk <= 0.5
            and self.source_alignment != "unsupported"
        )


class QueryEvaluator:
    def __init__(
        self,
        log_path: Path | None = _DEFAULT_LOG,
        append_log: bool = True,
    ):
        self.log_path = log_path
        self.append_log = append_log

    def evaluate(
        self,
        raw_query: str,
        sql_used: str,
        rows_returned: list,
        rag_chunks: list,
        final_answer: str,
    ) -> QueryEvalResult:
        t0 = time.monotonic()
        ans_lower = (final_answer or "").lower()
        did_abstain = any(sig in ans_lower for sig in _ABSTAIN_SIGNALS)

        sql_correct = bool(rows_returned) or did_abstain
        entity_resolution_accuracy = self._entity_resolution(raw_query, final_answer)
        hallucination_risk = self._hallucination_risk(final_answer, rows_returned, rag_chunks)
        source_alignment = self._source_alignment(final_answer, rows_returned, rag_chunks, did_abstain)
        # Abstain is correct when: abstained AND no rows, OR didn't abstain AND had rows
        abstain_correct = did_abstain == (not bool(rows_returned))

        result = QueryEvalResult(
            raw_query=raw_query,
            sql_correct=sql_correct,
            entity_resolution_accuracy=round(entity_resolution_accuracy, 3),
            hallucination_risk=round(hallucination_risk, 3),
            source_alignment=source_alignment,
            abstain_correct=abstain_correct,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if self.append_log and self.log_path:
            self._append_log(result)

        return result

    # ── Scoring sub-functions ──────────────────────────────────────────────────

    def _entity_resolution(self, query: str, answer: str) -> float:
        """Extract likely proper names from query; fuzzy-match them against the answer."""
        candidates = [
            w for w in re.findall(r"[A-Z][a-z]{1,}", query)
            if w.lower() not in _QUESTION_STOP and len(w) >= 3
        ]
        if not candidates:
            return 1.0
        scores = [_partial_ratio(c, answer) for c in candidates]
        return sum(scores) / len(scores)

    def _hallucination_risk(
        self, answer: str, rows_returned: list, rag_chunks: list
    ) -> float:
        ans_lower = (answer or "").lower()
        risk = 0.0

        # Overconfident language
        confident_hits = sum(1 for p in _CONFIDENT_PHRASES if p in ans_lower)
        risk += min(confident_hits * 0.08, 0.32)

        # Specific numbers in answer not backed by any source
        answer_numbers = set(_NUMBER_RE.findall(answer or ""))
        if answer_numbers and not rows_returned and not rag_chunks:
            risk += min(len(answer_numbers) * 0.07, 0.40)
        elif answer_numbers and rows_returned:
            # Check which numbers don't appear in any row string
            row_text = " ".join(str(r) for r in rows_returned)
            unsourced = sum(1 for n in answer_numbers if n not in row_text)
            risk += min(unsourced * 0.04, 0.20)

        # Answer much longer than source material
        source_text = (
            " ".join(str(r) for r in rows_returned)
            + " ".join(str(c) for c in rag_chunks)
        )
        if source_text:
            ratio = len(answer) / max(len(source_text), 1)
            if ratio > 3.0:
                risk += min((ratio - 3.0) * 0.04, 0.20)

        return min(risk, 1.0)

    def _source_alignment(
        self,
        answer: str,
        rows_returned: list,
        rag_chunks: list,
        did_abstain: bool,
    ) -> str:
        if did_abstain and not rows_returned and not rag_chunks:
            return "perfect"  # appropriate abstain
        if not rows_returned and not rag_chunks:
            return "unsupported" if not did_abstain else "partial"

        source_text = (
            " ".join(str(r) for r in rows_returned)
            + " ".join(str(c) for c in rag_chunks)
        ).lower()

        # Meaningful words from the answer (4+ chars, alphabetic)
        answer_words = re.findall(r"\b[a-z]{4,}\b", (answer or "").lower())
        if not answer_words:
            return "partial"

        matches = sum(1 for w in answer_words if w in source_text)
        coverage = matches / len(answer_words)

        if coverage >= 0.60:
            return "perfect"
        if coverage >= 0.30:
            return "partial"
        return "unsupported"

    # ── Log persistence ────────────────────────────────────────────────────────

    def _append_log(self, result: QueryEvalResult) -> None:
        try:
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(result)) + "\n")
        except Exception:
            pass  # logging must never break the pipeline


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="VoteIQ query evaluator — standalone smoke test")
    ap.add_argument("--query", default="How did Aaron Rouse vote on HB1 in 2025?")
    ap.add_argument("--answer", default="Aaron Rouse voted Yes on HB1 in the 2025 session.")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    ev = QueryEvaluator(append_log=not args.no_log)
    result = ev.evaluate(
        raw_query=args.query,
        sql_used="SELECT * FROM va_legislator_recent_votes WHERE voter_name LIKE '%Rouse%'",
        rows_returned=[{"voter_name": "Aaron Rouse", "option": "yes", "bill_id": "HB1", "session": "2025"}],
        rag_chunks=[],
        final_answer=args.answer,
    )
    print(json.dumps(asdict(result), indent=2))
    print(f"\npassed={result.passed}", file=__import__("sys").stderr)
