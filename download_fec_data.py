#!/usr/bin/env python3
"""
Download FEC (Federal Elections Commission) campaign finance data.
Downloads contribution data for federal candidates from FEC.gov

Data sources:
  - FEC Contributions API: https://api.open.fec.gov/
  - Individual contributions (Schedule A)
  - Committee-to-committee transfers
  - Candidate financial summary

Usage:
    python download_fec_data.py                    # Download all recent cycles
    python download_fec_data.py --candidate "Spanberger"
    python download_fec_data.py --cycle 2024
    python download_fec_data.py --state "VA"
"""

import argparse
import json
import os
import time
from pathlib import Path
from datetime import datetime

import requests

# FEC API Configuration
FEC_API_KEY = os.getenv('FEC_API_KEY', 'DEMO_KEY')  # Get from https://api.open.fec.gov
FEC_BASE_URL = "https://api.open.fec.gov/v1"
FEC_DATA_DIR = Path("fec_data")
FEC_DATA_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "VoteIQ/1.0 (Federal Election Research)",
}

# Virginia federal candidates of interest
VA_CANDIDATES = {
    "Spanberger": {
        "name": "Abigail Spanberger",
        "office": "federal:house,federal:governor",
        "cycles": [2018, 2020, 2022, 2024],
    },
    "Kiggans": {
        "name": "Jen Kiggans",
        "office": "federal:house",
        "cycles": [2022, 2024],
    },
}


def get_fec_api_key():
    """Validate FEC API key"""
    if FEC_API_KEY == 'DEMO_KEY':
        print("[WARN] Using DEMO_KEY - limited to 20 requests/min")
        print("       Get free key at: https://api.open.fec.gov/#/register")
    return FEC_API_KEY


def search_candidates(name: str = None, state: str = None, cycle: int = None) -> list:
    """
    Search FEC candidate database
    Returns list of candidates matching criteria
    """
    params = {
        "api_key": get_fec_api_key(),
        "per_page": 100,
    }

    if name:
        params["name"] = name
    if state:
        params["state"] = state
    if cycle:
        params["election_year"] = cycle

    try:
        resp = requests.get(
            f"{FEC_BASE_URL}/candidates/search",
            params=params,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except Exception as e:
        print(f"[ERROR] Failed to search candidates: {e}")
        return []


def get_candidate_info(candidate_id: str) -> dict:
    """Get detailed info about a specific candidate"""
    params = {"api_key": get_fec_api_key()}

    try:
        resp = requests.get(
            f"{FEC_BASE_URL}/candidate/{candidate_id}",
            params=params,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", [{}])[0]
    except Exception as e:
        print(f"[ERROR] Failed to get candidate info: {e}")
        return {}


def download_contributions(candidate_id: str, cycle: int = None) -> list:
    """
    Download all contributions to a candidate
    FEC provides Schedule A (individual contributions)
    """
    params = {
        "api_key": get_fec_api_key(),
        "candidate_id": candidate_id,
        "per_page": 100,
    }

    if cycle:
        params["cycle"] = cycle

    all_contributions = []
    page = 1

    print(f"  Downloading contributions for {candidate_id} (cycle {cycle or 'all'})...")

    while True:
        params["page"] = page
        try:
            resp = requests.get(
                f"{FEC_BASE_URL}/schedules/schedule_a",
                params=params,
                headers=HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            contributions = data.get("results", [])

            if not contributions:
                break

            all_contributions.extend(contributions)
            print(f"    Page {page}: +{len(contributions)} contributions")

            # Check if there are more pages
            pagination = data.get("pagination", {})
            if page * 100 >= pagination.get("count", 0):
                break

            page += 1
            time.sleep(0.5)  # Rate limiting

        except Exception as e:
            print(f"    [ERROR] Failed at page {page}: {e}")
            break

    return all_contributions


def download_financial_summary(candidate_id: str) -> dict:
    """Get candidate financial summary (total raised, spent, etc.)"""
    params = {"api_key": get_fec_api_key(), "candidate_id": candidate_id}

    try:
        resp = requests.get(
            f"{FEC_BASE_URL}/candidate/{candidate_id}/totals",
            params=params,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", [{}])[0]
    except Exception as e:
        print(f"[ERROR] Failed to get financial summary: {e}")
        return {}


def save_candidate_data(candidate: dict, contributions: list, summary: dict):
    """Save candidate data to JSON files"""
    candidate_id = candidate.get("candidate_id")
    candidate_name = candidate.get("name", "Unknown")

    if not candidate_id:
        return

    # Create candidate directory
    cand_dir = FEC_DATA_DIR / candidate_id
    cand_dir.mkdir(parents=True, exist_ok=True)

    # Save candidate info
    with open(cand_dir / "candidate.json", "w", encoding="utf-8") as f:
        json.dump(candidate, f, indent=2, default=str)

    # Save contributions
    with open(cand_dir / "contributions.jsonl", "w", encoding="utf-8") as f:
        for contrib in contributions:
            f.write(json.dumps(contrib, default=str) + "\n")

    # Save summary
    with open(cand_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"  [OK] Saved {len(contributions)} contributions to {cand_dir}")


def download_all_va_candidates(cycle: int = None):
    """Download data for all Virginia federal candidates of interest"""
    print(f"\n{'='*70}")
    print(f"Downloading FEC Data for Virginia Federal Candidates")
    print(f"Cycles: {cycle or 'All recent (2018-2024)'}")
    print(f"{'='*70}\n")

    cycles_to_process = [cycle] if cycle else [2018, 2020, 2022, 2024]

    for candidate_key, candidate_info in VA_CANDIDATES.items():
        print(f"\nCandidate: {candidate_info['name']}")
        print(f"  Office: {candidate_info['office']}")

        # Search for candidate
        results = search_candidates(
            name=candidate_info["name"], state="VA", cycle=cycle
        )

        if not results:
            print(f"  [WARN] No matching candidates found")
            continue

        for result in results:
            candidate_id = result.get("candidate_id")
            if not candidate_id:
                continue

            print(f"  ID: {candidate_id}")

            # Get detailed info
            candidate = get_candidate_info(candidate_id)
            print(f"  Office: {candidate.get('office_full', 'Unknown')}")

            # Download contributions for each cycle
            for target_cycle in cycles_to_process:
                if target_cycle not in candidate_info.get("cycles", []):
                    continue

                contributions = download_contributions(candidate_id, target_cycle)
                summary = download_financial_summary(candidate_id)

                save_candidate_data(candidate, contributions, summary)

                # Rate limiting
                time.sleep(1)

    print(f"\n{'='*70}")
    print(f"Download complete!")
    print(f"Data saved to: {FEC_DATA_DIR}")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Download FEC campaign finance data for federal candidates"
    )
    parser.add_argument(
        "--candidate", type=str, help="Candidate name to search for (e.g., Spanberger)"
    )
    parser.add_argument("--state", type=str, help="State code (e.g., VA)")
    parser.add_argument("--cycle", type=int, help="Election cycle (e.g., 2024)")
    parser.add_argument(
        "--all-va",
        action="store_true",
        help="Download all Virginia federal candidates of interest",
    )

    args = parser.parse_args()

    if args.all_va:
        download_all_va_candidates(args.cycle)
    elif args.candidate or args.state or args.cycle:
        results = search_candidates(args.candidate, args.state, args.cycle)
        print(f"\nFound {len(results)} candidates:")
        for r in results:
            print(f"  {r.get('name')} ({r.get('candidate_id')})")
    else:
        download_all_va_candidates()


if __name__ == "__main__":
    main()
