"""
drift_monitor.py

Ongoing observability for production truth degradation in VoteIQ.
Persists daily snapshots to drift_log.jsonl, computes 7-day rolling stats,
and fires a webhook alert when any metric deviates >2 standard deviations
from its rolling baseline.

Designed to run as a daily cron job on Render.

Usage:
    python drift_monitor.py
    python drift_monitor.py --webhook https://hooks.slack.com/...
    python drift_monitor.py --dry-run     # collect snapshot, skip write + webhook
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "polls.db")))
DRIFT_LOG = DATA_DIR / "drift_log.jsonl"
WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")

_WINDOW_DAYS = 7
_STDEV_THRESHOLD = 2.0

# Mirrors audit_data_integrity.py — physical ceiling for any VA chamber
_HOUSE_SIZE = 100


class DriftMonitor:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        log_path: Path = DRIFT_LOG,
        webhook_url: str = WEBHOOK_URL,
    ):
        self.db_path = db_path
        self.log_path = log_path
        self.webhook_url = webhook_url

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    # ── Snapshot collectors ────────────────────────────────────────────────────

    def _sbe_record_count(self, conn: sqlite3.Connection) -> int | None:
        if not self._table_exists(conn, "va_cf_schedule_a"):
            return None
        try:
            return conn.execute("SELECT COUNT(*) FROM va_cf_schedule_a").fetchone()[0]
        except Exception:
            return None

    def _entity_mismatch_rate(self, conn: sqlite3.Connection) -> float | None:
        """Fraction of SBE rows where donor name is unresolvable (both name fields empty)."""
        if not self._table_exists(conn, "va_cf_schedule_a"):
            return None
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM va_cf_schedule_a WHERE amount > 0"
            ).fetchone()[0]
            if total == 0:
                return 0.0
            unresolved = conn.execute(
                "SELECT COUNT(*) FROM va_cf_schedule_a "
                "WHERE amount > 0 "
                "AND (last_or_company IS NULL OR last_or_company = '') "
                "AND (first_name IS NULL OR first_name = '')"
            ).fetchone()[0]
            return unresolved / total
        except Exception:
            return None

    def _sql_zero_record_frequency(self, conn: sqlite3.Connection) -> float | None:
        """Fraction of recent vote sessions with zero rows — proxy for SQL returning empty results."""
        if not self._table_exists(conn, "va_legislator_recent_votes"):
            return None
        try:
            rows = conn.execute(
                "SELECT session, COUNT(*) AS cnt "
                "FROM va_legislator_recent_votes "
                "GROUP BY session"
            ).fetchall()
            if not rows:
                return 0.0
            zero_sessions = sum(1 for r in rows if r["cnt"] == 0)
            return zero_sessions / len(rows)
        except Exception:
            return None

    def _ingestion_anomalies(self, conn: sqlite3.Connection) -> dict:
        """Reuses audit_data_integrity.py checks: future dates, negative amounts, duplicate votes."""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        anomalies: dict = {}

        # Future-dated transactions (impossible ingest artifact)
        if self._table_exists(conn, "va_cf_schedule_a"):
            try:
                anomalies["future_dated_transactions"] = conn.execute(
                    "SELECT COUNT(*) FROM va_cf_schedule_a WHERE transaction_date > ?",
                    (today_str,),
                ).fetchone()[0]
            except Exception:
                pass

        # Negative amounts (data corruption signal)
        if self._table_exists(conn, "va_cf_schedule_a"):
            try:
                anomalies["negative_amounts"] = conn.execute(
                    "SELECT COUNT(*) FROM va_cf_schedule_a WHERE amount < 0"
                ).fetchone()[0]
            except Exception:
                pass

        # Duplicate vote rows — mirrors the 21,901-row duplicate fix.
        # motion is included because a legislator may cast separate procedural and
        # final-passage votes on the same bill in the same session (not duplicates).
        if self._table_exists(conn, "va_legislator_recent_votes"):
            try:
                anomalies["duplicate_vote_groups"] = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT bill_id, voter_name, session, motion, COUNT(*) AS cnt
                        FROM va_legislator_recent_votes
                        GROUP BY bill_id, voter_name, session, motion
                        HAVING cnt > 1
                    )
                """).fetchone()[0]
            except Exception:
                pass

        # Null spike in critical fields
        for table, col in [("va_cf_schedule_a", "amount"), ("legislators", "name")]:
            if self._table_exists(conn, table):
                try:
                    total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    if total > 0:
                        nulls = conn.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" IS NULL'
                        ).fetchone()[0]
                        anomalies[f"null_rate_{table}_{col}"] = round(nulls / total, 4)
                except Exception:
                    pass

        return anomalies

    def collect_snapshot(self) -> dict:
        snapshot: dict = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "db_path": str(self.db_path),
        }
        conn = self._connect()
        if conn is None:
            snapshot["error"] = f"DB not found at {self.db_path}"
            return snapshot
        try:
            snapshot["sbe_record_count"] = self._sbe_record_count(conn)
            snapshot["entity_mismatch_rate"] = self._entity_mismatch_rate(conn)
            snapshot["sql_zero_record_frequency"] = self._sql_zero_record_frequency(conn)
            snapshot["ingestion_anomalies"] = self._ingestion_anomalies(conn)
        finally:
            conn.close()
        return snapshot

    # ── Rolling window ─────────────────────────────────────────────────────────

    def _load_window(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        entries: list[dict] = []
        try:
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return entries[-_WINDOW_DAYS:]

    def _append_snapshot(self, snapshot: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot) + "\n")

    @staticmethod
    def _rolling_stats(window: list[dict], field: str) -> tuple[float | None, float | None]:
        vals = [e[field] for e in window if isinstance(e.get(field), (int, float))]
        if not vals:
            return None, None
        mean = sum(vals) / len(vals)
        if len(vals) < 2:
            return mean, None
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        return mean, math.sqrt(variance)

    # ── Deviation detection ────────────────────────────────────────────────────

    def _check_deviations(self, snapshot: dict, window: list[dict]) -> list[str]:
        alerts: list[str] = []
        metrics = ("sbe_record_count", "entity_mismatch_rate", "sql_zero_record_frequency")
        for field in metrics:
            current = snapshot.get(field)
            if current is None:
                continue
            mean, stdev = self._rolling_stats(window, field)
            if mean is None or stdev is None or stdev == 0:
                continue
            z = abs((current - mean) / stdev)
            if z > _STDEV_THRESHOLD:
                direction = "↑" if current > mean else "↓"
                alerts.append(
                    f"DRIFT {direction} {field}: z={z:.1f} "
                    f"(now={current}, 7d_avg={mean:.2f}, σ={stdev:.2f})"
                )
        return alerts

    # ── Anomaly alerts ─────────────────────────────────────────────────────────

    def _check_anomalies(self, snapshot: dict) -> list[str]:
        alerts: list[str] = []
        anomalies = snapshot.get("ingestion_anomalies", {})

        if anomalies.get("future_dated_transactions", 0) > 0:
            alerts.append(
                f"ANOMALY: {anomalies['future_dated_transactions']} future-dated transactions"
            )
        if anomalies.get("negative_amounts", 0) > 0:
            alerts.append(
                f"ANOMALY: {anomalies['negative_amounts']} negative-amount records"
            )
        if anomalies.get("duplicate_vote_groups", 0) > 0:
            alerts.append(
                f"ANOMALY: {anomalies['duplicate_vote_groups']} duplicate vote row groups"
            )
        for key, val in anomalies.items():
            if key.startswith("null_rate_") and isinstance(val, float) and val > 0.05:
                alerts.append(f"NULL_SPIKE: {key} = {val:.1%}")

        return alerts

    # ── Webhook ────────────────────────────────────────────────────────────────

    def _send_alert(self, alerts: list[str], snapshot: dict) -> bool:
        if not self.webhook_url:
            return False
        payload = {
            "text": f"VoteIQ drift alert ({snapshot['date']}): {len(alerts)} issue(s)",
            "alerts": alerts,
            "snapshot_date": snapshot["date"],
            "sbe_record_count": snapshot.get("sbe_record_count"),
            "entity_mismatch_rate": snapshot.get("entity_mismatch_rate"),
        }
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 300
        except Exception as exc:
            print(f"[drift_monitor] webhook error: {exc}", file=sys.stderr)
            return False

    # ── Main entry ─────────────────────────────────────────────────────────────

    def run_daily_check(self, dry_run: bool = False) -> dict:
        snapshot = self.collect_snapshot()
        window = self._load_window()

        alerts = self._check_deviations(snapshot, window)
        alerts += self._check_anomalies(snapshot)

        snapshot["alerts"] = alerts
        snapshot["window_size"] = len(window)

        webhook_sent = False
        if not dry_run:
            self._append_snapshot(snapshot)
            if alerts and self.webhook_url:
                webhook_sent = self._send_alert(alerts, snapshot)

        return {
            "date": snapshot["date"],
            "alerts": alerts,
            "alert_count": len(alerts),
            "window_size": len(window),
            "webhook_sent": webhook_sent,
            "dry_run": dry_run,
            "snapshot": snapshot,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="VoteIQ drift monitor — daily check")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--log", default=str(DRIFT_LOG))
    ap.add_argument("--webhook", default=WEBHOOK_URL)
    ap.add_argument("--dry-run", action="store_true", help="Collect snapshot but skip write and webhook")
    args = ap.parse_args()

    monitor = DriftMonitor(
        db_path=Path(args.db),
        log_path=Path(args.log),
        webhook_url=args.webhook,
    )
    result = monitor.run_daily_check(dry_run=args.dry_run)

    print(json.dumps(result, indent=2))

    print("\n=== Drift Monitor Summary ===", file=sys.stderr)
    if result["alerts"]:
        for a in result["alerts"]:
            print(f"  !! {a}", file=sys.stderr)
    else:
        print("  No drift detected.", file=sys.stderr)


if __name__ == "__main__":
    main()
