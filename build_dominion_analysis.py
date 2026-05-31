#!/usr/bin/env python3
"""Build Dominion Energy legislative influence analysis.

Identifies donations specifically from Dominion Energy / Dominion Resources /
Dominion Power (the electric utility), cross-references with VA legislators'
votes on key energy/utility bills, and computes a per-bill donor funding gap
(average Dominion $ for YES voters vs NO voters).

Writes cache["state"]["dominion_analysis"] with:
  - stats:          headline numbers
  - per_bill:       per-bill funding gap (YES vs NO voter Dominion $)
  - legislators:    per-legislator Dominion $, party, energy votes
  - key_bills:      metadata for each tracked bill
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH   = BASE_DIR / "polls.db"
CACHE_FILE = BASE_DIR / "data" / "donor_map_cache.json"

# ── Dominion utility employer filter ─────────────────────────────────────────
# Conservative: only match employers that are clearly Dominion Energy the utility.
# Two-step: must match INCLUDE pattern AND not match EXCLUDE pattern.
_DOM_INCLUDE = re.compile(
    r'dominion\s+(energy|resources?|power|virginia\s*power|va\s*power|'
    r'enery|engery|generation|transmissions?|electric\s*(?:co[-\s]?op|cooperat)|'
    r'resources\s*(?:inc|services)|energy\s*(?:inc|services|virginia)|'
    r'- power|-power)',
    re.IGNORECASE
)
_DOM_BARE   = re.compile(r'^dominion\s*(?:resources?\s*services,?\s*inc\.?)?$', re.IGNORECASE)
_OLD_DOM_ELEC = re.compile(r'old\s+dominion\s+electric', re.IGNORECASE)
_DOM_EXCLUDE = re.compile(
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
# Selected for: direct Dominion business relevance, meaningful YES/NO vote split
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
_TITLE_RE = re.compile(
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


# ── Build functions ───────────────────────────────────────────────────────────

def build_dominion_donations(conn: sqlite3.Connection) -> dict[tuple, float]:
    """Return {(last, first_initial): total_dominion_$} for current-session legislators."""
    rows = conn.execute(
        "SELECT employer, candidate_name, ROUND(SUM(amount),0) t "
        "FROM va_cf_schedule_a "
        "WHERE is_individual=1 AND employer IS NOT NULL "
        "GROUP BY employer, candidate_name"
    ).fetchall()

    # Build key → {total, donors, matched_employers}
    key_total: dict[tuple, float]       = defaultdict(float)
    key_employers: dict[tuple, set]     = defaultdict(set)

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
        "WHERE bill_id IN ({})".format(
            ",".join("?" for _ in KEY_BILLS)
        ),
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


def compute_analysis(conn: sqlite3.Connection) -> dict:
    dom_total, dom_emp = build_dominion_donations(conn)
    leg_votes           = build_legislator_votes(conn)
    leg_info            = build_legislator_info(conn)

    # Union of all keys that appear in votes (these are the legislators in scope)
    all_vote_keys = set(leg_votes.keys())

    # ── Per-legislator records ──
    legislators = []
    for key in all_vote_keys:
        info   = leg_info.get(key, {})
        dom_dollars  = dom_total.get(key, 0.0)
        votes  = leg_votes[key]

        # Energy bill summary
        voted_bills = {b: v for b, v in votes.items() if b in KEY_BILLS}
        yes_n = sum(1 for v in voted_bills.values() if v == "yes")
        no_n  = sum(1 for v in voted_bills.values() if v == "no")
        total_votes = yes_n + no_n
        yes_rate = round(yes_n / total_votes * 100, 1) if total_votes else None

        # Dominion alignment score (only meaningful if received Dominion $ AND voted)
        dom_score = None
        if dom_dollars > 0 and total_votes > 0:
            max_dom = max((v for v in dom_total.values()), default=1)
            log_max = math.log10(max_dom + 1)
            log_dom = math.log10(dom_dollars + 1)
            dom_score = round((yes_rate / 100) * (log_dom / log_max) * 100, 1) if yes_rate is not None else None

        legislators.append({
            "name":       info.get("name", " ".join(key)),
            "party":      info.get("party", ""),
            "chamber":    info.get("chamber", ""),
            "dom_total":  round(dom_dollars, 0),
            "dom_funded": dom_dollars > 0,
            "energy_votes": voted_bills,
            "energy_yes":  yes_n,
            "energy_no":   no_n,
            "yes_rate":    yes_rate,
            "dom_score":   dom_score,
        })

    legislators.sort(key=lambda x: -(x["dom_total"] or 0))

    # ── Per-bill funding gap ──
    per_bill = []
    # Fetch bill titles & status
    bill_meta = {}
    rows = conn.execute(
        "SELECT bill_number, title, status_label FROM legiscan_va_bills "
        "WHERE bill_number IN ({})".format(",".join("?" for _ in KEY_BILLS)),
        list(KEY_BILLS.keys()),
    ).fetchall()
    for r in rows:
        bill_meta[r["bill_number"]] = {
            "title": r["title"], "status": r["status_label"]
        }

    for bill_id, desc in KEY_BILLS.items():
        yes_dom, no_dom = [], []
        yes_voters, no_voters = [], []
        for leg in legislators:
            vote = leg["energy_votes"].get(bill_id)
            if not vote:
                continue
            dom = leg["dom_total"]
            if vote == "yes":
                yes_dom.append(dom)
                yes_voters.append(leg["name"])
            elif vote == "no":
                no_dom.append(dom)
                no_voters.append(leg["name"])

        def _avg(lst):
            funded = [v for v in lst if v > 0]
            return round(sum(funded) / len(funded), 0) if funded else 0.0

        avg_yes = _avg(yes_dom)
        avg_no  = _avg(no_dom)
        ratio   = round(avg_yes / avg_no, 1) if avg_no > 0 else None
        funded_yes = sum(1 for v in yes_dom if v > 0)
        funded_no  = sum(1 for v in no_dom  if v > 0)
        meta = bill_meta.get(bill_id, {})

        per_bill.append({
            "bill_id":      bill_id,
            "description":  desc,
            "title":        meta.get("title", desc),
            "status":       meta.get("status", ""),
            "yes_count":    len(yes_dom),
            "no_count":     len(no_dom),
            "funded_yes":   funded_yes,
            "funded_no":    funded_no,
            "avg_yes_dom":  avg_yes,
            "avg_no_dom":   avg_no,
            "ratio":        ratio,  # YES voters got ratio× more Dominion $ than NO voters
        })

    per_bill.sort(key=lambda x: -(x["ratio"] or 0))

    # ── Stats ──
    funded_legs = [l for l in legislators if l["dom_total"] > 0]
    total_dom_dollars = sum(v for v in dom_total.values())
    stats = {
        "total_dominion_dollars":  round(total_dom_dollars, 0),
        "legislators_funded":      len(funded_legs),
        "legislators_analyzed":    len(legislators),
        "top_recipient":           funded_legs[0]["name"]  if funded_legs else "",
        "top_recipient_amount":    funded_legs[0]["dom_total"] if funded_legs else 0,
        "key_bills_tracked":       len(KEY_BILLS),
        "avg_dom_yes_voter":       round(sum(l["dom_total"] for l in legislators if l.get("yes_rate") is not None and l["yes_rate"] >= 70) /
                                         max(1, sum(1 for l in legislators if l.get("yes_rate") is not None and l["yes_rate"] >= 70)), 0),
    }

    return {
        "stats":        stats,
        "per_bill":     per_bill,
        "legislators":  legislators,
        "key_bills":    {k: v for k, v in KEY_BILLS.items()},
    }


def main() -> None:
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

    print(f"Dominion analysis complete.")
    s = analysis["stats"]
    print(f"  Total Dominion utility $: ${s['total_dominion_dollars']:,.0f}")
    print(f"  Legislators funded: {s['legislators_funded']} / {s['legislators_analyzed']}")
    print(f"  Top recipient: {s['top_recipient']} (${s['top_recipient_amount']:,.0f})")
    print()
    print(f"Per-bill funding gap (YES voter avg Dominion $ vs NO voter avg):")
    for b in analysis["per_bill"]:
        ratio_str = f"{b['ratio']}x more" if b["ratio"] else "n/a"
        print(f"  {b['bill_id']:<8} YES avg=${b['avg_yes_dom']:>8,.0f} "
              f"NO avg=${b['avg_no_dom']:>8,.0f}  ratio={ratio_str:>10}  "
              f"(y={b['yes_count']} n={b['no_count']}) {b['description'][:45]}")
    print()
    print("Top 15 Dominion-funded current legislators:")
    for l in [x for x in analysis["legislators"] if x["dom_total"] > 0][:15]:
        print(f"  {l['name'][:35].ljust(35)} {(l['party'] or '?')[0]}  "
              f"${l['dom_total']:>8,.0f}  energy YES {l['yes_rate'] if l['yes_rate'] is not None else 'n/a'}%")


if __name__ == "__main__":
    main()
