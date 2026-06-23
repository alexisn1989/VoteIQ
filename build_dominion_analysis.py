#!/usr/bin/env python3
"""Build Dominion Energy legislative influence analysis.

Identifies donations specifically from Dominion Energy / Dominion Resources /
Dominion Power (the electric utility), cross-references with VA legislators'
votes on key energy/utility bills, and computes a per-bill donor funding gap.
Median Dominion $ is the primary metric; mean is supplemental.

Writes cache["state"]["dominion_analysis"] with:
  - stats:          headline numbers
  - per_bill:       per-bill funding gap (YES vs NO voter Dominion $)
  - legislators:    per-legislator Dominion $, party, energy votes
  - key_bills:      metadata for each tracked bill
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

BASE_DIR   = Path(__file__).resolve().parent
DB_PATH    = BASE_DIR / "polls.db"
CACHE_FILE = BASE_DIR / "data" / "donor_map_cache.json"

log = logging.getLogger(__name__)

# ── Methodology Version ───────────────────────────────────────────────────────
# Increment version, date, and changelog on every future methodology change.
FINANCE_METHODOLOGY_VERSION = "2.0"
FINANCE_METHODOLOGY_DATE    = "2026-06-19"
FINANCE_METHODOLOGY_AUTHOR  = "VoteIQ"
FINANCE_METHODOLOGY_CHANGELOG: dict[str, list[str]] = {
    "2.0": [
        "median-first reporting",
        "concentration metrics with tiered warnings",
        "statistical quality score with sample-size cap",
        "funding share band classification",
        "raw vs display value separation",
        "safe_ratio() division guard",
        "schema versioning on all cache objects",
    ],
    "1.0": [
        "initial release: mean-based donor-vote comparisons",
    ],
}

CACHE_SCHEMA_VERSION = "finance_cache_v2"

# ── Dominion utility employer filter ─────────────────────────────────────────
# Conservative: must match INCLUDE pattern AND not match EXCLUDE pattern.
_DOM_INCLUDE = re.compile(
    r'dominion\s+(energy|resources?|power|virginia\s*power|va\s*power|'
    r'enery|engery|generation|transmissions?|electric\s*(?:co[-\s]?op|cooperat)|'
    r'resources\s*(?:inc|services)|energy\s*(?:inc|services|virginia)|'
    r'- power|-power)',
    re.IGNORECASE
)
_DOM_BARE     = re.compile(r'^dominion\s*(?:resources?\s*services,?\s*inc\.?)?$', re.IGNORECASE)
_OLD_DOM_ELEC = re.compile(r'old\s+dominion\s+electric', re.IGNORECASE)
_DOM_EXCLUDE  = re.compile(
    r'university|college|school|academy|auto|medical|patholog|hospital|'
    r'cardiol|anesthes|fertil|behavior|counsel|eye\s|heart|care\s|'
    r'capital\s|financial|wealth|law\s|legal|payroll|insulation|floor\s|'
    r'wrecking|precast|aviation|air\s*&|surveyor|realty|tire|truck|peanut|'
    r'boat\s|speedway|racing|atlantic\s+dominion|new\s+dominion|'
    r'container|parts\s+distribut|landmark|enterprises?|entertainment',
    re.IGNORECASE
)


def is_dominion_utility(employer: str) -> bool:
    emp = (employer or "").strip()
    if not emp:
        return False
    if _DOM_EXCLUDE.search(emp):
        return False
    if _DOM_INCLUDE.search(emp):
        return True
    if _DOM_BARE.match(emp):
        return True
    if _OLD_DOM_ELEC.search(emp):
        return True
    return False


# ── Key energy/utility bills to track ────────────────────────────────────────
KEY_BILLS = {
    "HB1487": "Underground transmission lines pilot (Dominion infrastructure)",
    "HB429":  "Electric utilities — integrated resource plans & rates",
    "SB249":  "Electric utilities — integrated resource plans & rates (Senate)",
    "HB153":  "Data centers — high-energy site assessment",
    "SB94":   "Data centers — high-energy site assessment (Senate)",
    "HB590":  "Smart Solar Permitting Platform",
    "SB382":  "Smart Solar Permitting Platform (Senate)",
    "HB84":   "Electric utilities & licensed suppliers — regulations",
    "SB777":  "Electric utilities & licensed suppliers — regulations (Senate)",
    "SB253":  "Electric utilities — pilot energy-assistance programs",
}


# ── Name normalisation (mirrors correlation builder) ─────────────────────────
_TITLE_RE  = re.compile(
    r'^(mr\.?|mrs\.?|ms\.?|dr\.?|hon\.?|gov\.?|sen\.?|del\.?|rep\.?|'
    r'rev\.?|prof\.?|gen\.?|sgt\.?|cpl\.?|col\.?)\s+',
    re.IGNORECASE,
)
_SUFFIX_RE = re.compile(r'\s+(jr\.?|sr\.?|ii|iii|iv|esq\.?)$', re.IGNORECASE)


def normalize_name(raw: str) -> tuple[str, str]:
    """Return (last_lower, first_initial) for fuzzy matching."""
    s = _TITLE_RE.sub("", (raw or "").strip())
    s = _SUFFIX_RE.sub("", s).strip()
    parts = s.split()
    if not parts:
        return ("", "")
    last = parts[-1].lower().rstrip(".,")
    first_initial = parts[0][0].lower() if parts[0] else ""
    return (last, first_initial)


# ── Safeguard functions ───────────────────────────────────────────────────────

def safe_ratio(numerator, denominator):
    """Division with zero/None guard. Returns None instead of ∞ or ZeroDivisionError.

    Replaces all inline division in donor-vote ratio calculations.
    Render None as "N/A" — never display ∞×, 9999×, or any division-by-zero artifact.
    """
    if denominator is None or denominator == 0:
        return None
    if numerator is None or numerator == 0:
        return None
    return numerator / denominator


def sample_size_grade(yes_n: int, no_n: int) -> str:
    """Grade capped by the weakest side (min of yes_n, no_n).

    A perfect attribution + perfect provenance + n=2 on either side = D.
    Final quality score cannot exceed this grade.
    """
    n = min(yes_n, no_n)
    if n >= 50: return "A"
    if n >= 30: return "B"
    if n >= 10: return "C"
    return "D"


def concentration_tier(top5_share_pct) -> str | None:
    """Tiered severity for funding concentration. Returns None when below 50% threshold."""
    if top5_share_pct is None:
        return None
    if top5_share_pct >= 90: return "Extreme"
    if top5_share_pct >= 75: return "Severe"
    if top5_share_pct >= 50: return "Moderate"
    return None


def funding_share_band(share_pct) -> str:
    """Classify donor share-of-total into a named interpretation tier."""
    if share_pct is None:
        return "unknown"
    if share_pct > 15: return "major"
    if share_pct >= 5: return "significant"
    if share_pct >= 1: return "modest"
    return "minimal"


# ── Display formatters ────────────────────────────────────────────────────────
# Renderer reads *_display fields only — it never formats or rounds raw values.

def _fmt_dollars(v) -> str:
    return "N/A" if v is None else f"${v:,.0f}"


def _fmt_pct(v, decimals: int = 1) -> str:
    return "N/A" if v is None else f"{v:.{decimals}f}%"


def _fmt_ratio(v) -> str:
    return "N/A" if v is None else f"{v:.1f}×"


# ── Statistical helpers ───────────────────────────────────────────────────────

def _avg(lst: list[float]) -> float:
    """Mean over funded-only (non-zero) entries. Kept for backwards-compatible display."""
    funded = [v for v in lst if v > 0]
    return round(sum(funded) / len(funded), 0) if funded else 0.0


def _median(lst: list[float]):
    """Median over ALL entries including zeros (includes unfunded legislators)."""
    if not lst:
        return None
    try:
        return round(statistics.median(lst), 0)
    except statistics.StatisticsError:
        return None


def _stddev(lst: list[float]):
    if len(lst) < 2:
        return None
    try:
        return round(statistics.stdev(lst), 0)
    except statistics.StatisticsError:
        return None


def _top5_share_pct(lst: list[float]):
    """Percentage of group total concentrated in the top 5 recipients."""
    total = sum(lst)
    if not lst or total == 0:
        return None
    top5 = sum(sorted(lst, reverse=True)[:5])
    return round(top5 / total * 100, 1)


# ── Pre-export validation ─────────────────────────────────────────────────────

def validate_per_bill(obj: dict, bill_id: str = "") -> list[str]:
    """Return list of validation failure strings. Empty list = pass. Failed records are rejected."""
    failures: list[str] = []
    ctx = f"per_bill[{bill_id}]"

    if obj.get("yes_count", 0) < 0 or obj.get("no_count", 0) < 0:
        failures.append(f"{ctx}: negative vote count")

    med_yes = obj.get("median_yes_dom")
    max_yes = obj.get("max_yes_dom") or 0
    if med_yes is not None and max_yes > 0 and med_yes > max_yes:
        failures.append(f"{ctx}: median_yes_dom={med_yes} exceeds max_yes_dom={max_yes}")

    med_no = obj.get("median_no_dom")
    max_no = obj.get("max_no_dom") or 0
    if med_no is not None and max_no > 0 and med_no > max_no:
        failures.append(f"{ctx}: median_no_dom={med_no} exceeds max_no_dom={max_no}")

    for field in ("top5_share_yes_pct", "top5_share_no_pct"):
        v = obj.get(field)
        if v is not None and not (0 <= v <= 100):
            failures.append(f"{ctx}: {field}={v} out of [0, 100]")

    for ratio_field in ("ratio_medians", "ratio"):
        v = obj.get(ratio_field)
        if v is not None and not math.isfinite(v):
            failures.append(f"{ctx}: {ratio_field} is non-finite ({v})")

    return failures


def validate_legislator(obj: dict, name: str = "") -> list[str]:
    """Return list of validation failure strings. Empty list = pass."""
    failures: list[str] = []
    ctx = f"legislator[{name}]"

    if obj.get("dom_total", 0) < 0:
        failures.append(f"{ctx}: negative dom_total")
    if obj.get("total_raised", 0) < 0:
        failures.append(f"{ctx}: negative total_raised")

    pct = obj.get("dom_share_pct")
    if pct is not None and not (0 <= pct <= 100):
        failures.append(f"{ctx}: dom_share_pct={pct} out of [0, 100]")

    return failures


# ── Build functions ───────────────────────────────────────────────────────────

def build_dominion_donations(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Return ({norm_key: total_dom_$}, {norm_key: {employer_strings}})."""
    rows = conn.execute(
        "SELECT employer, candidate_name, ROUND(SUM(amount),0) t "
        "FROM va_cf_schedule_a "
        "WHERE is_individual=1 AND employer IS NOT NULL "
        "GROUP BY employer, candidate_name"
    ).fetchall()

    key_total: dict[tuple, float]   = defaultdict(float)
    key_employers: dict[tuple, set] = defaultdict(set)

    for r in rows:
        if not is_dominion_utility(r["employer"]):
            continue
        key = normalize_name(r["candidate_name"])
        if key == ("", ""):
            continue
        key_total[key]     += float(r["t"] or 0)
        key_employers[key].add(r["employer"])

    return dict(key_total), dict(key_employers)


def build_legislator_votes(conn: sqlite3.Connection) -> dict[tuple, dict]:
    """Return {norm_key: {bill_id: option, ...}} for key bills."""
    rows = conn.execute(
        "SELECT voter_name, bill_id, option "
        "FROM va_legislator_recent_votes "
        "WHERE bill_id IN ({})".format(",".join("?" for _ in KEY_BILLS)),
        list(KEY_BILLS.keys()),
    ).fetchall()

    votes: dict[tuple, dict[str, str]] = defaultdict(dict)
    for r in rows:
        key = normalize_name(r["voter_name"])
        votes[key][r["bill_id"]] = r["option"]
    return dict(votes)


def build_legislator_info(conn: sqlite3.Connection) -> dict[tuple, dict]:
    """Return {norm_key: {name, party, chamber}} from va_legislator_vote_summary."""
    rows = conn.execute(
        "SELECT voter_name, party, chamber FROM va_legislator_vote_summary"
    ).fetchall()
    info: dict[tuple, dict] = {}
    for r in rows:
        key = normalize_name(r["voter_name"])
        info[key] = {
            "name":    r["voter_name"],
            "party":   r["party"] or "",
            "chamber": r["chamber"] or "",
        }
    return info


def build_total_fundraising(conn: sqlite3.Connection) -> dict[tuple, float]:
    """Sum all Schedule A contributions per candidate (proxy for total individual contributions).

    Used to compute Dominion share-of-total for each legislator.
    Includes all donors, not just Dominion-affiliated ones.
    """
    rows = conn.execute(
        "SELECT candidate_name, ROUND(SUM(amount),0) t "
        "FROM va_cf_schedule_a "
        "GROUP BY candidate_name"
    ).fetchall()
    result: dict[tuple, float] = defaultdict(float)
    for r in rows:
        key = normalize_name(r["candidate_name"])
        if key != ("", ""):
            result[key] += float(r["t"] or 0)
    return dict(result)


def compute_analysis(conn: sqlite3.Connection) -> dict:
    dom_total, _dom_emp  = build_dominion_donations(conn)
    leg_votes             = build_legislator_votes(conn)
    leg_info              = build_legislator_info(conn)
    total_raised_by_leg   = build_total_fundraising(conn)
    all_vote_keys         = set(leg_votes.keys())

    # ── Per-legislator records ────────────────────────────────────────────────
    legislators: list[dict] = []
    leg_validation_failures: list[str] = []

    for key in all_vote_keys:
        info        = leg_info.get(key, {})
        dom_dollars = dom_total.get(key, 0.0)
        votes       = leg_votes[key]

        voted_bills = {b: v for b, v in votes.items() if b in KEY_BILLS}
        yes_n       = sum(1 for v in voted_bills.values() if v == "yes")
        no_n        = sum(1 for v in voted_bills.values() if v == "no")
        total_votes = yes_n + no_n
        yes_rate    = round(yes_n / total_votes * 100, 1) if total_votes else None

        dom_score = None
        if dom_dollars > 0 and total_votes > 0 and yes_rate is not None:
            max_dom = max((v for v in dom_total.values()), default=1)
            log_max = math.log10(max_dom + 1)
            log_dom = math.log10(dom_dollars + 1)
            dom_score = round((yes_rate / 100) * (log_dom / log_max) * 100, 1)

        total_raised_amt  = total_raised_by_leg.get(key, 0.0)
        raw_share         = safe_ratio(dom_dollars, total_raised_amt)
        dom_share_pct     = round(raw_share * 100, 1) if raw_share is not None else None
        dom_band          = funding_share_band(dom_share_pct)

        rec = {
            "schema_version":        CACHE_SCHEMA_VERSION,
            "name":                  info.get("name", " ".join(key)),
            "party":                 info.get("party", ""),
            "chamber":               info.get("chamber", ""),
            # Raw values
            "dom_total":             round(dom_dollars, 0),
            "dom_funded":            dom_dollars > 0,
            "total_raised":          round(total_raised_amt, 0),
            "dom_share_pct":         dom_share_pct,
            "dom_share_band":        dom_band,
            # Display values
            "dom_total_display":     _fmt_dollars(round(dom_dollars, 0)),
            "total_raised_display":  _fmt_dollars(round(total_raised_amt, 0)),
            "dom_share_pct_display": _fmt_pct(dom_share_pct),
            # Vote data
            "energy_votes":          voted_bills,
            "energy_yes":            yes_n,
            "energy_no":             no_n,
            "yes_rate":              yes_rate,
            "dom_score":             dom_score,
        }

        failures = validate_legislator(rec, rec["name"])
        if failures:
            for f in failures:
                log.warning("Validation failure — record rejected: %s", f)
                leg_validation_failures.append(f)
        else:
            legislators.append(rec)

    legislators.sort(key=lambda x: -(x["dom_total"] or 0))

    # ── Per-bill funding gap ──────────────────────────────────────────────────
    per_bill: list[dict] = []
    bill_validation_failures: list[str] = []

    bill_meta: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT bill_number, title, status_label FROM legiscan_va_bills "
        "WHERE bill_number IN ({})".format(",".join("?" for _ in KEY_BILLS)),
        list(KEY_BILLS.keys()),
    ).fetchall()
    for r in rows:
        bill_meta[r["bill_number"]] = {"title": r["title"], "status": r["status_label"]}

    for bill_id, desc in KEY_BILLS.items():
        yes_dom: list[float] = []
        no_dom:  list[float] = []

        for leg in legislators:
            vote = leg["energy_votes"].get(bill_id)
            if not vote:
                continue
            dom = leg["dom_total"]
            if vote == "yes":
                yes_dom.append(dom)
            elif vote == "no":
                no_dom.append(dom)

        yes_n = len(yes_dom)
        no_n  = len(no_dom)

        avg_yes  = _avg(yes_dom)
        avg_no   = _avg(no_dom)
        med_yes  = _median(yes_dom)
        med_no   = _median(no_dom)
        std_yes  = _stddev(yes_dom)
        std_no   = _stddev(no_dom)
        max_yes  = max(yes_dom) if yes_dom else None
        max_no   = max(no_dom)  if no_dom  else None
        top5_yes = _top5_share_pct(yes_dom)
        top5_no  = _top5_share_pct(no_dom)
        funded_yes = sum(1 for v in yes_dom if v > 0)
        funded_no  = sum(1 for v in no_dom  if v > 0)

        raw_ratio_means   = safe_ratio(avg_yes, avg_no)
        raw_ratio_medians = safe_ratio(med_yes, med_no)
        ratio_means   = round(raw_ratio_means,   1) if raw_ratio_means   is not None else None
        ratio_medians = round(raw_ratio_medians, 1) if raw_ratio_medians is not None else None

        conc_yes      = concentration_tier(top5_yes)
        conc_no       = concentration_tier(top5_no)
        mean_skew_warn = bool(
            (med_yes is not None and avg_yes > 0 and avg_yes > med_yes * 3) or
            (med_no  is not None and avg_no  > 0 and avg_no  > med_no  * 3)
        )
        ss_grade = sample_size_grade(yes_n, no_n)
        meta     = bill_meta.get(bill_id, {})

        rec = {
            "schema_version":             CACHE_SCHEMA_VERSION,
            "bill_id":                    bill_id,
            "description":                desc,
            "title":                      meta.get("title", desc),
            "status":                     meta.get("status", ""),
            # Raw values
            "yes_count":                  yes_n,
            "no_count":                   no_n,
            "funded_yes":                 funded_yes,
            "funded_no":                  funded_no,
            "avg_yes_dom":                avg_yes,
            "avg_no_dom":                 avg_no,
            "median_yes_dom":             med_yes,
            "median_no_dom":              med_no,
            "stddev_yes_dom":             std_yes,
            "stddev_no_dom":              std_no,
            "max_yes_dom":                max_yes,
            "max_no_dom":                 max_no,
            "top5_share_yes_pct":         top5_yes,
            "top5_share_no_pct":          top5_no,
            "ratio":                      ratio_means,    # backwards compat (ratio of means)
            "ratio_medians":              ratio_medians,  # primary metric
            # Display values (renderer reads these only)
            "avg_yes_dom_display":        _fmt_dollars(avg_yes),
            "avg_no_dom_display":         _fmt_dollars(avg_no),
            "median_yes_dom_display":     _fmt_dollars(med_yes),
            "median_no_dom_display":      _fmt_dollars(med_no),
            "top5_share_yes_pct_display": _fmt_pct(top5_yes),
            "top5_share_no_pct_display":  _fmt_pct(top5_no),
            "ratio_means_display":        _fmt_ratio(ratio_means),
            "ratio_medians_display":      _fmt_ratio(ratio_medians),
            # Quality / warning flags
            "sample_size_grade":          ss_grade,
            "concentration_tier_yes":     conc_yes,
            "concentration_tier_no":      conc_no,
            "mean_skew_warning":          mean_skew_warn,
            "concentration_warning":      conc_yes is not None,  # backwards compat
        }

        failures = validate_per_bill(rec, bill_id)
        if failures:
            for f in failures:
                log.warning("Validation failure — record rejected: %s", f)
                bill_validation_failures.append(f)
        else:
            per_bill.append(rec)

    per_bill.sort(key=lambda x: -(x.get("ratio_medians") or x.get("ratio") or 0))

    # ── Stats ─────────────────────────────────────────────────────────────────
    funded_legs       = [l for l in legislators if l["dom_total"] > 0]
    total_dom_dollars = sum(v for v in dom_total.values())
    yes_70_legs       = [
        l for l in legislators
        if l.get("yes_rate") is not None and l["yes_rate"] >= 70
    ]
    avg_dom_yes_voter = (
        round(sum(l["dom_total"] for l in yes_70_legs) / len(yes_70_legs), 0)
        if yes_70_legs else 0
    )
    all_failures = bill_validation_failures + leg_validation_failures

    stats = {
        "schema_version":         CACHE_SCHEMA_VERSION,
        "total_dominion_dollars": round(total_dom_dollars, 0),
        "legislators_funded":     len(funded_legs),
        "legislators_analyzed":   len(legislators),
        "top_recipient":          funded_legs[0]["name"]      if funded_legs else "",
        "top_recipient_amount":   funded_legs[0]["dom_total"] if funded_legs else 0,
        "key_bills_tracked":      len(KEY_BILLS),
        "avg_dom_yes_voter":      avg_dom_yes_voter,
        "methodology_version":    FINANCE_METHODOLOGY_VERSION,
        "methodology_date":       FINANCE_METHODOLOGY_DATE,
        "validation_failures":    all_failures,
    }

    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "stats":          stats,
        "per_bill":       per_bill,
        "legislators":    legislators,
        "key_bills":      dict(KEY_BILLS),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        analysis = compute_analysis(conn)
    finally:
        conn.close()

    with CACHE_FILE.open("r", encoding="utf-8") as f:
        cache = json.load(f)
    cache["state"]["dominion_analysis"] = analysis
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"), default=str)

    s = analysis["stats"]
    print(
        f"Dominion analysis complete"
        f" (schema={CACHE_SCHEMA_VERSION}, methodology v{FINANCE_METHODOLOGY_VERSION})."
    )
    print(f"  Total Dominion utility $: ${s['total_dominion_dollars']:,.0f}")
    print(f"  Legislators funded: {s['legislators_funded']} / {s['legislators_analyzed']}")
    print(f"  Top recipient: {s['top_recipient']} (${s['top_recipient_amount']:,.0f})")

    failures = s.get("validation_failures", [])
    if failures:
        print(f"  ⚠ {len(failures)} validation failure(s) — records rejected:")
        for f in failures:
            print(f"    {f}")

    print()
    print("Per-bill funding gap (primary: ratio of medians):")
    for b in analysis["per_bill"]:
        conc = b.get("concentration_tier_yes") or ""
        conc_s = f"  [{conc} concentration]" if conc else ""
        print(
            f"  {b['bill_id']:<8}"
            f"  YES med={b['median_yes_dom_display']:>10}"
            f"  NO med={b['median_no_dom_display']:>10}"
            f"  ratio_med={b['ratio_medians_display']:>8}"
            f"  ratio_mean={b['ratio_means_display']:>8}"
            f"  grade={b['sample_size_grade']}"
            f"  (y={b['yes_count']} n={b['no_count']})"
            f"{conc_s}"
        )

    print()
    print("Top 15 Dominion-funded legislators:")
    for leg in [l for l in analysis["legislators"] if l["dom_total"] > 0][:15]:
        print(
            f"  {leg['name'][:35].ljust(35)} {(leg['party'] or '?')[0]}"
            f"  {leg['dom_total_display']:>10}"
            f"  share={leg['dom_share_pct_display']} ({leg['dom_share_band']})"
            f"  energy YES {leg['yes_rate'] if leg['yes_rate'] is not None else 'n/a'}%"
        )


if __name__ == "__main__":
    main()
