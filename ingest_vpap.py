#!/usr/bin/env python3
"""
Ingest Virginia 2026 race and campaign finance data from VPAP via Gemini search grounding.

Gemini searches vpap.org using Google Search grounding and returns structured
candidate/finance data which is stored in polls.db (vpap_races + vpap_candidates tables).

Examples:
    python ingest_vpap.py
    python ingest_vpap.py --dry-run
    python ingest_vpap.py --year 2026
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
GEMINI_MODEL = "gemini-2.5-flash"

VPAP_QUERIES = [
    ("statewide", "Search vpap.org for all Virginia 2026 statewide race candidates including US Senate and any Governor race. Include campaign finance totals (money raised, spent, cash on hand)."),
    ("house_1_5",  "Search vpap.org for Virginia 2026 US House races districts 1 through 5. For each district list all candidates with party, incumbent status, and campaign finance."),
    ("house_6_11", "Search vpap.org for Virginia 2026 US House races districts 6 through 11. For each district list all candidates with party, incumbent status, and campaign finance."),
]

EXTRACTION_PROMPT = """{query}

Return ONLY valid JSON with this structure (omit fields you cannot find):
{{
  "fetched_date": "{date}",
  "source": "vpap.org",
  "races": [
    {{
      "race": "full race name",
      "office": "US Senate / US House / Governor / Lt Governor / Attorney General",
      "district": null or integer,
      "year": {year},
      "election_date": "YYYY-MM-DD or null",
      "primary_date": "YYYY-MM-DD or null",
      "candidates": [
        {{
          "name": "Full Name",
          "party": "D / R / I / L / G",
          "incumbent": true or false,
          "money_raised": integer or null,
          "money_spent": integer or null,
          "cash_on_hand": integer or null,
          "vpap_url": "full vpap.org URL or null"
        }}
      ]
    }}
  ]
}}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vpap_races (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            race_key        TEXT NOT NULL UNIQUE,
            office          TEXT,
            district        INTEGER,
            year            INTEGER,
            election_date   TEXT,
            primary_date    TEXT,
            raw_json        TEXT,
            fetched_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vpap_candidates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            race_key        TEXT NOT NULL,
            name            TEXT NOT NULL,
            party           TEXT,
            incumbent       INTEGER DEFAULT 0,
            money_raised    INTEGER,
            money_spent     INTEGER,
            cash_on_hand    INTEGER,
            vpap_url        TEXT,
            fetched_at      TEXT NOT NULL,
            UNIQUE(race_key, name)
        );

        CREATE INDEX IF NOT EXISTS idx_vpap_candidates_race ON vpap_candidates(race_key);
        CREATE INDEX IF NOT EXISTS idx_vpap_candidates_party ON vpap_candidates(party);
    """)
    conn.commit()


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.M)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]+\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON in response: {raw[:200]}")


def race_key(office: str, district: int | None, year: int) -> str:
    parts = [str(year), office.lower().replace(" ", "_")]
    if district:
        parts.append(str(district))
    return "_".join(parts)


def gemini_fetch(query: str, year: int, api_key: str) -> dict:
    client = _genai.Client(api_key=api_key)
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = EXTRACTION_PROMPT.format(query=query, date=today, year=year)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=_gtypes.GenerateContentConfig(
            tools=[_gtypes.Tool(google_search=_gtypes.GoogleSearch())]
        ),
    )
    return _parse_json(response.text)


def upsert_race(conn: sqlite3.Connection, race: dict, dry_run: bool) -> int:
    office = race.get("office", "")
    district = race.get("district")
    year = race.get("year", 2026)
    key = race_key(office, district, year)
    candidates = race.get("candidates") or []

    if dry_run:
        print(f"  [dry] {key}: {len(candidates)} candidates")
        return len(candidates)

    conn.execute(
        """
        INSERT INTO vpap_races (race_key, office, district, year, election_date, primary_date, raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(race_key) DO UPDATE SET
            office=excluded.office, district=excluded.district, year=excluded.year,
            election_date=excluded.election_date, primary_date=excluded.primary_date,
            raw_json=excluded.raw_json, fetched_at=excluded.fetched_at
        """,
        (key, office, district, year, race.get("election_date"), race.get("primary_date"),
         json.dumps(race), now_iso()),
    )

    written = 0
    for c in candidates:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        conn.execute(
            """
            INSERT INTO vpap_candidates (race_key, name, party, incumbent, money_raised,
                money_spent, cash_on_hand, vpap_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_key, name) DO UPDATE SET
                party=excluded.party, incumbent=excluded.incumbent,
                money_raised=excluded.money_raised, money_spent=excluded.money_spent,
                cash_on_hand=excluded.cash_on_hand, vpap_url=excluded.vpap_url,
                fetched_at=excluded.fetched_at
            """,
            (key, name, c.get("party"), 1 if c.get("incumbent") else 0,
             c.get("money_raised"), c.get("money_spent"), c.get("cash_on_hand"),
             c.get("vpap_url"), now_iso()),
        )
        written += 1
    conn.commit()
    return written


def ingest(conn: sqlite3.Connection, year: int, api_key: str, dry_run: bool) -> None:
    total_races = total_candidates = 0
    for label, query in VPAP_QUERIES:
        print(f"\nQuerying Gemini ({label})…")
        try:
            data = gemini_fetch(query, year, api_key)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        races = data.get("races") or []
        print(f"  Got {len(races)} races")
        for race in races:
            office = race.get("office", "?")
            district = race.get("district")
            label_str = f"{office} {district}" if district else office
            n = upsert_race(conn, race, dry_run)
            candidates = race.get("candidates") or []
            print(f"    {label_str}: {len(candidates)} candidates" + (f", {n} written" if not dry_run else ""))
            total_races += 1
            total_candidates += len(candidates)
        time.sleep(2)  # brief pause between Gemini calls

    print(f"\nDone. {total_races} races, {total_candidates} candidates total.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest VPAP Virginia race data via Gemini.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set.", file=sys.stderr)
        return 1
    if not _GENAI_AVAILABLE:
        print("ERROR: google-genai not installed.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    setup_db(conn)
    ingest(conn, args.year, api_key, args.dry_run)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
