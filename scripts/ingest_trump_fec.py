from __future__ import annotations

import pprint

from voteiq.queries.sectors import get_sector_totals, get_shared_donors_named
from voteiq.scripts.fix_fec_pipeline import ingest_candidate_fec

# Trump FEC candidate ID (consistent across cycles)
TRUMP_CANDIDATE_ID = "P80001571"
CYCLES = [2024, 2020]  # extend if needed: 2016, 2012

# Optional comparison candidates. These are FEC candidate IDs, not bioguide IDs.
OTHER_CANDIDATES = [
    "P40011839",  # Ron DeSantis presidential campaign
]


def main() -> int:
    # Step 1: Ingest FEC individual contributions
    for cycle in CYCLES:
        print(f"\nIngesting Trump contributions for {cycle}...")
        summary = ingest_candidate_fec(
            candidate_id=TRUMP_CANDIDATE_ID,
            cycles=[cycle],
            candidate_name="Donald J. Trump",
        )
        pprint.pprint(summary)
        print(f"Completed ingestion for {cycle}.")

    # Step 2: Aggregate sector totals
    for cycle in CYCLES:
        print(f"\nSector totals for {cycle}:")
        sectors = get_sector_totals(TRUMP_CANDIDATE_ID, cycle)
        pprint.pprint(sectors[:10])  # print top 10 sectors

    # Step 3: Identify shared donors across other candidates
    print("\nShared donors between Trump and selected candidates:")
    shared = get_shared_donors_named(
        [TRUMP_CANDIDATE_ID] + OTHER_CANDIDATES,
        cycle=2024,
        min_candidates=2,
    )
    pprint.pprint(shared[:10])  # top 10 shared donors
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
