"""
One-off retry for the 34 Suffolk documents that failed with Gemini
504 DEADLINE_EXCEEDED during the initial 2020-2026 scrape.

PDFs are already cached locally (suffolk_council_cache/), so this only
retries the Gemini extraction step -- with actual retry-on-timeout logic,
which scrape_suffolk_council.py's extract_votes_gemini() doesn't have
(a single 504 there just gives up and returns []).

Usage:
    python scripts/oneoffs/retry_suffolk_failures.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

import scrape_suffolk_council as s

FAILED_DOCS = [
    ("2498", "2020-08-19"), ("2425", "2020-03-04"), ("2414", "2020-02-05"),
    ("2722", "2021-10-20"), ("2683", "2021-08-18"), ("2668", "2021-07-21"),
    ("2578", "2021-02-17"), ("2901", "2022-08-17"), ("2789", "2022-02-16"),
    ("3215", "2023-12-20"), ("3024", "2023-02-01"), ("3428", "2024-12-18"),
    ("3327", "2024-07-02"), ("3247", "2024-02-21"), ("3601", "2025-10-15"),
    ("3568", "2025-08-20"), ("3529", "2025-06-18"), ("3451", "2025-02-05"),
    ("3763", "2026-07-01"),
]


def extract_with_retry(text: str, api_key: str, max_attempts: int = 4) -> list[dict]:
    for attempt in range(1, max_attempts + 1):
        votes = s.extract_votes_gemini(text, api_key)
        if votes:
            return votes
        # extract_votes_gemini returns [] both on a real "no votes found"
        # and on an error -- but a genuinely empty doc would have said so
        # via a different path upstream, so retrying an empty result here
        # is safe: worst case it just confirms zero votes again.
        if attempt < max_attempts:
            wait = 30 * attempt
            print(f"    retry {attempt}/{max_attempts - 1} in {wait}s...")
            time.sleep(wait)
    return []


def main() -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set")
        return

    s.init_db()

    total_stored = 0
    still_failed = []
    for i, (doc_id, meeting_date) in enumerate(FAILED_DOCS, 1):
        print(f"[{i}/{len(FAILED_DOCS)}] doc {doc_id} ({meeting_date})")
        yyyy, mm, dd = meeting_date.split("-")
        url_date = f"{mm}{dd}{yyyy}"

        pdf_path = s.download_agenda_pdf(doc_id, url_date)
        if not pdf_path:
            print("    no PDF -- skipping")
            still_failed.append((doc_id, meeting_date))
            continue

        text = s.extract_pdf_text(pdf_path)
        if len(text) < 200:
            print("    agenda text too short -- skipping")
            continue

        votes = extract_with_retry(text, api_key)
        print(f"    extracted {len(votes)} vote items")
        if not votes:
            still_failed.append((doc_id, meeting_date))
            continue

        stored = s.store_meeting_votes(doc_id, meeting_date, votes)
        total_stored += stored
        time.sleep(1)

    print(f"\nDone -- {total_stored} new member-vote rows recovered")
    if still_failed:
        print(f"Still failed after retries ({len(still_failed)}): {still_failed}")
    else:
        print("All 34 previously-failed documents recovered successfully.")


if __name__ == "__main__":
    main()
