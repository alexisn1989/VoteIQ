"""
build_donor_cycle_trends.py

For each significant VA campaign donor, compute per-cycle donation stats
and detect anomalous spending spikes — the "investment return" signal.

Key design: z-scores computed within parity group (odd-year vs even-year)
separately, because VA odd-year elections produce 5-50x more activity.

Writes to polls.db tables:
  donor_cycle_trends   — per (donor_key, cycle) stats
  donor_entity_map     — normalized key → canonical name + variants

Usage:
    python build_donor_cycle_trends.py
    python build_donor_cycle_trends.py --min-total 25000 --min-cycles 2
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR  = Path(__file__).resolve().parent
POLLS_DB  = BASE_DIR / "polls.db"

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_TOTAL_DEFAULT   = 50_000    # total donations across all cycles
MIN_CYCLES_DEFAULT  = 3         # same-parity active cycles to compute z-score
SPIKE_Z_THRESHOLD   = 1.5       # z-score above which we flag a spike
CYCLE_START         = "2012"    # pre-2012 data too sparse (<500 rows/year)
CYCLE_END           = "2026"

# ── Donor name normalisation ──────────────────────────────────────────────────
# Strip these from the END of names before extracting a brand key
_TAIL_STRIP = re.compile(
    r"""[\s,]*               # optional leading space/comma
    (
      political\s+action\s+committee |
      action\s+committee       |
      political\s+committee    |
      committee                |
      pac\b                    |
      fund\b                   |
      foundation\b             |
      association\b            |
      assoc\.?\b               |
      ,?\s+inc\.?              |
      ,?\s+llc\.?              |
      ,?\s+corp\.?             |
      ,?\s+co\.?               |
      ,?\s+ltd\.?
    )$""",
    re.VERBOSE | re.IGNORECASE,
)

# Strip these from the FRONT
_HEAD_STRIP = re.compile(
    r"^(the\s+|virginia\s+|va\s+)", re.IGNORECASE
)

# Punctuation normalisation
_PUNCT = re.compile(r"[^\w\s]")


def _donor_key(raw: str) -> str:
    """
    Normalise a donor name to a grouping key.

    'Dominion PAC'                    → 'dominion'
    'Dominion Energy PAC'             → 'dominion energy'
    'Dominion Political Action Comm.' → 'dominion'
    'Clean Virginia Fund'             → 'clean virginia'
    'Everytown for Gun Safety Action' → 'everytown'
    """
    s = raw.strip()
    # Iteratively strip tail suffixes (some names stack them)
    for _ in range(4):
        s2 = _TAIL_STRIP.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    s = _HEAD_STRIP.sub("", s).strip()
    s = _PUNCT.sub("", s).lower()
    words = s.split()
    # Drop trailing stop words that might remain
    while words and words[-1] in {"for", "and", "of", "the", "a", "an"}:
        words.pop()
    # Take up to 3 meaningful words
    key_words = [w for w in words[:3] if len(w) > 1]
    return "_".join(key_words) if key_words else raw.lower()[:20]


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS donor_cycle_trends (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_key        TEXT    NOT NULL,
    canonical_name   TEXT,
    election_cycle   TEXT    NOT NULL,
    cycle_parity     TEXT    NOT NULL,   -- 'odd' or 'even'
    is_individual    INTEGER DEFAULT 0,  -- 1 if majority of gifts are from individuals
    total_amount     REAL    DEFAULT 0,
    gift_count       INTEGER DEFAULT 0,
    recipient_count  INTEGER DEFAULT 0,

    -- Within-parity z-score vs this donor's own history
    zscore           REAL,
    -- % change vs prior same-parity cycle (NULL if first cycle)
    pct_change_prior REAL,
    -- Ratio vs donor's own same-parity mean
    ratio_vs_mean    REAL,

    is_spike         INTEGER DEFAULT 0,  -- 1 if zscore >= SPIKE_Z_THRESHOLD
    spike_magnitude  REAL,               -- zscore - threshold (0 if not spike)

    UNIQUE(donor_key, election_cycle)
);
CREATE INDEX IF NOT EXISTS idx_dct_key     ON donor_cycle_trends(donor_key);
CREATE INDEX IF NOT EXISTS idx_dct_cycle   ON donor_cycle_trends(election_cycle);
CREATE INDEX IF NOT EXISTS idx_dct_spike   ON donor_cycle_trends(is_spike, zscore DESC);
CREATE INDEX IF NOT EXISTS idx_dct_amount  ON donor_cycle_trends(total_amount DESC);

CREATE TABLE IF NOT EXISTS donor_entity_map (
    donor_key        TEXT PRIMARY KEY,
    canonical_name   TEXT NOT NULL,
    variant_names    TEXT,               -- JSON array of raw name variants
    first_cycle      TEXT,
    last_cycle       TEXT,
    total_all_cycles REAL,
    active_cycles    INTEGER,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_dem_canonical ON donor_entity_map(canonical_name);
"""


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    # Migrate: add is_individual if the table existed before this column was added
    existing = {r[1] for r in conn.execute("PRAGMA table_info(donor_cycle_trends)")}
    if "is_individual" not in existing:
        conn.execute("ALTER TABLE donor_cycle_trends ADD COLUMN is_individual INTEGER DEFAULT 0")
    conn.commit()


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_raw(conn: sqlite3.Connection) -> dict[str, dict[str, list[float]]]:
    """
    Load va_cf_schedule_a → {donor_key → {cycle → [amounts]}}
    Filtered to CYCLE_START–CYCLE_END.
    """
    rows = conn.execute(
        """
        SELECT last_or_company, election_cycle, amount
        FROM va_cf_schedule_a
        WHERE election_cycle >= ? AND election_cycle <= ?
          AND amount > 0
          AND last_or_company IS NOT NULL
          AND last_or_company != ''
        """,
        (CYCLE_START, CYCLE_END),
    ).fetchall()

    data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for raw_name, cycle, amount in rows:
        key = _donor_key(raw_name)
        data[key][cycle].append(amount)
    return data


def _load_individual_flags(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """
    Return {donor_key → {cycle → is_individual}} where 1 means the majority
    of gifts in that cycle came from individual donors (is_individual=1).
    """
    rows = conn.execute(
        """
        SELECT last_or_company, election_cycle,
               SUM(CASE WHEN is_individual = 1 THEN 1 ELSE 0 END) as ind_count,
               COUNT(*) as total_count
        FROM va_cf_schedule_a
        WHERE election_cycle >= ? AND election_cycle <= ?
          AND last_or_company IS NOT NULL AND last_or_company != ''
        GROUP BY last_or_company, election_cycle
        """,
        (CYCLE_START, CYCLE_END),
    ).fetchall()

    flags: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for raw_name, cycle, ind_count, total_count in rows:
        key = _donor_key(raw_name)
        # Accumulate across entity variants — take majority vote
        flags[key][cycle] += ind_count
    # We'll resolve majority vs total in main() once we know total gift counts
    return flags  # raw ind_count sums; divide by gift_count in main()


def _load_names(conn: sqlite3.Connection) -> dict[str, dict[str, list[str]]]:
    """
    Load {donor_key → {cycle → [raw_names]}} for canonical name selection.
    """
    rows = conn.execute(
        """
        SELECT last_or_company, election_cycle
        FROM va_cf_schedule_a
        WHERE election_cycle >= ? AND election_cycle <= ?
          AND last_or_company IS NOT NULL AND last_or_company != ''
        GROUP BY last_or_company, election_cycle
        """,
        (CYCLE_START, CYCLE_END),
    ).fetchall()

    names: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for raw_name, cycle in rows:
        key = _donor_key(raw_name)
        names[key][cycle].append(raw_name)
    return names


def _load_recipients(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """
    Load {donor_key → {cycle → unique_recipient_count}}.
    """
    rows = conn.execute(
        """
        SELECT last_or_company, election_cycle,
               COUNT(DISTINCT candidate_name) as n
        FROM va_cf_schedule_a
        WHERE election_cycle >= ? AND election_cycle <= ?
          AND last_or_company IS NOT NULL AND last_or_company != ''
        GROUP BY last_or_company, election_cycle
        """,
        (CYCLE_START, CYCLE_END),
    ).fetchall()

    recipients: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for raw_name, cycle, n in rows:
        key = _donor_key(raw_name)
        recipients[key][cycle] += n
    return recipients


# ── Statistics helpers ────────────────────────────────────────────────────────

def _parity(cycle: str) -> str:
    return "odd" if int(cycle) % 2 == 1 else "even"


def _zscore_and_stats(
    sorted_cycles: list[str],
    cycle_totals: dict[str, float],
    parity: str,
) -> dict[str, dict]:
    """
    For each cycle, compute within-parity z-score, pct_change, ratio_vs_mean.
    Returns {cycle → {zscore, pct_change, ratio_vs_mean}}.
    """
    # All same-parity cycles for this donor (where amount > 0)
    parity_cycles = sorted(
        c for c in sorted_cycles if _parity(c) == parity and cycle_totals.get(c, 0) > 0
    )
    parity_amounts = [cycle_totals[c] for c in parity_cycles]

    if len(parity_amounts) < 2:
        mean = parity_amounts[0] if parity_amounts else 0
        stdev = 0.0
    else:
        mean  = statistics.mean(parity_amounts)
        stdev = statistics.stdev(parity_amounts)

    results = {}
    prev_same_parity: str | None = None

    for cycle in parity_cycles:
        amt = cycle_totals[cycle]
        # z-score
        z = (amt - mean) / stdev if stdev > 0 else 0.0
        # pct change vs prior same-parity cycle
        pct = None
        if prev_same_parity is not None:
            prev_amt = cycle_totals[prev_same_parity]
            if prev_amt > 0:
                pct = round((amt - prev_amt) / prev_amt * 100, 1)
        # ratio vs mean
        ratio = round(amt / mean, 2) if mean > 0 else None

        results[cycle] = {
            "zscore":           round(z, 3),
            "pct_change_prior": pct,
            "ratio_vs_mean":    ratio,
        }
        prev_same_parity = cycle

    return results


# ── Canonical name selection ──────────────────────────────────────────────────

def _canonical(names_by_cycle: dict[str, list[str]], totals: dict[str, float]) -> tuple[str, list[str]]:
    """
    Pick the canonical display name (most-used variant weighted by donation size)
    and return all unique variant names.
    """
    name_weight: dict[str, float] = defaultdict(float)
    for cycle, cycle_names in names_by_cycle.items():
        cycle_amt = totals.get(cycle, 0)
        for n in cycle_names:
            name_weight[n] += cycle_amt / max(len(cycle_names), 1)

    canonical = max(name_weight, key=lambda n: name_weight[n])
    variants   = sorted(set(n for names in names_by_cycle.values() for n in names))
    return canonical, variants


# ── Main ──────────────────────────────────────────────────────────────────────

def main(min_total: float, min_cycles: int) -> None:
    conn = sqlite3.connect(POLLS_DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)

    print("Loading donation data from va_cf_schedule_a …")
    raw     = _load_raw(conn)
    names   = _load_names(conn)
    recips  = _load_recipients(conn)
    ind_raw = _load_individual_flags(conn)
    print(f"  {len(raw):,} unique donor keys loaded")

    # Aggregate per-key per-cycle
    cycle_totals_by_key: dict[str, dict[str, float]] = {}
    cycle_gifts_by_key:  dict[str, dict[str, int]]   = {}

    for key, cycle_map in raw.items():
        cycle_totals_by_key[key] = {c: sum(v)      for c, v in cycle_map.items()}
        cycle_gifts_by_key[key]  = {c: len(v)      for c, v in cycle_map.items()}

    # Filter: min total and min same-parity cycles
    qualifying: list[str] = []
    for key, totals in cycle_totals_by_key.items():
        grand_total = sum(totals.values())
        if grand_total < min_total:
            continue
        active_odd  = sum(1 for c, a in totals.items() if _parity(c) == "odd"  and a > 0)
        active_even = sum(1 for c, a in totals.items() if _parity(c) == "even" and a > 0)
        if max(active_odd, active_even) < min_cycles:
            continue
        qualifying.append(key)

    print(f"  {len(qualifying):,} donors pass thresholds "
          f"(min_total=${min_total:,.0f}, min_cycles={min_cycles})")

    now = datetime.now(timezone.utc).isoformat()
    trend_rows = 0
    entity_rows = 0

    for key in qualifying:
        totals     = cycle_totals_by_key[key]
        gifts      = cycle_gifts_by_key[key]
        name_map   = names.get(key, {})
        recip_map  = recips.get(key, {})

        sorted_cycles = sorted(totals.keys())
        canonical_name, variants = _canonical(name_map, totals)

        # Compute stats for each parity separately
        odd_stats  = _zscore_and_stats(sorted_cycles, totals, "odd")
        even_stats = _zscore_and_stats(sorted_cycles, totals, "even")

        for cycle in sorted_cycles:
            amt       = totals[cycle]
            gift_cnt  = gifts.get(cycle, 0)
            recip_cnt = recip_map.get(cycle, 0)
            par       = _parity(cycle)
            stats     = (odd_stats if par == "odd" else even_stats).get(cycle, {})

            z     = stats.get("zscore", 0.0)
            pct   = stats.get("pct_change_prior")
            ratio = stats.get("ratio_vs_mean")

            is_spike  = 1 if z >= SPIKE_Z_THRESHOLD else 0
            spike_mag = round(z - SPIKE_Z_THRESHOLD, 3) if is_spike else 0.0

            # is_individual: 1 if majority of gifts in this cycle are from individuals
            ind_count = ind_raw.get(key, {}).get(cycle, 0)
            is_ind    = 1 if gift_cnt > 0 and ind_count / gift_cnt > 0.5 else 0

            conn.execute(
                """
                INSERT INTO donor_cycle_trends
                    (donor_key, canonical_name, election_cycle, cycle_parity,
                     is_individual,
                     total_amount, gift_count, recipient_count,
                     zscore, pct_change_prior, ratio_vs_mean,
                     is_spike, spike_magnitude)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(donor_key, election_cycle) DO UPDATE SET
                    canonical_name   = excluded.canonical_name,
                    cycle_parity     = excluded.cycle_parity,
                    is_individual    = excluded.is_individual,
                    total_amount     = excluded.total_amount,
                    gift_count       = excluded.gift_count,
                    recipient_count  = excluded.recipient_count,
                    zscore           = excluded.zscore,
                    pct_change_prior = excluded.pct_change_prior,
                    ratio_vs_mean    = excluded.ratio_vs_mean,
                    is_spike         = excluded.is_spike,
                    spike_magnitude  = excluded.spike_magnitude
                """,
                (key, canonical_name, cycle, par,
                 is_ind,
                 amt, gift_cnt, recip_cnt,
                 z, pct, ratio, is_spike, spike_mag),
            )
            trend_rows += 1

        # Entity map
        grand_total   = sum(totals.values())
        active_cycles = sum(1 for a in totals.values() if a > 0)
        conn.execute(
            """
            INSERT INTO donor_entity_map
                (donor_key, canonical_name, variant_names,
                 first_cycle, last_cycle, total_all_cycles, active_cycles, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(donor_key) DO UPDATE SET
                canonical_name   = excluded.canonical_name,
                variant_names    = excluded.variant_names,
                first_cycle      = excluded.first_cycle,
                last_cycle       = excluded.last_cycle,
                total_all_cycles = excluded.total_all_cycles,
                active_cycles    = excluded.active_cycles,
                updated_at       = excluded.updated_at
            """,
            (key, canonical_name, json.dumps(variants),
             sorted_cycles[0], sorted_cycles[-1],
             grand_total, active_cycles, now),
        )
        entity_rows += 1

        if entity_rows % 500 == 0:
            conn.commit()
            print(f"  … {entity_rows:,} donors processed")

    conn.commit()
    print(f"\nDone. {trend_rows:,} cycle rows, {entity_rows:,} entity rows written.")
    conn.close()

    _print_summary()


def _print_summary() -> None:
    conn = sqlite3.connect(POLLS_DB)

    # Top institutional spikes — filter to $500K+ to avoid individual-donor noise
    print("\nTop institutional spikes by z-score (z >= 1.5, amount >= $500K):")
    print(f"  {'Donor':<38} {'Cycle':>6} {'Amount':>10} {'z':>6} {'vs prior':>10} {'vs mean':>8}")
    rows = conn.execute("""
        SELECT canonical_name, election_cycle, total_amount,
               zscore, pct_change_prior, ratio_vs_mean
        FROM donor_cycle_trends
        WHERE is_spike = 1 AND total_amount >= 500000 AND is_individual = 0
        ORDER BY zscore DESC
        LIMIT 20
    """).fetchall()
    for r in rows:
        pct_s   = f"{r[4]:+.0f}%" if r[4] is not None else "  n/a"
        ratio_s = f"{r[5]:.1f}x"  if r[5] is not None else " n/a"
        print(f"  {r[0][:38]:<38} {r[1]:>6}  ${r[2]/1e6:>7.2f}M  "
              f"z={r[3]:>5.2f}  {pct_s:>8}  {ratio_s:>7}")

    # Dominion — aggregate all entity variants per cycle for the narrative view
    print("\nDominion Energy — aggregated across all entity variants:")
    rows2 = conn.execute("""
        SELECT election_cycle,
               SUM(total_amount) as total,
               cycle_parity,
               MAX(is_spike) as any_spike
        FROM donor_cycle_trends
        WHERE donor_key LIKE '%dominion%'
        GROUP BY election_cycle, cycle_parity
        ORDER BY election_cycle
    """).fetchall()
    odd_amts  = [r[1] for r in rows2 if r[2] == "odd"]
    even_amts = [r[1] for r in rows2 if r[2] == "even"]
    odd_mean  = statistics.mean(odd_amts)  if odd_amts  else 0
    even_mean = statistics.mean(even_amts) if even_amts else 0
    for r in rows2:
        base = odd_mean if r[2] == "odd" else even_mean
        ratio = r[1] / base if base else 0
        spike = "  *** SPIKE ***" if ratio >= 1.8 else ""
        print(f"  {r[0]} [{r[2][:3]}]  ${r[1]/1e6:>6.2f}M  "
              f"{ratio:>5.1f}x vs {r[2]}-yr mean  (${base/1e6:.2f}M){spike}")

    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--min-total",  type=float, default=MIN_TOTAL_DEFAULT,
                   help="Minimum total donations across all cycles (default: $50K)")
    p.add_argument("--min-cycles", type=int,   default=MIN_CYCLES_DEFAULT,
                   help="Min same-parity active cycles for z-score (default: 3)")
    args = p.parse_args()
    main(args.min_total, args.min_cycles)
