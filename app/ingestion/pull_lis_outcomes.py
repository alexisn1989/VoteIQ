#!/usr/bin/env python3
"""
app/ingestion/pull_lis_outcomes.py

Pulls Virginia bill outcome data from the LIS API (GetLegislationListAsync)
for one or more regular sessions and stores results in virginia_legislature.db.
Then propagates into va_bills in polls.db via the same join used by enrich_bills.py.

Usage:
    python app/ingestion/pull_lis_outcomes.py                  # 2022-2026
    python app/ingestion/pull_lis_outcomes.py --sessions 2025  # one year
    python app/ingestion/pull_lis_outcomes.py --sessions 2024,2025,2026

Session codes: YYYYS where S=1 (regular), 2 (special I), etc.
    2026 regular = 20261
    2025 regular = 20251
    etc.

Fields sourced from GetLegislationListAsync:
    Bill_id, Bill_description, Patron_id, Patron_name,
    Passed_house, Passed_senate, Passed, Failed, Carried_over,
    Approved, Vetoed, Chapter_id, Introduction_date,
    Last_governor_action, session
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR    = Path(__file__).resolve().parent.parent.parent
LIS_DB      = BASE_DIR / "virginia_legislature.db"
POLLS_DB    = BASE_DIR / "polls.db"
LIS_API_KEY = os.getenv("LIS_API_KEY")
LIS_BASE    = "https://lis.virginia.gov"

# Regular session codes: year -> sessionCode
_SESSION_CODES: dict[str, str] = {
    "2022": "20221",
    "2023": "20231",
    "2024": "20241",
    "2025": "20251",
    "2026": "20261",
}

# LegislationStatus → (Passed, Failed, Carried_over, Approved, Vetoed, governor_action)
_STATUS_MAP: dict[str, tuple[str, str, str, str, str, str]] = {
    "Acts of Assembly Chapter": ("Y", "N", "N", "Y", "N", "Approved"),
    "Passed":                   ("Y", "N", "N", "N", "N", ""),
    "Governor's Veto":          ("N", "N", "N", "N", "Y", "Vetoed"),
    "Continued":                ("N", "N", "Y", "N", "N", ""),
    "Introduced":               ("N", "N", "N", "N", "N", ""),
}


# ── Schema ─────────────────────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(bills)")}

    if "session" not in existing:
        conn.execute("ALTER TABLE bills ADD COLUMN session TEXT")
        conn.execute("UPDATE bills SET session = '2026' WHERE session IS NULL")
        print("  Added bills.session column, backfilled existing rows as '2026'")

    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bills_bill_session "
            "ON bills(Bill_id, session)"
        )
    except sqlite3.OperationalError:
        pass  # index already exists

    conn.commit()


# ── LIS API call ───────────────────────────────────────────────────────────────

def _fetch_lis_bills(session_code: str) -> list[dict]:
    url  = f"{LIS_BASE}/AdvancedLegislationSearch/api/GetLegislationListAsync"
    body = json.dumps({"sessionCode": session_code}).encode()
    req  = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json; charset=utf-8",
        "WebAPIKey":    LIS_API_KEY or "",
        "User-Agent":  "VoteIQ/1.0",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    bills = raw.get("Legislations") if isinstance(raw, dict) else raw
    return bills or []


# ── Field extraction ───────────────────────────────────────────────────────────

def _parse_patrons(raw) -> tuple[str, str]:
    """Return (patron_id, patron_name) for the chief patron."""
    if isinstance(raw, str):
        try:
            patrons = ast.literal_eval(raw)
        except Exception:
            return "", ""
    else:
        patrons = raw or []

    chief = next((p for p in patrons if p.get("PatronTypeID") == 1), None)
    if not chief and patrons:
        chief = patrons[0]
    if not chief:
        return "", ""
    return (
        str(chief.get("MemberID") or ""),
        str(chief.get("PatronDisplayName") or chief.get("MemberDisplayName") or ""),
    )


def _yn(val) -> str:
    """Convert a date string (or None/'None') to Y/N."""
    if not val or str(val).strip().lower() in ("none", "null", ""):
        return "N"
    return "Y"


def _safe(val) -> str:
    v = str(val or "").strip()
    return "" if v.lower() in ("none", "null") else v


# ── Per-session pull ───────────────────────────────────────────────────────────

def pull_session(lis: sqlite3.Connection, year: str) -> int:
    session_code = _SESSION_CODES[year]
    print(f"  Fetching LIS session {session_code} ({year})...")
    bills = _fetch_lis_bills(session_code)
    print(f"  Got {len(bills)} bills from API")

    upserted = 0
    for b in bills:
        bill_id = _safe(b.get("LegislationNumber"))
        if not bill_id:
            continue

        status = _safe(b.get("LegislationStatus"))
        flags  = _STATUS_MAP.get(status, ("N", "N", "N", "N", "N", ""))
        passed, failed, carried_over, approved, vetoed, gov_action = flags

        passed_house   = _yn(b.get("HousePassageDate"))
        passed_senate  = _yn(b.get("SenatePassageDate"))
        chapter_id     = _safe(b.get("ChapterNumber"))
        intro_date     = _safe(b.get("IntroductionDate"))
        bill_desc      = _safe(b.get("Description"))
        patron_id, patron_name = _parse_patrons(b.get("Patrons"))

        lis.execute(
            """INSERT INTO bills
                   (Bill_id, Bill_description, Patron_id, Patron_name,
                    Passed_house, Passed_senate, Passed, Failed,
                    Carried_over, Approved, Vetoed,
                    Chapter_id, Introduction_date,
                    Last_governor_action, session)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(Bill_id, session) DO UPDATE SET
                   Bill_description   = excluded.Bill_description,
                   Patron_id          = excluded.Patron_id,
                   Patron_name        = excluded.Patron_name,
                   Passed_house       = excluded.Passed_house,
                   Passed_senate      = excluded.Passed_senate,
                   Passed             = excluded.Passed,
                   Failed             = excluded.Failed,
                   Carried_over       = excluded.Carried_over,
                   Approved           = excluded.Approved,
                   Vetoed             = excluded.Vetoed,
                   Chapter_id         = excluded.Chapter_id,
                   Introduction_date  = excluded.Introduction_date,
                   Last_governor_action = excluded.Last_governor_action""",
            (bill_id, bill_desc, patron_id, patron_name,
             passed_house, passed_senate, passed, failed,
             carried_over, approved, vetoed,
             chapter_id, intro_date, gov_action, year),
        )
        upserted += 1

    lis.commit()
    return upserted


# ── Propagate to va_bills ──────────────────────────────────────────────────────

def propagate_to_polls(years: list[str]) -> None:
    """Push LIS outcome fields into va_bills for the given sessions."""
    lis   = sqlite3.connect(str(LIS_DB))
    polls = sqlite3.connect(str(POLLS_DB))
    polls.row_factory = sqlite3.Row

    # Ensure va_bills has the LIS columns
    existing = {r[1] for r in polls.execute("PRAGMA table_info(va_bills)")}
    for col in ("patron_name", "passed_house", "passed_senate",
                "chapter_id", "governor_action", "governor_action_date",
                "passed", "failed", "carried_over", "approved", "vetoed"):
        if col not in existing:
            polls.execute(f"ALTER TABLE va_bills ADD COLUMN {col} TEXT")
    polls.commit()

    total_updated = 0
    for year in years:
        updated = 0
        for r in lis.execute(
            """SELECT Bill_id, Patron_name, Passed_house, Passed_senate,
                      Chapter_id, Last_governor_action,
                      Passed, Failed, Carried_over, Approved, Vetoed
               FROM bills WHERE session = ?""",
            (year,),
        ):
            existing_row = polls.execute(
                "SELECT id FROM va_bills WHERE bill_number = ? AND session = ?",
                (r[0], year),
            ).fetchone()
            if not existing_row:
                continue
            polls.execute(
                """UPDATE va_bills
                      SET patron_name        = ?,
                          passed_house       = ?,
                          passed_senate      = ?,
                          chapter_id         = ?,
                          governor_action    = ?,
                          passed             = ?,
                          failed             = ?,
                          carried_over       = ?,
                          approved           = ?,
                          vetoed             = ?
                    WHERE id = ?""",
                (r[1], r[2], r[3], r[4], r[5],
                 r[6], r[7], r[8], r[9], r[10],
                 existing_row["id"]),
            )
            updated += 1
        polls.commit()
        total_updated += updated
        print(f"  Propagated {updated} rows to va_bills for session {year}")

    lis.close()
    polls.close()
    print(f"  Total va_bills updated: {total_updated}")


# ── Coverage report ────────────────────────────────────────────────────────────

def _coverage(polls: sqlite3.Connection) -> None:
    print("\n=== va_bills outcome coverage ===")
    checks = [
        ("passed",       "Passed (LIS)"),
        ("approved",     "Approved/signed"),
        ("vetoed",       "Vetoed"),
        ("chapter_id",   "Chapter ID"),
        ("patron_name",  "Patron name"),
    ]
    for col, label in checks:
        try:
            r = polls.execute(
                f"SELECT COUNT(*) total, "
                f"SUM(CASE WHEN {col} IS NOT NULL AND {col} != '' AND {col} != 'N' THEN 1 ELSE 0 END) filled "
                f"FROM va_bills"
            ).fetchone()
            pct = round(r[1] / r[0] * 100, 1) if r[0] else 0
            print(f"  {label:30s}: {r[1]:5d} / {r[0]:5d} ({pct}%)")
        except Exception:
            print(f"  {label:30s}: column missing")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull LIS bill outcomes for multiple sessions into virginia_legislature.db."
    )
    parser.add_argument(
        "--sessions", default=",".join(_SESSION_CODES.keys()),
        help=f"Comma-separated years to pull (default: all {list(_SESSION_CODES.keys())})",
    )
    parser.add_argument(
        "--no-propagate", action="store_true",
        help="Skip propagation to va_bills in polls.db",
    )
    args = parser.parse_args()

    years = [y.strip() for y in args.sessions.split(",") if y.strip() in _SESSION_CODES]
    if not years:
        print(f"ERROR: no valid years in --sessions. Valid: {list(_SESSION_CODES.keys())}")
        return

    if not LIS_API_KEY:
        print("ERROR: LIS_API_KEY not set in environment.")
        return

    lis = sqlite3.connect(str(LIS_DB))
    _ensure_schema(lis)

    print(f"Pulling LIS outcomes for sessions: {years}")
    for year in years:
        n = pull_session(lis, year)
        print(f"  {year}: {n} rows upserted")

    lis.close()

    if not args.no_propagate:
        print("\nPropagating to va_bills...")
        propagate_to_polls(years)
        polls = sqlite3.connect(str(POLLS_DB))
        _coverage(polls)
        polls.close()


if __name__ == "__main__":
    main()
