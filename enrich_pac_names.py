#!/usr/bin/env python3
"""
Enrich FEC PAC names that fall into 'Other' by classifying them with Gemini
(Google Search grounding).

Steps:
  1. Extract all unique PAC names from fec_industry_totals WHERE industry='Other'
  2. Skip names that now match updated INDUSTRY_MAP or _INTERNAL_PATTERNS
  3. Ask Gemini (with search grounding) to classify each remaining name
  4. Store results in pac_name_lookup table
  5. Re-run ingest_fec_pacs.py to rebuild industry totals with enriched lookups

Usage:
    python enrich_pac_names.py                # classify + show plan (dry run)
    python enrich_pac_names.py --apply        # write to DB
    python enrich_pac_names.py --apply --reingest   # also re-run ingestion
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
GEMINI_MODEL = "gemini-2.5-flash"

VALID_INDUSTRIES = [
    "Defense", "Healthcare", "Energy", "Finance", "Technology",
    "Labor", "Agriculture", "Real Estate", "Transportation", "Telecom",
    "Alcohol/Tobacco", "Automotive", "Legal", "Small Business",
    "Hospitality", "Retail", "Manufacturing", "Ideological", "Other",
]

CLASSIFY_PROMPT = """You are classifying political action committees (PACs) by industry sector.

For each PAC name below, return ONLY a JSON object mapping the exact PAC name to one industry label.
Use ONLY these labels: {industries}

Rules:
- If the PAC represents a specific company or trade association, use that company's primary industry
- Leadership PACs, party committees, and political advocacy groups → "Ideological"
- Payment processors, consulting firms, media buyers, vendors → "Other"
- Individual names (person, not org) → "Other"

PAC names to classify:
{names}

Return ONLY valid JSON, no explanation. Example:
{{"NAME OF PAC 1": "Defense", "NAME OF PAC 2": "Labor"}}
"""


def setup_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pac_name_lookup (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pac_name    TEXT NOT NULL UNIQUE,
            industry    TEXT NOT NULL,
            source      TEXT DEFAULT 'gemini',
            confidence  TEXT DEFAULT 'high',
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def get_other_pac_names(conn: sqlite3.Connection) -> list[str]:
    """Extract unique PAC names from top_donors of Other rows."""
    names: set[str] = set()
    rows = conn.execute(
        "SELECT top_donors FROM fec_industry_totals WHERE industry='Other'"
    ).fetchall()
    for (td,) in rows:
        if not td:
            continue
        try:
            donors = json.loads(td)
            for d in donors:
                n = (d.get("name") or "").strip()
                if n:
                    names.add(n)
        except Exception:
            pass
    return sorted(names)


def already_classified(name: str) -> str | None:
    """Return industry if name now matches updated INDUSTRY_MAP or _INTERNAL_PATTERNS."""
    from ingest_fec_pacs import INDUSTRY_MAP, _INTERNAL_PATTERNS, is_internal_transfer, classify_contributor
    if is_internal_transfer(name):
        return "__skip__"
    result = classify_contributor(name)
    if result != "Other":
        return result
    return None


def get_existing_lookup(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT pac_name, industry FROM pac_name_lookup").fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def gemini_classify(names: list[str], api_key: str) -> dict[str, str]:
    """Send a batch of PAC names to Gemini for industry classification."""
    if not _GENAI_OK:
        print("ERROR: google-genai not installed. Run: pip install google-genai")
        return {}

    client = _genai.Client(api_key=api_key)
    prompt = CLASSIFY_PROMPT.format(
        industries=", ".join(VALID_INDUSTRIES),
        names="\n".join(f"- {n}" for n in names),
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=_gtypes.GenerateContentConfig(
                tools=[_gtypes.Tool(google_search=_gtypes.GoogleSearch())]
            ),
        )
        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        return {k: v for k, v in result.items() if v in VALID_INDUSTRIES}
    except Exception as exc:
        print(f"  Gemini error: {exc}")
        return {}


def store_lookup(conn: sqlite3.Connection, classifications: dict[str, str], source: str) -> int:
    stored = 0
    for pac_name, industry in classifications.items():
        try:
            conn.execute(
                "INSERT OR REPLACE INTO pac_name_lookup (pac_name, industry, source) VALUES (?, ?, ?)",
                (pac_name, industry, source),
            )
            stored += 1
        except Exception as exc:
            print(f"  DB error storing '{pac_name}': {exc}")
    conn.commit()
    return stored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrich FEC PAC names with Gemini")
    parser.add_argument("--apply", action="store_true", help="Write classifications to DB")
    parser.add_argument("--reingest", action="store_true", help="Re-run ingest_fec_pacs.py after enrichment")
    parser.add_argument("--batch-size", type=int, default=20, help="PAC names per Gemini call")
    args = parser.parse_args(argv)

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        return 1

    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)

    # Step 1: Get all unique PAC names from Other rows
    all_names = get_other_pac_names(conn)
    print(f"Found {len(all_names)} unique PAC names in 'Other' bucket")

    # Step 2: Categorize each name
    existing = get_existing_lookup(conn)
    skip: list[str] = []
    already_done: list[tuple[str, str]] = []
    already_in_lookup: list[str] = []
    needs_gemini: list[str] = []

    for name in all_names:
        if name in existing:
            already_in_lookup.append(name)
            continue
        result = already_classified(name)
        if result == "__skip__":
            skip.append(name)
        elif result is not None:
            already_done.append((name, result))
        else:
            needs_gemini.append(name)

    print(f"\n  Already in lookup table:  {len(already_in_lookup)}")
    print(f"  Now filtered (internal):  {len(skip)}")
    print(f"  Now classified by map:    {len(already_done)}")
    print(f"  Needs Gemini:             {len(needs_gemini)}")

    if skip:
        print(f"\nFiltered as internal/vendor:")
        for n in skip:
            print(f"  SKIP  {n}")

    if already_done:
        print(f"\nNow classified by updated keyword map:")
        for name, ind in already_done:
            print(f"  {ind:<20} {name}")

    if not needs_gemini:
        print("\nNothing left for Gemini — all classified!")
    else:
        print(f"\nSending {len(needs_gemini)} names to Gemini:")
        for n in needs_gemini:
            print(f"  ? {n}")

    if not args.apply:
        print("\n[dry run] Pass --apply to write to DB and enrich classifications")
        conn.close()
        return 0

    # Step 3: Store newly-classified-by-map entries
    if already_done:
        stored = store_lookup(conn, dict(already_done), source="keyword_map")
        print(f"\nStored {stored} keyword-map classifications")

    # Step 4: Gemini batch classification
    gemini_results: dict[str, str] = {}
    if needs_gemini:
        print(f"\nCalling Gemini in batches of {args.batch_size}…")
        for i in range(0, len(needs_gemini), args.batch_size):
            batch = needs_gemini[i : i + args.batch_size]
            print(f"  Batch {i//args.batch_size + 1}: {len(batch)} names…")
            result = gemini_classify(batch, api_key)
            print(f"    Classified {len(result)}/{len(batch)}")
            for name, ind in result.items():
                print(f"    {ind:<20} {name}")
            gemini_results.update(result)
            if i + args.batch_size < len(needs_gemini):
                time.sleep(2)  # avoid rate limiting

        stored = store_lookup(conn, gemini_results, source="gemini")
        print(f"\nStored {stored} Gemini classifications")

        # Report unclassified
        unclassified = [n for n in needs_gemini if n not in gemini_results]
        if unclassified:
            print(f"\n{len(unclassified)} names still unclassified (Gemini returned 'Other' or failed):")
            for n in unclassified:
                print(f"  {n}")

    conn.close()

    # Step 5: Optional re-ingest
    if args.reingest:
        print("\nRe-running ingest_fec_pacs.py for all cycles…")
        script = BASE_DIR / "ingest_fec_pacs.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=False,
        )
        if result.returncode != 0:
            print("ingest_fec_pacs.py exited with errors")
            return 1
        print("Re-ingestion complete.")

    total_stored = len(already_done) + len(gemini_results)
    print(f"\nDone. {total_stored} new classifications stored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
