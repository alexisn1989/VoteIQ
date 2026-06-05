"""
run_spike_watchlist.py

Compares the latest donor_cycle_trends data against user-defined watchlist
entries and writes triggered alerts to spike_alerts.

Run after every SBE ingest:
    python run_spike_watchlist.py
    python run_spike_watchlist.py --cycle 2025   # check specific cycle only
    python run_spike_watchlist.py --dry-run       # print alerts, don't write

The watchlist is seeded on first run with sensible default watches
(Energy sector >2x, major known donors >1.5x).  Users can add more via the
/api/watchlist endpoints.

Schema (polls.db):
  spike_watchlist  — what to watch
  spike_alerts     — triggered alerts (append-only log)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB = BASE_DIR / "polls.db"

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spike_watchlist (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    label            TEXT NOT NULL,               -- human name e.g. "Energy sector >2x"
    donor_key        TEXT,                        -- NULL = match all in sector
    donor_name_match TEXT,                        -- LIKE pattern e.g. '%Dominion%'
    sector           TEXT,                        -- NULL = any sector
    threshold_ratio  REAL    DEFAULT 2.0,         -- ratio_vs_mean trigger threshold
    parity           TEXT    DEFAULT 'both',      -- 'odd' | 'even' | 'both'
    min_amount       REAL    DEFAULT 100000,      -- ignore tiny donors
    active           INTEGER DEFAULT 1,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS spike_alerts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id     INTEGER,
    watch_label      TEXT,
    donor_key        TEXT,
    canonical_name   TEXT,
    election_cycle   TEXT,
    cycle_parity     TEXT,
    total_amount     REAL,
    ratio_vs_mean    REAL,
    pct_change_prior REAL,
    context_bills    TEXT,    -- JSON array of relevant bill titles
    alerted_at       TEXT,
    seen             INTEGER DEFAULT 0,
    UNIQUE(watchlist_id, donor_key, election_cycle)
);
CREATE INDEX IF NOT EXISTS idx_sa_cycle  ON spike_alerts(election_cycle);
CREATE INDEX IF NOT EXISTS idx_sa_seen   ON spike_alerts(seen);
CREATE INDEX IF NOT EXISTS idx_sa_watch  ON spike_alerts(watchlist_id);
"""

# ── Default watchlist entries (seeded on first run) ───────────────────────────

DEFAULT_WATCHES: list[dict] = [
    {
        "label":           "Energy sector >2x baseline",
        "donor_key":       None,
        "donor_name_match": "%energy%|%electric%|%dominion%|%appalachian power%|%solar%",
        "sector":          None,
        "threshold_ratio": 2.0,
        "parity":          "both",
        "min_amount":      100_000,
    },
    {
        "label":           "Dominion Energy (all entities) >1.5x",
        "donor_key":       None,
        "donor_name_match": "%dominion%",
        "sector":          None,
        "threshold_ratio": 1.5,
        "parity":          "both",
        "min_amount":      500_000,
    },
    {
        "label":           "Clean Virginia Fund >1.5x",
        "donor_key":       None,
        "donor_name_match": "%clean virginia%",
        "sector":          None,
        "threshold_ratio": 1.5,
        "parity":          "both",
        "min_amount":      200_000,
    },
    {
        "label":           "Alcohol/Gambling sector >2x baseline",
        "donor_key":       None,
        "donor_name_match": "%casino%|%gaming%|%mgm%|%caesars%|%hard rock%|%cannabis%",
        "sector":          None,
        "threshold_ratio": 2.0,
        "parity":          "both",
        "min_amount":      100_000,
    },
    {
        "label":           "Any institutional donor >3x baseline",
        "donor_key":       None,
        "donor_name_match": None,   # all donors
        "sector":          None,
        "threshold_ratio": 3.0,
        "parity":          "both",
        "min_amount":      500_000,
    },
    {
        "label":           "Healthcare sector >2x baseline",
        "donor_key":       None,
        "donor_name_match": "%health%|%hospital%|%pharma%|%medical%",
        "sector":          None,
        "threshold_ratio": 2.0,
        "parity":          "both",
        "min_amount":      100_000,
    },
]


# ── Setup ─────────────────────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert default watches if the table is empty."""
    n = conn.execute("SELECT COUNT(*) FROM spike_watchlist").fetchone()[0]
    if n > 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    for w in DEFAULT_WATCHES:
        conn.execute("""
            INSERT INTO spike_watchlist
                (label, donor_key, donor_name_match, sector,
                 threshold_ratio, parity, min_amount, active, created_at)
            VALUES (?,?,?,?,?,?,?,1,?)
        """, (
            w["label"], w.get("donor_key"), w.get("donor_name_match"),
            w.get("sector"), w["threshold_ratio"], w["parity"],
            w["min_amount"], now,
        ))
    conn.commit()
    print(f"  Seeded {len(DEFAULT_WATCHES)} default watch entries.")


# ── Donor matching ────────────────────────────────────────────────────────────

def _matches_pattern(name: str, pattern: str | None) -> bool:
    """
    Match canonical_name against a pipe-separated LIKE pattern string.
    e.g. '%dominion%|%appalachian%' matches either word.
    None pattern matches everything.
    """
    if not pattern:
        return True
    name_lower = (name or "").lower()
    for part in pattern.split("|"):
        like = part.strip().lstrip("%").rstrip("%").lower()
        if like and like in name_lower:
            return True
    return False


# ── Core detection ────────────────────────────────────────────────────────────

def run_watchlist(
    conn: sqlite3.Connection,
    cycle_filter: str | None = None,
    dry_run: bool = False,
) -> int:
    """
    For each active watchlist entry, find donor_cycle_trends rows that:
    - Match the donor_name_match pattern
    - Have ratio_vs_mean >= threshold_ratio
    - Have total_amount >= min_amount
    - Are in the specified cycle (or the most recent cycle if None)
    """
    watches = conn.execute(
        "SELECT * FROM spike_watchlist WHERE active = 1"
    ).fetchall()
    watches = [dict(r) for r in watches]

    # Determine cycle to check
    if cycle_filter:
        cycles_to_check = [cycle_filter]
    else:
        # Most recent cycle in donor_cycle_trends
        row = conn.execute(
            "SELECT election_cycle FROM donor_cycle_trends ORDER BY election_cycle DESC LIMIT 1"
        ).fetchone()
        cycles_to_check = [row[0]] if row else []

    if not cycles_to_check:
        print("  No cycles to check.")
        return 0

    print(f"  Checking cycles: {cycles_to_check}")

    alert_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for cycle in cycles_to_check:
        # Load all trends for this cycle
        trends = conn.execute("""
            SELECT donor_key, canonical_name, election_cycle, cycle_parity,
                   total_amount, ratio_vs_mean, pct_change_prior, is_spike
            FROM donor_cycle_trends
            WHERE election_cycle = ? AND is_individual = 0
              AND ratio_vs_mean IS NOT NULL
        """, (cycle,)).fetchall()
        trends = [dict(t) for t in trends]

        for watch in watches:
            wid       = watch["id"]
            threshold = watch["threshold_ratio"]
            min_amt   = watch["min_amount"] or 0
            parity    = watch["parity"] or "both"
            pattern   = watch.get("donor_name_match")

            for t in trends:
                # Amount filter
                if t["total_amount"] < min_amt:
                    continue
                # Parity filter
                if parity != "both" and t["cycle_parity"] != parity:
                    continue
                # Ratio filter
                if (t["ratio_vs_mean"] or 0) < threshold:
                    continue
                # Name pattern filter
                if not _matches_pattern(t["canonical_name"], pattern):
                    continue

                # Get context bills for this cycle
                ctx_bills: list[str] = []
                if not dry_run:
                    ctx_rows = conn.execute("""
                        SELECT title FROM cycle_context
                        WHERE election_cycle = ? AND significance = 'high'
                        ORDER BY donor_sector, title LIMIT 4
                    """, (cycle,)).fetchall()
                    ctx_bills = [r["title"][:70] for r in ctx_rows]

                ratio   = round(t["ratio_vs_mean"], 2)
                pct_chg = t["pct_change_prior"]

                if dry_run:
                    yr_type = "state-election yr" if t["cycle_parity"] == "odd" else "federal-election yr"
                    pct_s   = f", +{pct_chg:.0f}% vs prior" if pct_chg else ""
                    print(
                        f"  [ALERT] Watch '{watch['label']}': "
                        f"{t['canonical_name']} gave ${t['total_amount']:,.0f} "
                        f"in {cycle} ({yr_type}) — {ratio:.1f}x their baseline{pct_s}"
                    )
                else:
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO spike_alerts
                                (watchlist_id, watch_label, donor_key, canonical_name,
                                 election_cycle, cycle_parity, total_amount,
                                 ratio_vs_mean, pct_change_prior, context_bills, alerted_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            wid, watch["label"],
                            t["donor_key"], t["canonical_name"],
                            cycle, t["cycle_parity"], t["total_amount"],
                            ratio, pct_chg,
                            json.dumps(ctx_bills), now,
                        ))
                        alert_count += 1
                    except sqlite3.IntegrityError:
                        pass  # already alerted

    if not dry_run:
        conn.commit()

    return alert_count


# ── Summary ───────────────────────────────────────────────────────────────────

def _print_alerts(conn: sqlite3.Connection, n: int = 20) -> None:
    rows = conn.execute("""
        SELECT watch_label, canonical_name, election_cycle,
               total_amount, ratio_vs_mean, pct_change_prior, alerted_at
        FROM spike_alerts
        ORDER BY alerted_at DESC, ratio_vs_mean DESC
        LIMIT ?
    """, (n,)).fetchall()

    if not rows:
        print("  No alerts on record.")
        return

    print(f"\nLatest {min(n, len(rows))} alerts:")
    print(f"  {'Watch':<35} {'Donor':<30} {'Cycle':>6} {'Amount':>10} {'Ratio':>7}")
    for r in rows:
        pct_s = f"+{r['pct_change_prior']:.0f}%" if r["pct_change_prior"] else ""
        print(
            f"  {r['watch_label'][:35]:<35} "
            f"{r['canonical_name'][:30]:<30} "
            f"{r['election_cycle']:>6}  "
            f"${r['total_amount']/1e6:>6.2f}M  "
            f"{r['ratio_vs_mean']:>5.1f}x  {pct_s}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main(cycle: str | None, dry_run: bool) -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    _seed_defaults(conn)

    n_watches = conn.execute(
        "SELECT COUNT(*) FROM spike_watchlist WHERE active=1"
    ).fetchone()[0]
    print(f"Running watchlist ({n_watches} active watches)…")

    n = run_watchlist(conn, cycle_filter=cycle, dry_run=dry_run)

    if dry_run:
        print(f"\n  Dry run — {n} alerts would have been written.")
    else:
        total = conn.execute("SELECT COUNT(*) FROM spike_alerts").fetchone()[0]
        new   = conn.execute(
            "SELECT COUNT(*) FROM spike_alerts WHERE seen = 0"
        ).fetchone()[0]
        print(f"\n  {n} new alert rows written. "
              f"{new} unseen alerts, {total} total on record.")
        _print_alerts(conn)

    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cycle",    default=None, help="Specific cycle to check (default: latest)")
    p.add_argument("--dry-run",  action="store_true", help="Print alerts without writing")
    args = p.parse_args()
    main(args.cycle, args.dry_run)
