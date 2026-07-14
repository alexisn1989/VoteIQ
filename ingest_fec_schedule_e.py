#!/usr/bin/env python3
"""
Fetch FEC Schedule E (independent expenditures) targeting Virginia federal
candidates and store in polls.db fec_independent_expenditures table.

Schedule E = outside spending by super PACs, 527s, dark-money groups —
FOR or AGAINST a candidate, not coordinated with the campaign.

Usage:
    python ingest_fec_schedule_e.py
    python ingest_fec_schedule_e.py --cycle 2024
    python ingest_fec_schedule_e.py --candidate H2VA02064   # Kiggans only
    python ingest_fec_schedule_e.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# DATA_DIR-aware, matching ingest_fec_2026.py / build_federal_vote_alignment.py /
# ingest_party_breakdown.py. Without this, the script silently wrote to (and
# compared its sanity-check baseline against) an ephemeral copy of polls.db in
# the deployed source checkout, not the persistent /var/data disk — every
# production run was writing data nobody ever read, then aborting against a
# stale baseline stamped from an earlier accidental run against that same
# wrong path.
DB_PATH  = Path(os.getenv("DATA_DIR", str(BASE_DIR))) / "polls.db"

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

FEC_BASE = "https://api.open.fec.gov/v1"
HEADERS  = {"User-Agent": "VoteIQ/1.0 (Virginia civic data; alexisnieuwenhuys89@gmail.com)"}

# VA federal delegation: bioguide_id -> (display_name, fec_candidate_id)
# Canonical copy lives in voteiq/config/districts.py
from voteiq.config.districts import VA_MEMBERS


def setup_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fec_independent_expenditures (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id         TEXT,
            candidate_name      TEXT,
            fec_candidate_id    TEXT,
            committee_id        TEXT,
            committee_name      TEXT,
            support_oppose      TEXT,
            expenditure_amount  REAL,
            expenditure_date    TEXT,
            election_type       TEXT,
            cycle               INTEGER,
            office              TEXT,
            state               TEXT,
            district            TEXT,
            purpose             TEXT,
            payee_name          TEXT,
            filing_form         TEXT,
            transaction_id      TEXT UNIQUE,
            fetched_at          TEXT
        )
    """)
    conn.commit()


def _fec_get(path: str, params: dict, api_key: str, timeout: int = 20) -> dict | None:
    params = dict(params)
    params["api_key"] = api_key
    url = f"{FEC_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(4):
        try:
            data = urllib.request.urlopen(req, timeout=timeout).read()
            return json.loads(data)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 20 * (attempt + 1)
                print(f"  Rate limited — waiting {wait}s (attempt {attempt+1}/4)", flush=True)
                time.sleep(wait)
            else:
                print(f"  FEC HTTP error {exc.code}: {exc}", flush=True)
                return None
        except Exception as exc:
            print(f"  FEC request failed: {exc}", flush=True)
            return None
    return None


def fetch_schedule_e(
    fec_candidate_id: str,
    cycle: int,
    api_key: str,
    max_pages: int = 20,
) -> list[dict]:
    """Paginate through Schedule E for one candidate."""
    rows: list[dict] = []
    last_index = None
    last_expenditure_date = None

    for page in range(max_pages):
        params: dict = {
            "candidate_id":  fec_candidate_id,
            "cycle":         cycle,
            "per_page":      100,
            "sort":          "-expenditure_date",
            "sort_hide_null": "true",
            "is_notice":     "false",
        }
        if last_index and last_expenditure_date:
            params["last_index"] = last_index
            params["last_expenditure_date"] = last_expenditure_date

        data = _fec_get("/schedules/schedule_e/", params, api_key)
        if not data:
            break

        results = data.get("results", [])
        if not results:
            break

        rows.extend(results)
        print(f"    page {page+1}: {len(results)} rows (total so far: {len(rows)})", flush=True)

        pagination = data.get("pagination", {})
        if not pagination.get("last_indexes"):
            break
        last_index = pagination["last_indexes"].get("last_index")
        last_expenditure_date = pagination["last_indexes"].get("last_expenditure_date")
        if not last_index:
            break

        time.sleep(0.5)

    return rows


def upsert_rows(conn: sqlite3.Connection, bioguide_id: str, rows: list[dict], cycle: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for r in rows:
        txn_id = r.get("transaction_id") or r.get("sub_id") or ""
        if not txn_id:
            continue
        try:
            conn.execute("""
                INSERT INTO fec_independent_expenditures (
                    bioguide_id, candidate_name, fec_candidate_id,
                    committee_id, committee_name, support_oppose,
                    expenditure_amount, expenditure_date, election_type,
                    cycle, office, state, district, purpose, payee_name,
                    filing_form, transaction_id, fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    expenditure_amount = excluded.expenditure_amount,
                    fetched_at         = excluded.fetched_at
            """, (
                bioguide_id,
                r.get("candidate_name") or "",
                r.get("candidate_id") or "",
                r.get("committee", {}).get("committee_id") or r.get("committee_id") or "",
                r.get("committee", {}).get("name") or r.get("committee_name") or "",
                r.get("support_oppose_indicator") or "",
                r.get("expenditure_amount") or 0.0,
                (r.get("expenditure_date") or "")[:10],
                r.get("election_type") or "",
                cycle,
                r.get("candidate_office") or "",
                r.get("candidate_office_state") or "",
                r.get("candidate_office_district") or "",
                r.get("expenditure_description") or "",
                r.get("payee_name") or "",
                r.get("filing_form") or "",
                txn_id,
                now,
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch FEC Schedule E IE spending for VA candidates.")
    parser.add_argument("--cycle", type=int, default=2024, help="Election cycle (default: 2024)")
    parser.add_argument("--candidate", help="Single FEC candidate ID (e.g. H2VA02064)")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.getenv("FEC_API_KEY")
    if not api_key:
        print("ERROR: FEC_API_KEY not set in .env")
        return 1

    conn = sqlite3.connect(args.db)
    setup_db(conn)

    targets: list[tuple[str, str, str]] = []
    if args.candidate:
        match = [(bgid, name, fec_id) for bgid, (name, fec_id) in VA_MEMBERS.items() if fec_id == args.candidate]
        if not match:
            targets = [("unknown", args.candidate, args.candidate)]
        else:
            targets = match
    else:
        targets = [(bgid, name, fec_id) for bgid, (name, fec_id) in VA_MEMBERS.items()]

    total_inserted = 0
    for bioguide_id, name, fec_candidate_id in targets:
        print(f"\n[{name}] {fec_candidate_id} — cycle {args.cycle}", flush=True)
        rows = fetch_schedule_e(fec_candidate_id, args.cycle, api_key, max_pages=args.max_pages)
        print(f"  fetched {len(rows)} expenditures", flush=True)

        if args.dry_run:
            for r in rows[:3]:
                so = r.get("support_oppose_indicator", "?")
                amt = r.get("expenditure_amount", 0)
                cmte = (r.get("committee", {}) or {}).get("name") or r.get("committee_name", "?")
                date = (r.get("expenditure_date") or "")[:10]
                print(f"    {so} ${amt:,.0f}  {cmte}  ({date})", flush=True)
            continue

        n = upsert_rows(conn, bioguide_id, rows, args.cycle)
        total_inserted += n
        print(f"  upserted {n} rows", flush=True)
        time.sleep(1)

    if args.dry_run:
        conn.close()
        print("\n[dry-run] no writes")
    else:
        from voteiq.services.data_meta import check_count, stamp
        n = conn.execute("SELECT COUNT(*) FROM fec_independent_expenditures").fetchone()[0]
        check_count("fec_independent_expenditures", n)
        conn.close()
        stamp("fec_independent_expenditures", row_count=n, source_url="https://fec.gov/")
        print(f"\n[done] {total_inserted} total rows upserted into fec_independent_expenditures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
