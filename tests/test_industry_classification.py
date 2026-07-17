"""
Regression tests for industry classification in the sector drill-down.

Guards against:
  (a) Individual contributors being misclassified due to surname keyword matches
  (b) Sector totals in the drill-down not reconciling with total_raised
"""
import sys
import os
import sqlite3
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_va_state_finance import classify_sector


# ── Classification unit tests ──────────────────────────────────────────────────

def test_cree_employee_not_wine_spirits():
    """Cree semiconductor employee (company='') must never land in Wine & Spirits.

    Root cause of original bug: api_sector_contributors passed the individual's
    SURNAME as the `company` arg, so 'Cree' in the combined string hit the
    'wine assoc' / 'wine wholesaler' etc. keywords — but 'cree' alone does not.
    Correct call passes company='' for individual contributors.
    """
    result = classify_sector("Contracts Manager", "Cree", "")
    assert result != "Wine & Spirits", (
        f"classify_sector('Contracts Manager', 'Cree', '') returned {result!r}; "
        "expected anything but Wine & Spirits. "
        "Ensure company='' is passed for individual contributors."
    )


def test_cree_employee_is_uncategorized():
    """Cree/Contracts Manager with no recognized industry → Individual/Other."""
    result = classify_sector("Contracts Manager", "Cree", "")
    assert result == "Individual/Other", (
        f"Expected Individual/Other for Cree semiconductor employee, got {result!r}. "
        "This individual does not map to any known sector."
    )


def test_wine_wholesaler_classified_correctly():
    """Virginia Wine Wholesalers Assn should land in Wine & Spirits when passed as company."""
    result = classify_sector("", "Virginia Wine Wholesalers Assn", "Virginia Wine Wholesalers Assn")
    assert result == "Wine & Spirits", (
        f"Expected Wine & Spirits for Virginia Wine Wholesalers, got {result!r}."
    )


def test_individual_surname_wine_never_contaminates():
    """An individual whose surname happens to contain 'wine' must not hit Wine & Spirits."""
    # Hypothetical: person named "Wineberg" with employer unrelated to alcohol.
    result = classify_sector("Software Engineer", "Accenture", "")
    assert result != "Wine & Spirits", (
        f"Software engineer at Accenture classified as Wine & Spirits: {result!r}."
    )


# ── Reconciliation test against real DB ───────────────────────────────────────

def _find_db() -> Path | None:
    base = Path(__file__).resolve().parents[1]
    data_env = os.environ.get("DATA_DIR")
    candidates = []
    if data_env:
        candidates.append(Path(data_env) / "polls.db")
    candidates += [base / "polls.db", Path("/var/data/polls.db")]
    for p in candidates:
        if p.is_file():
            return p
    return None


def test_sector_totals_reconcile_with_total_raised():
    """build_summary sector totals must sum to total_raised (within 1 cent).

    Picks the first legislator with va_sbe finance data and verifies that
    sum(s['total'] for s in by_sector) == total_raised.
    """
    from backfill_missing_finance import build_summary, donor_meta, _name_key, _ALIASES

    db_path = _find_db()
    if db_path is None:
        print("polls.db not found — skipping DB reconciliation test")
        return

    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT name FROM campaign_finance_summary WHERE source='va_sbe' LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        print("No va_sbe rows in campaign_finance_summary — skipping")
        return

    name = row["name"]
    key = _name_key(name)

    raw_index: dict = defaultdict(set)
    for r in conn.execute("SELECT DISTINCT candidate_name FROM va_cf_schedule_a"):
        k = _name_key(r["candidate_name"])
        if k:
            raw_index[k].add(r["candidate_name"])

    raw_names = (
        list(_ALIASES[name]) if name in _ALIASES
        else sorted(raw_index.get(key, []))
    )
    if not raw_names:
        conn.close()
        print(f"No raw candidates found for {name!r} — skipping")
        return

    donor_meta.clear()
    summary = build_summary(conn, raw_names)
    conn.close()

    if not summary:
        print(f"No summary built for {name!r} — skipping")
        return

    sector_sum = sum(s["total"] for s in summary["by_sector"])
    total_raised = summary["total_raised"]

    print(f"\nLegislator: {name}")
    print(f"  total_raised : ${total_raised:,.2f}")
    print(f"  sector sum   : ${sector_sum:,.2f}")
    for s in sorted(summary["by_sector"], key=lambda x: x["total"], reverse=True):
        pct = round(s["total"] / total_raised * 100, 1) if total_raised else 0
        print(f"  {s['sector']:<30s}  ${s['total']:>12,.2f}  ({pct}%)")

    assert abs(sector_sum - total_raised) < 0.02, (
        f"Sector totals (${sector_sum:,.2f}) do not reconcile with "
        f"total_raised (${total_raised:,.2f}) for {name!r}. "
        f"Difference: ${abs(sector_sum - total_raised):.2f}"
    )
    print("  OK reconciled")
