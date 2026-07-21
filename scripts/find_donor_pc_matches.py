#!/usr/bin/env python3
"""
find_donor_pc_matches.py

Reviewable CLI wrapper around voteiq.services.donor_pc_match.find_matches().
Prints every lead found -- a fuzzy name match between a Planning Commission
case's applicant/property-owner and a campaign donor who also gave to a
council member who voted on that case (Portsmouth + Newport News only, the
2 cities with a real council_hearing_date link).

This is intentionally NOT wired into the public chat yet. Every result here
is a lead for a human (reporter, resident, admin) to verify independently,
not a proven conflict of interest -- see the confidence field and the
module docstring in voteiq/services/donor_pc_match.py for why.

Usage:
    python scripts/find_donor_pc_matches.py
    python scripts/find_donor_pc_matches.py --min-confidence high
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from voteiq.services.donor_pc_match import find_matches  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-confidence", choices=["low", "high"], default="low",
                     help="Only show matches at or above this confidence (default: low, i.e. show all)")
    args = ap.parse_args()

    results = find_matches()
    if args.min_confidence == "high":
        results = [r for r in results if r["confidence"] == "high"]

    if not results:
        print("No leads found. This can genuinely mean nothing to report -- Portsmouth and "
              "Newport News are the only 2 cities with a real PC-case-to-Council-vote link, "
              "and a lead only surfaces once a case has actually gone to a recorded Council "
              "vote (council_hearing_date < today) AND the applicant/owner fuzzy-matches a "
              "real donor who also gave to a member who voted on it.")
        return

    print(f"{len(results)} lead(s) -- READ THE CONFIDENCE FIELD. 'low' means a common-surname "
          f"or generic-name match; verify independently before treating any of this as fact.\n")
    for r in results:
        print(
            f"[{r['confidence'].upper()}] {r['city']} {r['item_ref']} ({r['council_hearing_date']}): "
            f"\"{r['case_title'][:80]}\"\n"
            f"    {r['matched_role']} \"{r['matched_name']}\" ~ donor \"{r['donor_name'].strip()}\" "
            f"gave ${r['donor_amount']:,.0f} ({r['donor_cycle']}) to {r['council_member']}\n"
            f"    {r['council_member']} voted {r['member_vote']!r} -- case {r['vote_result']} "
            f"{r['vote_count']}\n"
        )


if __name__ == "__main__":
    main()
