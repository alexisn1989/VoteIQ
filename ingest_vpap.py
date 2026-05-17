#!/usr/bin/env python3
"""
Ingest Virginia 2026 race and campaign finance data from VPAP via Gemini search grounding.

Gemini searches vpap.org using Google Search grounding and returns structured
candidate/finance data which is stored in polls.db (vpap_races + vpap_candidates tables).

Examples:
    python ingest_vpap.py
    python ingest_vpap.py --dry-run
    python ingest_vpap.py --fec-only
    python ingest_vpap.py --year 2026
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
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
FEC_API_BASE = "https://api.open.fec.gov/v1"

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

CANDIDATE_FINANCE_PROMPT = """Search vpap.org and fec.gov for {name}'s campaign finance for the {year} election cycle in Virginia {race}.
Return ONLY valid JSON:
{{
  "name": "{name}",
  "party": "{party}",
  "incumbent": {incumbent},
  "money_raised": integer or null,
  "money_spent": integer or null,
  "cash_on_hand": integer or null,
  "vpap_url": "full URL or null",
  "data_confidence": "high / medium / low"
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
    for col, typedef in [("fec_candidate_id", "TEXT"), ("data_source", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE vpap_candidates ADD COLUMN {col} {typedef}")
            conn.commit()
        except sqlite3.OperationalError:
            pass


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


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def canonical_candidate_name(conn: sqlite3.Connection, key: str, name: str) -> str:
    """Resolve single-word Gemini names like 'Luria' to existing full names in a race."""
    cleaned = str(name or "").strip()
    parts = _norm_name(cleaned).split()
    if len(parts) != 1:
        return cleaned
    token = parts[0]
    rows = conn.execute(
        "SELECT name FROM vpap_candidates WHERE race_key = ?", (key,)
    ).fetchall()
    matches = [r[0] for r in rows if token in _norm_name(r[0]).split() and " " in r[0]]
    return matches[0] if len(matches) == 1 else cleaned


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
        name = canonical_candidate_name(conn, key, c.get("name") or "")
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


def gemini_fetch_candidate(name: str, party: str, incumbent: bool, race_label: str,
                            year: int, api_key: str) -> dict:
    client = _genai.Client(api_key=api_key)
    prompt = CANDIDATE_FINANCE_PROMPT.format(
        name=name, party=party,
        incumbent="true" if incumbent else "false",
        race=race_label, year=year,
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=_gtypes.GenerateContentConfig(
            tools=[_gtypes.Tool(google_search=_gtypes.GoogleSearch())]
        ),
    )
    return _parse_json(response.text)


def backfill_missing_finance(conn: sqlite3.Connection, year: int, api_key: str,
                              dry_run: bool) -> None:
    """Query Gemini individually for every candidate still showing $0 finance."""
    rows = conn.execute(
        """SELECT c.rowid, c.race_key, c.name, c.party, c.incumbent
           FROM vpap_candidates c
           WHERE (c.money_raised IS NULL OR c.money_raised = 0)
             AND c.race_key LIKE ?
           ORDER BY c.race_key, c.name""",
        (f"{year}_%",),
    ).fetchall()

    if not rows:
        print("  No candidates missing finance data.")
        return

    print(f"\nBackfilling finance for {len(rows)} candidates with $0...")
    updated = 0
    for rowid, rkey, name, party, incumbent in rows:
        race_label = rkey.replace(f"{year}_", "").replace("_", " ")
        print(f"  {name} ({party}) - {race_label}", end=" ... ", flush=True)

        if dry_run:
            print("skipped (dry-run)")
            continue

        try:
            data = gemini_fetch_candidate(name, party or "?", bool(incumbent),
                                          race_label, year, api_key)
            raised = data.get("money_raised")
            spent = data.get("money_spent")
            coh = data.get("cash_on_hand")
            confidence = data.get("data_confidence", "?")

            if raised or coh:
                conn.execute(
                    """UPDATE vpap_candidates
                       SET money_raised=?, money_spent=?, cash_on_hand=?,
                           vpap_url=COALESCE(?, vpap_url), fetched_at=?
                       WHERE rowid=?""",
                    (raised, spent, coh, data.get("vpap_url"), now_iso(), rowid),
                )
                conn.commit()
                updated += 1
                print(f"raised=${raised:,}" if raised else f"cash=${coh:,}", f"[{confidence}]")
            else:
                print(f"no data found [{confidence}]")
        except Exception as exc:
            print(f"error: {exc}")

        time.sleep(1.5)

    print(f"  Backfill done. Updated {updated} of {len(rows)} candidates.")


# ── FEC integration ───────────────────────────────────────────────────────────

def _fec_normalize(name: str) -> str:
    """Convert FEC name 'WITTMAN, ROBERT J. MR.' to comparable lowercase 'robert wittman'."""
    name = name.strip().upper()
    for suffix in (" MR.", " MRS.", " MS.", " DR.", " JR.", " SR.", " II", " III", " IV", " ESQ."):
        name = name.replace(suffix, "")
    if "," in name:
        last, rest = name.split(",", 1)
        first = rest.strip().split()[0] if rest.strip() else ""
        name = f"{first} {last.strip()}"
    return " ".join(name.lower().split())


def _best_fec_match(fec_name: str, vpap_names: list[str], threshold: float = 0.72) -> str | None:
    fec_norm = _fec_normalize(fec_name)
    best_ratio, best_name = 0.0, None
    for vname in vpap_names:
        ratio = difflib.SequenceMatcher(None, fec_norm, _fec_normalize(vname)).ratio()
        if ratio > best_ratio:
            best_ratio, best_name = ratio, vname
    return best_name if best_ratio >= threshold else None


def fec_fetch_va(year: int, api_key: str = "DEMO_KEY") -> list[dict]:
    results = []
    for office in ("S", "H"):
        page = 1
        while True:
            qs = urllib.parse.urlencode({
                "state": "VA", "cycle": year, "office": office,
                "api_key": api_key, "per_page": 100, "page": page,
            })
            url = f"{FEC_API_BASE}/candidates/totals/?{qs}"
            try:
                with urllib.request.urlopen(url, timeout=20) as resp:
                    data = json.loads(resp.read())
            except Exception as exc:
                print(f"  FEC fetch error (office={office}, page={page}): {exc}")
                break
            results.extend(data.get("results", []))
            pagination = data.get("pagination", {})
            if page >= pagination.get("pages", 1):
                break
            page += 1
            time.sleep(0.5)
    return results


def ingest_fec(conn: sqlite3.Connection, year: int, dry_run: bool,
               fec_api_key: str = "DEMO_KEY") -> None:
    """Overwrite vpap_candidates finance with authoritative FEC data."""
    print("\nFetching FEC data for Virginia...")
    fec_candidates = fec_fetch_va(year, fec_api_key)
    print(f"  Got {len(fec_candidates)} FEC candidates")

    vpap_rows = conn.execute(
        "SELECT name FROM vpap_candidates WHERE race_key LIKE ?", (f"{year}_%",)
    ).fetchall()
    vpap_names = [r[0] for r in vpap_rows]

    updated = skipped = 0
    for fc in fec_candidates:
        fec_name = fc.get("name", "")
        raised = fc.get("receipts")
        spent = fc.get("disbursements")
        coh_raw = fc.get("cash_on_hand_end_period")
        coh = float(coh_raw) if coh_raw else None
        fec_id = fc.get("candidate_id")

        match = _best_fec_match(fec_name, vpap_names)
        if not match:
            skipped += 1
            continue

        if dry_run:
            label = f"raised=${int(raised):,}" if raised else "no finance"
            print(f"  [dry] {fec_name} -> {match}: {label}")
            continue

        conn.execute(
            """UPDATE vpap_candidates
               SET money_raised=?, money_spent=?, cash_on_hand=?,
                   fec_candidate_id=?, data_source='fec', fetched_at=?
               WHERE name=? AND race_key LIKE ?""",
            (int(raised) if raised else None,
             int(spent) if spent else None,
             int(coh) if coh else None,
             fec_id, now_iso(), match, f"{year}_%"),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0]
        if changed:
            updated += 1
            label = f"raised=${int(raised):,}" if raised else "no finance"
            print(f"  [FEC] {match}: {label}")

    if not dry_run:
        conn.commit()
    print(f"  FEC pass done. Updated {updated}, no match for {skipped} FEC candidates.")


# ── Main ingest flow ──────────────────────────────────────────────────────────

def ingest(conn: sqlite3.Connection, year: int, api_key: str, dry_run: bool,
           skip_backfill: bool = False, fec_api_key: str = "DEMO_KEY",
           skip_fec: bool = False) -> None:
    total_races = total_candidates = 0
    for label, query in VPAP_QUERIES:
        print(f"\nQuerying Gemini ({label})...")
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
        time.sleep(2)

    print(f"\nBulk pass done. {total_races} races, {total_candidates} candidates.")

    if not skip_backfill:
        backfill_missing_finance(conn, year, api_key, dry_run)

    if not skip_fec:
        ingest_fec(conn, year, dry_run, fec_api_key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest VPAP Virginia race data via Gemini.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-backfill", action="store_true", help="Skip per-candidate Gemini finance backfill")
    parser.add_argument("--skip-fec", action="store_true", help="Skip FEC authoritative finance pass")
    parser.add_argument("--backfill-only", action="store_true", help="Only run Gemini backfill, skip bulk queries")
    parser.add_argument("--fec-only", action="store_true", help="Only run FEC finance pass")
    parser.add_argument("--fec-api-key", default=os.getenv("FEC_API_KEY", "DEMO_KEY"),
                        help="FEC API key (default: FEC_API_KEY env var or DEMO_KEY)")
    args = parser.parse_args(argv)

    api_key = os.getenv("GEMINI_API_KEY", "")

    conn = sqlite3.connect(args.db)
    setup_db(conn)

    if args.fec_only:
        ingest_fec(conn, args.year, args.dry_run, args.fec_api_key)
    elif args.backfill_only:
        if not api_key:
            print("ERROR: GEMINI_API_KEY not set.", file=sys.stderr)
            conn.close()
            return 1
        if not _GENAI_AVAILABLE:
            print("ERROR: google-genai not installed.", file=sys.stderr)
            conn.close()
            return 1
        backfill_missing_finance(conn, args.year, api_key, args.dry_run)
    else:
        if not api_key:
            print("ERROR: GEMINI_API_KEY not set.", file=sys.stderr)
            conn.close()
            return 1
        if not _GENAI_AVAILABLE:
            print("ERROR: google-genai not installed.", file=sys.stderr)
            conn.close()
            return 1
        ingest(conn, args.year, api_key, args.dry_run,
               skip_backfill=args.skip_backfill,
               fec_api_key=args.fec_api_key,
               skip_fec=args.skip_fec)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
