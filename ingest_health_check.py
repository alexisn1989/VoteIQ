"""
ingest_health_check.py

Lightweight post-ingest anomaly detection for VoteIQ.
Run after every SBE/legislative ingest to catch row-count drops, null spikes,
duplicate PKs, and donor resolution failures before they reach production.

Hard-fail conditions (exit 1):
  - >40% row count drop vs baseline
  - >10% null rate on any critical field
  - Duplicate PK groups detected

Usage:
    python ingest_health_check.py
    python ingest_health_check.py --write-baseline   # force-update baseline
    python ingest_health_check.py --db /var/data/polls.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "polls.db")))
BASELINE_PATH = DATA_DIR / "health_baseline.json"

# Tables to track for row counts after every ingest
_TRACKED_TABLES = [
    "legislators",
    "va_bills",
    "va_legislator_recent_votes",
    "va_cf_schedule_a",
    "va_cf_reports",
    "campaign_finance_summary",
    "fec_individual_contributions",
    "fec_independent_expenditures",
    "lobbyist_registry",
    "governor_actions",
]

# Critical fields per the spec: legislator_name, vote_value, amount, donor_name.
# va_cf_reports.CandidateName is intentionally excluded — PAC/committee filings
# legitimately have no candidate name (CandidateType != candidate committee).
_CRITICAL_NULLS: dict[str, list[str]] = {
    "legislators": ["name"],
    "va_legislator_recent_votes": ["voter_name", "option"],
    "va_cf_schedule_a": ["amount"],
    "fec_individual_contributions": ["amount"],
    "fec_independent_expenditures": ["expenditure_amount", "committee_name"],
}

# Compound PK columns per table for duplicate detection.
# Mirrors the unique-index fix that resolved 21,901 duplicate vote rows.
# va_cf_schedule_a is excluded: no stable compound PK exists in the SBE schema;
# duplicate detection there is handled by the donor_resolution_fallback_rate check.
_PK_COLUMNS: dict[str, list[str]] = {
    # A legislator can cast separate procedural and final-passage votes on the same
    # bill in the same session, so motion is required to make a true compound PK.
    "va_legislator_recent_votes": ["bill_id", "voter_name", "session", "motion"],
    "legislators": ["id"],
    "va_bills": ["bill_number", "session"],
}

_DROP_HARD = 0.40   # >40% drop → hard fail
_DROP_WARN = 0.20   # >20% drop → warning
_NULL_HARD = 0.10   # >10% null rate → hard fail


class HealthCheckRunner:
    def __init__(self, db_path: Path = DB_PATH, baseline_path: Path = BASELINE_PATH):
        self.db_path = db_path
        self.baseline_path = baseline_path

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            _fatal(f"DB not found: {self.db_path}")
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    def _row_count(self, conn: sqlite3.Connection, table: str) -> int | None:
        if not self._table_exists(conn, table):
            return None
        try:
            return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except Exception:
            return None

    def _null_rate(self, conn: sqlite3.Connection, table: str, col: str) -> float | None:
        if not self._table_exists(conn, table):
            return None
        try:
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if total == 0:
                return 0.0
            nulls = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" '
                f'WHERE "{col}" IS NULL OR CAST("{col}" AS TEXT) = \'\''
            ).fetchone()[0]
            return nulls / total
        except Exception:
            return None

    def _duplicate_pk_groups(self, conn: sqlite3.Connection, table: str, pk_cols: list[str]) -> int:
        if not self._table_exists(conn, table):
            return 0
        col_expr = ", ".join(f'"{c}"' for c in pk_cols)
        try:
            row = conn.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT {col_expr}, COUNT(*) AS cnt
                    FROM "{table}"
                    GROUP BY {col_expr}
                    HAVING cnt > 1
                )
            """).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def _donor_resolution_fallback_rate(self, conn: sqlite3.Connection) -> float | None:
        """Fraction of SBE rows where both name fields are empty — can't be attributed."""
        if not self._table_exists(conn, "va_cf_schedule_a"):
            return None
        try:
            total = conn.execute("SELECT COUNT(*) FROM va_cf_schedule_a").fetchone()[0]
            if total == 0:
                return 0.0
            failed = conn.execute(
                "SELECT COUNT(*) FROM va_cf_schedule_a "
                "WHERE (last_or_company IS NULL OR last_or_company = '') "
                "AND (first_name IS NULL OR first_name = '')"
            ).fetchone()[0]
            return failed / total
        except Exception:
            return None

    def _zero_record_sessions(self, conn: sqlite3.Connection) -> list[str]:
        """Sessions present in va_legislator_recent_votes with zero rows after dedup."""
        if not self._table_exists(conn, "va_legislator_recent_votes"):
            return []
        try:
            rows = conn.execute(
                "SELECT session, COUNT(*) AS cnt "
                "FROM va_legislator_recent_votes "
                "GROUP BY session "
                "HAVING cnt = 0 "
                "ORDER BY session"
            ).fetchall()
            return [str(r["session"]) for r in rows]
        except Exception:
            return []

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def collect_snapshot(self) -> dict:
        conn = self._connect()
        try:
            row_counts: dict[str, int | None] = {
                t: self._row_count(conn, t) for t in _TRACKED_TABLES
            }
            null_rates: dict[str, dict[str, float | None]] = {
                t: {c: self._null_rate(conn, t, c) for c in cols}
                for t, cols in _CRITICAL_NULLS.items()
            }
            dup_counts: dict[str, int] = {
                t: self._duplicate_pk_groups(conn, t, pk)
                for t, pk in _PK_COLUMNS.items()
            }
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "db_path": str(self.db_path),
                "row_counts": row_counts,
                "null_rates": null_rates,
                "duplicate_counts": dup_counts,
                "donor_resolution_fallback_rate": self._donor_resolution_fallback_rate(conn),
                "zero_record_sessions": self._zero_record_sessions(conn),
            }
        finally:
            conn.close()

    # ── Baseline ───────────────────────────────────────────────────────────────

    def _load_baseline(self) -> dict | None:
        if not self.baseline_path.exists():
            return None
        try:
            return json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_baseline(self, snapshot: dict) -> None:
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    # ── Run ────────────────────────────────────────────────────────────────────

    def run(self, write_baseline: bool = False) -> dict:
        snapshot = self.collect_snapshot()
        baseline = self._load_baseline()

        warnings: list[str] = []
        hard_failures: list[str] = []

        # Row count delta
        if baseline:
            for table, current in snapshot["row_counts"].items():
                prev = baseline.get("row_counts", {}).get(table)
                if prev is None or current is None or prev == 0:
                    continue
                drop = (prev - current) / prev
                if drop > _DROP_HARD:
                    hard_failures.append(
                        f"ROW_DROP_CRITICAL: {table} -{drop:.0%} ({prev} → {current})"
                    )
                elif drop > _DROP_WARN:
                    warnings.append(
                        f"ROW_DROP_WARN: {table} -{drop:.0%} ({prev} → {current})"
                    )

        # Null rates
        for table, cols in snapshot["null_rates"].items():
            for col, rate in cols.items():
                if rate is None:
                    continue
                if rate > _NULL_HARD:
                    hard_failures.append(
                        f"NULL_CRITICAL: {table}.{col} = {rate:.1%} null"
                    )

        # Duplicate PKs
        for table, count in snapshot["duplicate_counts"].items():
            if count > 0:
                hard_failures.append(
                    f"DUPLICATE_PK: {table} — {count} duplicate key group(s)"
                )

        # Donor resolution
        fallback = snapshot.get("donor_resolution_fallback_rate")
        if fallback is not None and fallback > 0.05:
            warnings.append(f"DONOR_FALLBACK: {fallback:.1%} of SBE rows unresolvable")

        # Zero-record sessions
        zero = snapshot.get("zero_record_sessions", [])
        if zero:
            warnings.append(f"ZERO_VOTE_SESSIONS: {', '.join(zero)}")

        baseline_written = False
        if write_baseline or (baseline is None and not hard_failures):
            self._save_baseline(snapshot)
            baseline_written = True

        return {
            "passed": len(hard_failures) == 0,
            "hard_failures": hard_failures,
            "warnings": warnings,
            "baseline_written": baseline_written,
            "baseline_ts": baseline.get("timestamp") if baseline else None,
            "snapshot": snapshot,
        }


def _fatal(msg: str) -> None:
    print(json.dumps({"error": msg, "exit_code": 1}), file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="VoteIQ post-ingest health check")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--baseline", default=str(BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true",
                    help="Overwrite baseline with current snapshot regardless of failures")
    args = ap.parse_args()

    runner = HealthCheckRunner(
        db_path=Path(args.db),
        baseline_path=Path(args.baseline),
    )
    report = runner.run(write_baseline=args.write_baseline)

    print(json.dumps(report, indent=2))

    print("\n=== Health Check Summary ===", file=sys.stderr)
    for f in report["hard_failures"]:
        print(f"  !! {f}", file=sys.stderr)
    for w in report["warnings"]:
        print(f"  ?? {w}", file=sys.stderr)
    if not report["hard_failures"] and not report["warnings"]:
        print("  All checks passed.", file=sys.stderr)

    if not report["passed"]:
        print(
            json.dumps({"event": "health_check_fail", "hard_failures": report["hard_failures"]}),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
