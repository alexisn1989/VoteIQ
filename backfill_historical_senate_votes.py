#!/usr/bin/env python3
"""
Backfill VA federal delegation votes for 117th and 118th Congress.

Covers both chambers:
  Senate: Warner + Kaine
  House:  All 11 VA House members (bioguides in ingest_congress_votes.py)

117th Congress: 2021 (session 1), 2022 (session 2)
118th Congress: 2023 (session 1), 2024 (session 2)

Usage:
    python backfill_historical_senate_votes.py
    python backfill_historical_senate_votes.py --dry-run
    python backfill_historical_senate_votes.py --senate-limit 500 --house-limit 300
    python backfill_historical_senate_votes.py --senate-only
    python backfill_historical_senate_votes.py --house-only
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# (congress, session, year)
HISTORICAL_SESSIONS = [
    (117, 1, 2021),
    (117, 2, 2022),
    (118, 1, 2023),
    (118, 2, 2024),
]


def run_session(
    congress: int,
    session: int,
    year: int,
    senate_limit: int,
    house_limit: int,
    dry_run: bool,
    senate_only: bool,
    house_only: bool,
) -> None:
    print(f"\n{'='*60}")
    print(f"Congress {congress}, Session {session} ({year})")
    print(f"{'='*60}")

    cmd = [
        sys.executable,
        str(BASE_DIR / "ingest_congress_votes.py"),
        "--congress",      str(congress),
        "--session",       str(session),
        "--year",          str(year),
        "--senate-limit",  str(0 if house_only else senate_limit),
        "--house-limit",   str(0 if senate_only else house_limit),
        "--enrich-titles",
    ]
    if dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    if result.returncode != 0:
        print(f"  WARNING: session {congress}/{session} exited with code {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill 117th/118th Congress votes for VA federal delegation"
    )
    parser.add_argument("--senate-limit", type=int, default=500,
                        help="Max Senate votes per session (default 500)")
    parser.add_argument("--house-limit",  type=int, default=300,
                        help="Max House rolls per session (default 300)")
    parser.add_argument("--senate-only",  action="store_true")
    parser.add_argument("--house-only",   action="store_true")
    parser.add_argument("--dry-run",      action="store_true")
    args = parser.parse_args(argv)

    for congress, session, year in HISTORICAL_SESSIONS:
        run_session(
            congress, session, year,
            senate_limit=args.senate_limit,
            house_limit=args.house_limit,
            dry_run=args.dry_run,
            senate_only=args.senate_only,
            house_only=args.house_only,
        )

    print("\n\nDone. All historical sessions processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
