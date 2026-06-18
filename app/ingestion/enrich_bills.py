#!/usr/bin/env python3
"""
app/ingestion/enrich_bills.py

Enriches va_bills in polls.db with context from local OpenStates and LIS
databases. No API calls — reads from openstates_va.db and virginia_legislature.db
which are maintained by separate ingestion scripts.

Usage:
    python app/ingestion/enrich_bills.py               # all sources
    python app/ingestion/enrich_bills.py --source os   # OpenStates only
    python app/ingestion/enrich_bills.py --source lis  # LIS only

Coverage:
    OpenStates  — sponsors, result, openstates_url, latest_action/date,
                  description_full (2023 + 2025 only)  — sessions 2021-2026
    LIS         — patron_name, passed_house, passed_senate, chapter_id,
                  governor_action/date                  — session 2026 only

Join key: (bill_number, session) in va_bills  <->
          (bill_id,     session) in openstates bills
          (Bill_id,     implied 2026) in virginia_legislature bills
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

BASE_DIR       = Path(__file__).resolve().parent.parent.parent
POLLS_DB       = BASE_DIR / "polls.db"
OPENSTATES_DB  = BASE_DIR / "openstates_va.db"
LIS_DB         = BASE_DIR / "virginia_legislature.db"


# ── Schema helpers ─────────────────────────────────────────────────────────────

_OPENSTATES_COLS = {
    "sponsors":          "TEXT",   # pipe-delimited sponsor names
    "result":            "TEXT",   # pass / fail / unknown
    "openstates_url":    "TEXT",
    "latest_action":     "TEXT",
    "latest_action_date": "TEXT",
    "description_full":  "TEXT",   # rich prose description (2023 + 2025 only)
}

_LIS_COLS = {
    "patron_name":          "TEXT",  # primary patron surname from LIS
    "passed_house":         "TEXT",  # Y / N
    "passed_senate":        "TEXT",  # Y / N
    "chapter_id":           "TEXT",  # Acts of Assembly chapter, e.g. CHAP0350
    "governor_action":      "TEXT",
    "governor_action_date": "TEXT",
}


def _ensure_columns(conn: sqlite3.Connection, col_map: dict[str, str]) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(va_bills)")}
    for col, typedef in col_map.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE va_bills ADD COLUMN {col} {typedef}")
            print(f"  Added column: va_bills.{col} ({typedef})")
    conn.commit()


# ── OpenStates enrichment ──────────────────────────────────────────────────────

def enrich_from_openstates(polls: sqlite3.Connection) -> None:
    if not OPENSTATES_DB.exists():
        print(f"  SKIP: {OPENSTATES_DB} not found")
        return

    _ensure_columns(polls, _OPENSTATES_COLS)

    os_conn = sqlite3.connect(str(OPENSTATES_DB))
    os_conn.row_factory = sqlite3.Row

    # Build description lookup: (bill_id, session) -> description
    desc_lookup: dict[tuple[str, str], str] = {}
    for r in os_conn.execute("SELECT bill_id, session, description FROM bill_descriptions"):
        desc_lookup[(r["bill_id"], r["session"])] = r["description"] or ""

    updated = skipped = 0
    session_counts: dict[str, int] = {}

    for r in os_conn.execute(
        "SELECT bill_id, session, sponsors, result, openstates_url, "
        "       latest_action, latest_date "
        "FROM bills"
    ):
        bill_number = r["bill_id"]
        session     = r["session"]
        description_full = desc_lookup.get((bill_number, session), "")

        existing = polls.execute(
            "SELECT id FROM va_bills WHERE bill_number = ? AND session = ?",
            (bill_number, session),
        ).fetchone()

        if not existing:
            skipped += 1
            continue

        polls.execute(
            """UPDATE va_bills
                  SET sponsors          = ?,
                      result            = ?,
                      openstates_url    = ?,
                      latest_action     = ?,
                      latest_action_date = ?,
                      description_full  = ?
                WHERE id = ?""",
            (
                r["sponsors"] or "",
                r["result"] or "",
                r["openstates_url"] or "",
                r["latest_action"] or "",
                r["latest_date"] or "",
                description_full,
                existing["id"],
            ),
        )
        updated += 1
        session_counts[session] = session_counts.get(session, 0) + 1

    polls.commit()
    os_conn.close()

    print(f"  OpenStates: {updated} rows updated, {skipped} bill_ids not in va_bills")
    for sess in sorted(session_counts):
        print(f"    {sess}: {session_counts[sess]} updated")


# ── LIS enrichment ─────────────────────────────────────────────────────────────

def enrich_from_lis(polls: sqlite3.Connection) -> None:
    if not LIS_DB.exists():
        print(f"  SKIP: {LIS_DB} not found")
        return

    _ensure_columns(polls, _LIS_COLS)

    lis_conn = sqlite3.connect(str(LIS_DB))
    lis_conn.row_factory = sqlite3.Row

    # Check whether virginia_legislature.db has multi-session data (session column).
    lis_cols = {r[1] for r in lis_conn.execute("PRAGMA table_info(bills)")}
    has_session_col = "session" in lis_cols

    if has_session_col:
        query = (
            "SELECT Bill_id, session, Patron_name, Passed_house, Passed_senate, "
            "       Chapter_id, Last_governor_action, Last_governor_action_date "
            "FROM bills WHERE session IS NOT NULL"
        )
    else:
        # Legacy single-session DB — treat all rows as 2026
        query = (
            "SELECT Bill_id, '2026' AS session, Patron_name, Passed_house, Passed_senate, "
            "       Chapter_id, Last_governor_action, Last_governor_action_date "
            "FROM bills"
        )

    updated = skipped = 0
    session_counts: dict[str, int] = {}

    for r in lis_conn.execute(query):
        bill_number = (r["Bill_id"] or "").strip()
        session     = (r["session"] or "").strip()
        if not bill_number or not session:
            continue

        existing = polls.execute(
            "SELECT id FROM va_bills WHERE bill_number = ? AND session = ?",
            (bill_number, session),
        ).fetchone()

        if not existing:
            skipped += 1
            continue

        polls.execute(
            """UPDATE va_bills
                  SET patron_name          = ?,
                      passed_house         = ?,
                      passed_senate        = ?,
                      chapter_id           = ?,
                      governor_action      = ?,
                      governor_action_date = ?
                WHERE id = ?""",
            (
                r["Patron_name"] or "",
                r["Passed_house"] or "",
                r["Passed_senate"] or "",
                r["Chapter_id"] or "",
                r["Last_governor_action"] or "",
                r["Last_governor_action_date"] or "",
                existing["id"],
            ),
        )
        updated += 1
        session_counts[session] = session_counts.get(session, 0) + 1

    polls.commit()
    lis_conn.close()

    sessions_covered = sorted(session_counts)
    print(f"  LIS: {updated} rows updated, {skipped} bill_ids not in va_bills")
    for sess in sessions_covered:
        print(f"    {sess}: {session_counts[sess]} updated")


# ── Coverage report ────────────────────────────────────────────────────────────

def _coverage_report(polls: sqlite3.Connection) -> None:
    print("\n=== va_bills enrichment coverage ===")
    col_checks = [
        ("sponsors",          "OpenStates sponsors"),
        ("result",            "OpenStates result"),
        ("description_full",  "OpenStates description"),
        ("patron_name",       "LIS patron"),
        ("chapter_id",        "LIS chapter"),
    ]
    for col, label in col_checks:
        try:
            r = polls.execute(
                f"SELECT COUNT(*) total, "
                f"SUM(CASE WHEN {col} IS NOT NULL AND {col} != '' THEN 1 ELSE 0 END) filled "
                f"FROM va_bills"
            ).fetchone()
            pct = round(r[1] / r[0] * 100, 1) if r[0] else 0
            print(f"  {label:30s}: {r[1]:5d} / {r[0]:5d} ({pct}%)")
        except Exception:
            print(f"  {label:30s}: column missing")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich va_bills from OpenStates and LIS local databases."
    )
    parser.add_argument(
        "--source", choices=["os", "lis", "all"], default="all",
        help="Which source to run: os=OpenStates, lis=LIS, all=both (default: all)",
    )
    args = parser.parse_args()

    polls = sqlite3.connect(str(POLLS_DB))
    polls.row_factory = sqlite3.Row

    if args.source in ("os", "all"):
        print("OpenStates enrichment...")
        enrich_from_openstates(polls)

    if args.source in ("lis", "all"):
        print("LIS enrichment...")
        enrich_from_lis(polls)

    _coverage_report(polls)
    polls.close()


if __name__ == "__main__":
    main()
