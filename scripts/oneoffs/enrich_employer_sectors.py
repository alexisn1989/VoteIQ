#!/usr/bin/env python3
"""
Use Gemini to classify 'Other'/'Unknown' employer strings in
fec_individual_contributions, then update the DB and rebuild sector totals.

Usage:
    python enrich_employer_sectors.py
    python enrich_employer_sectors.py --dry-run   # print mappings without writing
    python enrich_employer_sectors.py --batch 50  # employers per Gemini call
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import google.genai as genai
from google.genai import types as gtypes
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).resolve().parent / "polls.db"
GEMINI_MODEL = "gemini-2.5-flash"

VALID_SECTORS = [
    "Defense", "Finance", "Real Estate", "Healthcare", "Energy", "Legal",
    "Technology", "Education", "Construction", "Manufacturing", "Consulting",
    "Lobbying", "Automotive", "Hospitality", "Government", "Advocacy",
    "Nonprofit", "Agriculture", "Transportation", "Media", "Retail",
    "Retired", "Self-Employed", "Homemaker", "Other",
]

SYSTEM_PROMPT = """You are a campaign finance data analyst. Classify employer names
from FEC contribution filings into industry sectors.

Rules:
- Return ONLY a JSON object mapping each employer string to a sector.
- Use exactly the sector names provided — no variations.
- "Retired", "Self-Employed", "Homemaker" are valid sectors for status entries.
- If truly unclassifiable, use "Other".
- Do not explain, just return the JSON.

Valid sectors: """ + ", ".join(VALID_SECTORS)


def get_unclassified_employers(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT contributor_employer
        FROM fec_individual_contributions
        WHERE employer_sector IN ('Other', 'Unknown')
          AND COALESCE(contributor_employer, '') != ''
        ORDER BY contributor_employer
    """).fetchall()
    return [r[0] for r in rows]


def classify_batch(client: genai.Client, employers: list[str]) -> dict[str, str]:
    employer_list = "\n".join(f'  "{e}"' for e in employers)
    prompt = f"Classify these employer strings:\n{employer_list}\n\nReturn JSON only."

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[gtypes.Content(role="user", parts=[gtypes.Part(text=prompt)])],
        config=gtypes.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=2048,
        ),
    )
    text = response.text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def update_sectors(conn: sqlite3.Connection, mapping: dict[str, str], dry_run: bool) -> int:
    updated = 0
    for employer, sector in mapping.items():
        if sector not in VALID_SECTORS:
            print(f"  SKIP invalid sector '{sector}' for '{employer}'")
            continue
        if sector in ("Other", "Unknown"):
            continue  # Don't overwrite with same value
        if dry_run:
            print(f"  {employer!r:50s} -> {sector}")
        else:
            conn.execute("""
                UPDATE fec_individual_contributions
                SET employer_sector = ?
                WHERE contributor_employer = ?
                  AND employer_sector IN ('Other', 'Unknown')
            """, (sector, employer))
            updated += 1
    if not dry_run:
        conn.commit()
    return updated


def rebuild_sector_totals(conn: sqlite3.Connection) -> None:
    """Rebuild candidate_sector_totals from scratch after re-classification."""
    pairs = conn.execute("""
        SELECT DISTINCT candidate_id, candidate_name, cycle
        FROM fec_individual_contributions
    """).fetchall()

    for candidate_id, candidate_name, cycle in pairs:
        rows = conn.execute("""
            SELECT employer_sector, SUM(amount) AS total, COUNT(*) AS donors
            FROM fec_individual_contributions
            WHERE candidate_id = ? AND cycle = ?
            GROUP BY employer_sector
        """, (candidate_id, cycle)).fetchall()

        for sector, total, donors in rows:
            conn.execute("""
                INSERT INTO candidate_sector_totals
                    (candidate_id, candidate_name, sector, total_amount, donor_count, cycle, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(candidate_id, sector, cycle) DO UPDATE SET
                    total_amount = excluded.total_amount,
                    donor_count  = excluded.donor_count,
                    updated_at   = excluded.updated_at
            """, (candidate_id, candidate_name, sector, total, donors, cycle))

    conn.commit()
    print(f"  Rebuilt sector totals for {len(pairs)} candidate/cycle pairs")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print mappings without writing to DB")
    parser.add_argument("--batch", type=int, default=40, help="Employers per Gemini call")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set")

    conn = sqlite3.connect(DB_PATH)
    employers = get_unclassified_employers(conn)
    print(f"Found {len(employers)} unclassified employers")

    if not employers:
        print("Nothing to do.")
        conn.close()
        return

    client = genai.Client(api_key=api_key)
    all_mappings: dict[str, str] = {}

    for i in range(0, len(employers), args.batch):
        batch = employers[i:i + args.batch]
        print(f"\nClassifying batch {i // args.batch + 1} ({len(batch)} employers)...")
        try:
            mapping = classify_batch(client, batch)
            all_mappings.update(mapping)
            for emp, sector in mapping.items():
                print(f"  {emp!r:50s} -> {sector}")
            time.sleep(1)
        except Exception as exc:
            print(f"  ERROR on batch: {exc}")
            continue

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Applying {len(all_mappings)} mappings...")
    updated = update_sectors(conn, all_mappings, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"  Updated {updated} employer rows")
        print("Rebuilding sector totals...")
        rebuild_sector_totals(conn)

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
