#!/usr/bin/env python3
"""
Sync Virginia state legislators from openstates_va.db into polls.db (va_finance_people).

Pulls verified Delegate/Senator records from the OpenStates local DB and upserts
them into va_finance_people, replacing any Gemini-extracted guesses.

Run after build_openstates_db.py has completed a fresh pull.

Usage:
    python ingest_va_legislators.py
    python ingest_va_legislators.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLLS_DB  = BASE_DIR / "polls.db"
OS_DB     = BASE_DIR / "openstates_va.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def setup_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS va_finance_people (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name     TEXT NOT NULL,
            office          TEXT,
            district        TEXT,
            party           TEXT,
            role            TEXT,
            incumbent       INTEGER DEFAULT 0,
            committee_name  TEXT,
            finance_url     TEXT,
            source_url      TEXT,
            data_confidence TEXT,
            raw_json        TEXT,
            fetched_at      TEXT,
            UNIQUE(person_name, office, district, role)
        );
    """)
    conn.commit()


def sync(dry_run: bool = False) -> None:
    if not OS_DB.exists():
        print(f"ERROR: {OS_DB} not found. Run build_openstates_db.py first.", file=sys.stderr)
        sys.exit(1)

    os_conn = sqlite3.connect(OS_DB)
    legislators = os_conn.execute(
        "SELECT name, party, chamber, district "
        "FROM legislators "
        "WHERE chamber IN ('Delegate', 'Senator') "
        "ORDER BY chamber, CAST(district AS INTEGER)"
    ).fetchall()
    os_conn.close()

    print(f"Found {len(legislators)} legislators in openstates_va.db")

    if dry_run:
        for name, party, chamber, district in legislators:
            office = "House of Delegates" if chamber == "Delegate" else "State Senate"
            print(f"  [dry] {office} dist={district}  {name} ({party})")
        return

    conn = sqlite3.connect(POLLS_DB)
    setup_table(conn)

    # Clear old legislative rows so renamed/redistricted members don't linger
    deleted = conn.execute(
        "DELETE FROM va_finance_people WHERE office IN ('House of Delegates', 'State Senate')"
    ).rowcount
    print(f"  Cleared {deleted} old legislative rows")

    now = now_iso()
    inserted = 0
    for name, party, chamber, district in legislators:
        office = "House of Delegates" if chamber == "Delegate" else "State Senate"
        conn.execute(
            """INSERT INTO va_finance_people
                   (person_name, office, district, party, role, incumbent,
                    data_confidence, fetched_at)
               VALUES (?, ?, ?, ?, 'officeholder', 1, 'high', ?)
               ON CONFLICT(person_name, office, district, role) DO UPDATE SET
                   party=excluded.party,
                   data_confidence=excluded.data_confidence,
                   fetched_at=excluded.fetched_at""",
            (name, office, district, party, now),
        )
        inserted += 1

    conn.commit()

    totals = conn.execute(
        "SELECT office, COUNT(*) FROM va_finance_people GROUP BY office ORDER BY office"
    ).fetchall()
    conn.close()

    print(f"  Inserted/updated {inserted} legislators")
    for office, count in totals:
        print(f"    {office}: {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync VA state legislators from OpenStates into polls.db"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    sync(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
